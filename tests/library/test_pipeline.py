"""oMLX client tests (no network) and analysis pipeline integration."""

import json
from pathlib import Path

import pytest

from library.config import LibraryConfig
from library.db import Library
from library.omlx_client import (
    OmlxClient,
    OmlxError,
    extract_json,
    normalize_base_url,
)
from library.pipeline import AnalysisPipeline, transcription_windows


# -- omlx_client -------------------------------------------------------------


def test_normalize_base_url():
    assert normalize_base_url("http://x:8989") == "http://x:8989/v1"
    assert normalize_base_url("http://x:8989/") == "http://x:8989/v1"
    assert normalize_base_url("http://x:8989/v1") == "http://x:8989/v1"
    assert normalize_base_url("http://x:8989/v1/") == "http://x:8989/v1"


def test_extract_json_variants():
    obj = {"title": "t", "tags": ["a"]}
    assert extract_json(json.dumps(obj)) == obj
    assert extract_json("```json\n" + json.dumps(obj) + "\n```") == obj
    assert extract_json(f"结果如下：\n{json.dumps(obj)}\n完毕") == obj
    with pytest.raises(OmlxError):
        extract_json("没有任何 JSON")


@pytest.mark.parametrize("duration", [0, 120, 300, 301, 400, 3295, 7200])
def test_transcription_windows_respect_budget(duration):
    windows = transcription_windows(duration, 300)
    assert sum(span for _, span in windows) <= min(duration, 300)
    for start, span in windows:
        assert start >= 0 and span > 0 and start + span <= duration
    for (start, span), (next_start, _) in zip(windows, windows[1:]):
        assert start + span < next_start
    if 0 < duration <= 300:
        assert windows == [(0.0, duration)]


def test_transcription_long_video_samples_without_tail():
    assert transcription_windows(3600, 300) == [
        (0.0, 120.0),
        (1200.0, 90.0),
        (1800.0, 90.0),
    ]


def test_sampled_transcription_uses_original_timestamps(plib, monkeypatch, tmp_path):
    lib, cfg = plib
    calls = []
    monkeypatch.setattr(
        "library.pipeline.media.extract_audio",
        lambda *args, **kwargs: calls.append(kwargs),
    )
    fake = FakeOmlx()
    pipeline = AnalysisPipeline(lib, cfg, fake)
    data = pipeline._stage_transcribe(
        tmp_path,
        Path("video.mp4"),
        {"duration_s": 3600},
        {"wav": "audio.wav", "speech_ratio": 1.0},
    )
    assert fake.transcribe_calls == 3
    assert data["sampled"] is True
    assert [(s["start_s"], s["end_s"]) for s in data["segments"]] == [
        (0, 120),
        (1200, 1290),
        (1800, 1890),
    ]
    assert sum(c["duration_s"] for c in calls) == 300
    pipeline._stage_transcribe(
        tmp_path,
        Path("video.mp4"),
        {"duration_s": 3600},
        {"wav": "audio.wav", "speech_ratio": 1.0},
    )
    assert fake.transcribe_calls == 3


class _FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("err", request=None, response=None)


def test_transcribe_handles_text_and_json_shapes(monkeypatch):
    client = OmlxClient("http://localhost:8989/v1", "k", max_retries=1)

    def fake_request(method, url, **kw):
        assert url == "/audio/transcriptions"
        assert kw["data"]["language"] == "zh"
        return _FakeResponse('{"text": "你好"}')

    monkeypatch.setattr(client, "_request", fake_request)
    audio = Path("/dev/null")
    assert client.transcribe(audio, "asr") == "你好"

    monkeypatch.setattr(
        client,
        "_request",
        lambda *a, **k: _FakeResponse("纯文本转写"),
    )
    assert client.transcribe(audio, "asr") == "纯文本转写"


# -- pipeline ----------------------------------------------------------------


def test_chat_disables_thinking(monkeypatch):
    client = OmlxClient("http://localhost:8989/v1", "k", max_retries=1)

    def fake_request(method, url, **kw):
        assert method == "POST" and url == "/chat/completions"
        assert kw["json"]["chat_template_kwargs"] == {"enable_thinking": False}
        return _FakeResponse('{"choices": [{"message": {"content": "{}"}}]}')

    monkeypatch.setattr(client, "_request", fake_request)
    try:
        assert client.chat("vision", [{"role": "user", "content": "test"}]) == "{}"
    finally:
        client.close()


ANALYSIS_JSON = {
    "title": "水彩人物示范",
    "summary": "画家示范水彩人物头像，重点讲解肤色调配与湿画法过渡。",
    "category": "绘画",
    "topic": "水彩人物",
    "form": "教程",
    "medium": "水彩",
    "tools": ["水彩纸", "貂毛笔"],
    "techniques": ["湿画法", "肤色调配"],
    "tags": ["水彩", "人物", "入门"],
    "artists": [
        {
            "name": "李画家",
            "role": "author",
            "evidence": "语音自述",
            "confidence": "high",
        }
    ],
    "source_accounts": [
        {"name": "绘画视频号", "evidence": "水印", "confidence": "medium"}
    ],
    "representative_frames": [{"index": 0, "reason": "过程画面"}],
}


class FakeOmlx:
    """Records calls; returns canned transcription + analysis JSON."""

    def __init__(self):
        self.transcribe_calls = 0
        self.chat_calls = 0
        self.prompts: list = []

    def transcribe(self, audio, model, language="zh"):
        self.transcribe_calls += 1
        return "今天我们画一个水彩人物头像，先调肤色。"

    def chat_vision(self, model, system, prompt, images):
        self.chat_calls += 1
        self.prompts.append(prompt)
        assert images, "frames must be attached"
        return "```json\n" + json.dumps(ANALYSIS_JSON) + "\n```"


@pytest.fixture()
def plib(tmp_path, tiny_video):
    cfg = LibraryConfig(data_dir=tmp_path / "data")
    lib = Library(cfg.db_path)
    folder = tiny_video.parent
    fid = lib.add_folder(str(folder))
    from library.scanner import scan_folder

    scan_folder(lib, fid, str(folder), cfg)
    return lib, cfg


def test_pipeline_end_to_end(plib):
    lib, cfg = plib
    fake = FakeOmlx()
    pipeline = AnalysisPipeline(lib, cfg, fake)
    video = lib.list_videos()[0]
    cid = video["content_id"]

    result = pipeline.analyze_content(cid, task_id=None)
    assert result["title"] == "水彩人物示范"
    assert "水彩人物头像" in fake.prompts[0]  # transcript fed to the model

    video = lib.get_video(cid)
    assert video["category"] == "绘画"
    assert video["analysis_status"] == "done"
    assert json.loads(video["ai_tags"]) == ["水彩", "人物", "入门"]
    assert video["duration_s"] == pytest.approx(2.0, abs=0.5)
    assert video["container"].startswith("mov")

    transcripts = lib.get_transcripts(cid)
    assert transcripts and "水彩" in transcripts[0]["text"]
    assert transcripts[0]["start_s"] == 0.0

    shots = lib.get_screenshots(cid)
    assert 1 <= len(shots) <= 2
    assert all(Path(s["path"]).exists() for s in shots)

    people = lib.video_people(cid)
    kinds = {(p["kind"], p["role"]) for p in people}
    assert ("painter", "author") in kinds
    assert ("source_account", "reposting_account") in kinds

    hits = lib.search_videos("湿画法")
    assert cid in [h["content_id"] for h in hits]


def test_pipeline_uses_cache_on_rerun(plib):
    lib, cfg = plib
    fake = FakeOmlx()
    pipeline = AnalysisPipeline(lib, cfg, fake)
    cid = lib.list_videos()[0]["content_id"]
    pipeline.analyze_content(cid)
    assert fake.transcribe_calls == 1 and fake.chat_calls == 1

    pipeline.analyze_content(cid)  # all stages cached
    assert fake.transcribe_calls == 1 and fake.chat_calls == 1
    video = lib.get_video(cid)
    assert video["title"] == ANALYSIS_JSON["title"]
    assert video["summary"] == ANALYSIS_JSON["summary"]
    assert video["category"] == ANALYSIS_JSON["category"]
    assert pipeline.analyze_content(cid) == ANALYSIS_JSON


def test_invalid_analysis_cache_is_regenerated(plib):
    lib, cfg = plib
    fake = FakeOmlx()
    pipeline = AnalysisPipeline(lib, cfg, fake)
    cid = lib.list_videos()[0]["content_id"]
    pipeline.analyze_content(cid)
    cache_dir = pipeline._cache_dir(lib.get_video(cid)["sha256"])
    from library.pipeline import ANALYSIS_PROMPT_VERSION

    pipeline._save_stage(cache_dir, "analyze", {"result": {}}, version=ANALYSIS_PROMPT_VERSION)
    result = pipeline.analyze_content(cid)
    assert fake.chat_calls == 2
    assert result == ANALYSIS_JSON
    assert lib.get_video(cid)["title"] == ANALYSIS_JSON["title"]


def test_finalize_rejects_cache_wrapper_without_erasing_metadata(plib):
    lib, cfg = plib
    pipeline = AnalysisPipeline(lib, cfg, FakeOmlx())
    cid = lib.list_videos()[0]["content_id"]
    pipeline.analyze_content(cid)
    before = lib.get_video(cid)
    with pytest.raises(OmlxError, match="no analysis content"):
        pipeline._stage_finalize(cfg.analysis_cache_dir, cid, {"frames": []}, {"result": ANALYSIS_JSON})
    assert lib.get_video(cid)["title"] == before["title"]
    assert lib.get_video(cid)["summary"] == before["summary"]


def test_reanalysis_keeps_user_set_painter(plib, tmp_path):
    """User sets the painter → re-analysis must neither add AI painters
    beside it nor downgrade the user link (regression: video 37)."""
    lib, cfg = plib
    pipeline = AnalysisPipeline(lib, cfg, FakeOmlx())
    cid = lib.list_videos()[0]["content_id"]
    pipeline.analyze_content(cid)  # first pass: AI links 李画家 etc.

    person_id = lib.upsert_person("painter", "一堂", confirmed_status="confirmed")
    lib.set_video_artist(cid, person_id)  # user replaces painter

    # re-run finalize from scratch (bust the analyze cache)
    sha = lib.get_video(cid)["sha256"]
    cache_dir = cfg.analysis_cache_dir / sha[:2] / sha
    (cache_dir / "analyze.json").unlink()
    pipeline.analyze_content(cid)

    people = lib.video_people(cid)
    painters = [p for p in people if p["kind"] == "painter"]
    assert len(painters) == 1
    assert painters[0]["canonical_name"] == "一堂"
    assert painters[0]["source"] == "user"
    assert painters[0]["confirmed_status"] == "confirmed"
    # source accounts are still recorded on re-analysis
    assert any(p["kind"] == "source_account" for p in people)


def test_pipeline_no_speech_fallback(plib, monkeypatch):
    lib, cfg = plib
    from library import media as media_mod

    monkeypatch.setattr(media_mod, "detect_speech_ratio", lambda wav: 0.0)
    fake = FakeOmlx()
    pipeline = AnalysisPipeline(lib, cfg, fake)
    cid = lib.list_videos()[0]["content_id"]
    pipeline.analyze_content(cid)
    video = lib.get_video(cid)
    assert video["transcript_status"] == "no_speech"
    assert video["analysis_status"] == "done"  # visual fallback still done
    assert fake.transcribe_calls == 0
    # summary still produced from frames only
    assert video["summary"]


def test_pipeline_transcribe_service_error_falls_back(plib):
    lib, cfg = plib
    from library.omlx_client import OmlxError

    class BrokenAsr(FakeOmlx):
        def transcribe(self, audio, model, language="zh"):
            self.transcribe_calls += 1
            raise OmlxError("404 model not found")

    fake = BrokenAsr()
    pipeline = AnalysisPipeline(lib, cfg, fake)
    cid = lib.list_videos()[0]["content_id"]
    pipeline.analyze_content(cid)  # must not raise
    video = lib.get_video(cid)
    assert video["transcript_status"] == "failed"
    assert video["analysis_status"] == "done"
    assert video["summary"]  # visual analysis still produced
    assert fake.chat_calls == 1


def test_pipeline_rejects_error_json_result(plib):
    """Model replying with a JSON-formatted error must fail the stage,
    never be cached as a successful (empty) analysis."""
    lib, cfg = plib
    from library.omlx_client import OmlxError

    class ErrorJsonModel(FakeOmlx):
        def chat_vision(self, model, system, prompt, images):
            self.chat_calls += 1
            self.prompts.append(prompt)
            if self.chat_calls < 3:
                return "我认为这个视频内容复杂，无法直接给出结论。"  # prose
            # attempt 3: valid JSON but error-shaped, no analysis content
            return '{"error": "未检测到有效的 JSON 结构输出", "status": "failed"}'

    fake = ErrorJsonModel()
    pipeline = AnalysisPipeline(lib, cfg, fake)
    cid = lib.list_videos()[0]["content_id"]
    with pytest.raises(OmlxError, match="no analysis content"):
        pipeline.analyze_content(cid)
    video = lib.get_video(cid)
    # worker marks failed on exception; here the stage just raised
    assert video["analysis_status"] == "analyzing"
    assert video["ai_title"] is None
    # bad result must not be cached as a successful analysis
    sha = video["sha256"]
    assert not (cfg.analysis_cache_dir / sha[:2] / sha / "analyze.json").exists()
