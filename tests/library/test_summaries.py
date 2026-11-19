"""通用视频总结：存储、管线与渲染测试。"""

import json
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from library.config import LibraryConfig
from library.summary_pipeline import SummaryPipeline, dump, render_notes


@pytest.fixture
def summaries(tmp_path):
    cfg = LibraryConfig(data_dir=tmp_path / "data")
    return SummaryPipeline(cfg, client=None)


def test_create_persists_queued_record(summaries, tiny_video):
    item = summaries.create(tiny_video, include_frames=True)
    assert item["status"] == "queued"
    assert item["title"] == "clip"
    assert item["include_frames"] is True
    stored = summaries.read(item["id"])
    assert stored["path"] == str(Path(tiny_video).resolve())
    assert (summaries.folder(item["id"]) / "summary.json").is_file()


def test_create_rejects_missing_or_unsupported_path(summaries, tmp_path):
    with pytest.raises(ValueError):
        summaries.create(tmp_path / "nope.mp4")
    with pytest.raises(ValueError):
        summaries.create(tmp_path / "note.txt")


def test_read_unknown_raises_file_not_found(summaries):
    with pytest.raises(FileNotFoundError):
        summaries.read("0" * 32)


def test_list_items_sorted_by_created_desc(summaries, tiny_video):
    first = summaries.create(tiny_video)
    second = summaries.create(tiny_video)
    ids = [item["id"] for item in summaries.list_items()]
    assert ids.index(second["id"]) < ids.index(first["id"])


def test_delete_removes_folder(summaries, tiny_video):
    item = summaries.create(tiny_video)
    summaries.delete(item["id"])
    assert not summaries.folder(item["id"]).exists()
    with pytest.raises(FileNotFoundError):
        summaries.read(item["id"])


# ---- 管线（mock 客户端） ---------------------------------------------------


def notes_reply():
    return {
        "topic": "视频教程主题",
        "chapters": [
            {
                "title": "开篇",
                "start_s": 0.0,
                "end_s": 1.5,
                "points": ["要点一", "步骤：点击开始"],
            }
        ],
        "takeaways": ["回顾要点"],
    }


def chat_flows(polished="润色后的通顺全文。", notes=None):
    """三次文本调用的默认序列：润色 → 章节草稿 → 汇总。"""
    notes = notes if notes is not None else notes_reply()
    draft = {"chapters": notes["chapters"]}
    return [
        polished,
        json.dumps(draft, ensure_ascii=False),
        json.dumps(notes, ensure_ascii=False),
    ]


@pytest.fixture
def summary(tmp_path, tiny_video):
    cfg = LibraryConfig(data_dir=tmp_path / "data")
    client = Mock()
    client.transcribe.return_value = "这里没有坏。"
    client.chat.side_effect = chat_flows()
    pipe = SummaryPipeline(cfg, client)
    return pipe, pipe.create(tiny_video), client


def test_run_produces_polished_transcript_and_notes(summary):
    pipe, item, client = summary
    pipe.run(item["id"])
    done = pipe.read(item["id"])
    assert done["status"] == "done"
    folder = pipe.folder(item["id"])
    polished = (folder / "transcript-polished.md").read_text(encoding="utf-8")
    assert "润色后的通顺全文。" in polished
    notes = (folder / "notes.md").read_text(encoding="utf-8")
    assert "## 开篇（00:00–00:01）" in notes
    assert "![开篇](frames/" in notes
    assert any((folder / "frames").glob("*.jpg"))
    assert client.chat.call_count == 3  # 润色 + 章节草稿 + 汇总


def test_no_speech_marks_failed(summary, monkeypatch):
    pipe, item, client = summary
    client.transcribe.return_value = ""
    with pytest.raises(ValueError):
        pipe.run(item["id"])
    done = pipe.read(item["id"])
    assert done["status"] == "failed"
    assert "未检测到语音内容" in done["error"]


def test_include_frames_false_runs_pure_asr(summary):
    pipe, item, client = summary
    item.update(include_frames=False)
    dump(pipe.folder(item["id"]) / "summary.json", item)
    pipe.run(item["id"])
    done = pipe.read(item["id"])
    assert done["status"] == "done"
    assert not (pipe.folder(item["id"]) / "frames").exists()
    assert "](frames/" not in (pipe.folder(item["id"]) / "notes.md").read_text(
        encoding="utf-8"
    )


def test_invalid_timecodes_dropped_not_failed(summary):
    pipe, item, client = summary
    reply = notes_reply()
    reply["chapters"] = [
        reply["chapters"][0],
        {"title": "越界", "start_s": 5.0, "end_s": 9.0, "points": ["x"]},
        {"title": "空段", "start_s": 1.2, "end_s": 1.0, "points": ["y"]},
    ]
    client.chat.side_effect = chat_flows(notes=reply)
    pipe.run(item["id"])
    done = pipe.read(item["id"])
    assert done["status"] == "done"
    notes = (pipe.folder(item["id"]) / "notes.md").read_text(encoding="utf-8")
    assert "开篇" in notes and "越界" not in notes and "空段" not in notes


def test_bad_notes_json_retried_once_then_failed(summary):
    pipe, item, client = summary
    client.chat.side_effect = ["润色稿。", "这不是 JSON", "仍然不是 JSON"]
    with pytest.raises(ValueError):
        pipe.run(item["id"])
    done = pipe.read(item["id"])
    assert done["status"] == "failed"
    assert "模型未返回有效笔记" in done["error"]
    # 润色 1 次 + 章节草稿 1 次失败 + 重试 1 次
    assert client.chat.call_count == 3


def test_overlong_transcript_fails(summary, monkeypatch):
    pipe, item, client = summary
    monkeypatch.setattr("library.summary_pipeline.MAX_TRANSCRIPT_CHARS", 3)
    with pytest.raises(ValueError):
        pipe.run(item["id"])
    done = pipe.read(item["id"])
    assert done["status"] == "failed"
    assert "视频过长" in done["error"]


def test_worker_handles_summary_task(tmp_path, tiny_video):
    from types import SimpleNamespace

    from library.worker import Worker

    cfg = LibraryConfig(data_dir=tmp_path / "data")
    client = Mock()
    client.transcribe.return_value = "这里没有坏。"
    client.chat.side_effect = chat_flows()
    pipe = SummaryPipeline(cfg, client)
    item = pipe.create(tiny_video)
    lib = Mock()
    Worker(lib, cfg, SimpleNamespace(client=client))._handle(
        {"id": 7, "type": "summary", "payload": {"summary_id": item["id"]}}
    )
    assert pipe.read(item["id"])["status"] == "done"
    lib.update_task.assert_called_with(7, status="done", progress=1.0)


# ---- API --------------------------------------------------------------------


def api(summary, monkeypatch):
    from library.app import create_app, get_state

    pipeline, item, client = summary
    monkeypatch.setattr("library.worker.Worker.start", lambda self: None)
    monkeypatch.setattr("library.worker.Worker.stop", lambda self, **kwargs: None)
    http = TestClient(create_app(pipeline.cfg))
    state = get_state()
    state.worker.pipeline.client = client
    state.worker.model_factory = None
    return http, state, pipeline


def test_api_create_run_detail_download_delete(summary, monkeypatch):
    http, state, pipeline = api(summary, monkeypatch)
    _, item, _ = summary
    assert http.post("/api/summaries", json={"path": "/nope.mp4"}).status_code == 400
    assert http.post("/api/summaries", json={"path": __file__}).status_code == 400
    created = http.post(
        "/api/summaries", json={"path": item["path"], "include_frames": True}
    )
    assert created.status_code == 200
    sid = created.json()["id"]
    assert any(s["id"] == sid for s in http.get("/api/summaries").json())
    state.worker._handle(state.lib.next_task())
    detail = http.get(f"/api/summaries/{sid}").json()
    assert detail["status"] == "done"
    assert "开篇" in detail["notes"]
    assert "润色后的通顺全文" in detail["transcript_polished"]
    frame = detail["frames"][0]
    assert http.get(f"/api/summaries/{sid}/frames/{frame}").status_code == 200
    assert (
        http.get("/api/summaries/{sid}/frames/../summary.json").status_code == 404
    )
    notes = http.get(f"/api/summaries/{sid}/download")
    assert notes.status_code == 200 and "开篇" in notes.text
    transcript = http.get(
        f"/api/summaries/{sid}/download", params={"file": "transcript"}
    )
    assert transcript.status_code == 200 and "润色后的通顺全文" in transcript.text
    assert http.get(f"/api/summaries/{sid}/download?file=nope").status_code == 422
    retry = http.post(f"/api/summaries/{sid}/retry")
    assert retry.status_code == 200 and retry.json()["task_id"]
    assert http.delete(f"/api/summaries/{sid}").status_code == 200
    assert http.get(f"/api/summaries/{sid}").status_code == 404
    assert Path(item["path"]).exists()


def test_api_retry_conflicts_while_queued(summary, monkeypatch):
    http, state, _ = api(summary, monkeypatch)
    _, item, _ = summary
    created = http.post("/api/summaries", json={"path": item["path"]})
    sid = created.json()["id"]
    assert http.post(f"/api/summaries/{sid}/retry").status_code == 409


def test_api_delete_running_refused(summary, monkeypatch):
    http, state, pipeline = api(summary, monkeypatch)
    _, item, _ = summary
    task = state.lib.enqueue_task("summary", {"summary_id": item["id"]})
    state.lib.update_task(task, status="running")
    assert http.delete(f"/api/summaries/{item['id']}").status_code == 409
    assert pipeline.folder(item["id"]).exists()


# ---- 超长分块（两段式 + 代码选帧） ------------------------------------------


def long_segments(total_chars, per_segment=300):
    import math

    count = math.ceil(total_chars / per_segment)
    return [
        {"start": i * 15, "end": i * 15 + 15, "text": "字" * per_segment}
        for i in range(count)
    ]


def test_polish_splits_long_transcript_and_caches_per_chunk(tmp_path):
    cfg = LibraryConfig(data_dir=tmp_path / "data")
    client = Mock()
    client.chat.side_effect = ["第一块润色。", "第二块润色。"]
    pipe = SummaryPipeline(cfg, client)
    cache = tmp_path / "cache"
    cache.mkdir()
    transcript = {"precision": "", "segments": long_segments(9000)}
    polished = pipe.polish(transcript, cache, cfg, client)
    assert client.chat.call_count == 2
    assert polished == "第一块润色。\n\n第二块润色。"
    # 重试只补缺失的块：删掉第一块缓存后仅调用一次
    client.chat.reset_mock()
    client.chat.side_effect = ["重写第一块。"]
    chunk_files = sorted(cache.rglob("polish-*.md"))
    assert len(chunk_files) == 2
    chunk_files[0].unlink()
    assert pipe.polish(transcript, cache, cfg, client) == "重写第一块。\n\n第二块润色。"
    assert client.chat.call_count == 1


def test_notes_two_stage_merges_drafts_and_matches_frames(tmp_path):
    cfg = LibraryConfig(data_dir=tmp_path / "data")
    draft1 = {
        "chapters": [
            {"title": "环境准备", "start_s": 0, "end_s": 100, "points": ["装软件"]}
        ]
    }
    draft2 = {
        "chapters": [
            {"title": "写第一行代码", "start_s": 100, "end_s": 200, "points": ["hello"]}
        ]
    }
    merged = notes_reply()
    merged["chapters"] = draft1["chapters"] + draft2["chapters"]
    client = Mock()
    client.chat.side_effect = [
        json.dumps(draft1, ensure_ascii=False),
        json.dumps(draft2, ensure_ascii=False),
        json.dumps(merged, ensure_ascii=False),
    ]
    pipe = SummaryPipeline(cfg, client)
    cache = tmp_path / "cache"
    cache.mkdir()
    transcript = {"precision": "", "segments": long_segments(13000)}
    frames = [(tmp_path / "frame_00_t0.0.jpg", 0.0), (tmp_path / "frame_05_t150.0.jpg", 150.0)]
    notes = pipe.notes(transcript, frames, 200.0, cache, cfg, client)
    assert client.chat.call_count == 3  # 两块草稿 + 汇总
    rendered = render_notes({"path": "/tmp/x.mp4", "duration": 200}, notes, frames)
    assert "环境准备" in rendered and "写第一行代码" in rendered
    # 章节中点就近匹配帧：环境准备(50s)→150s 帧更近？0s 帧更近 → frame_00
    assert "frame_00_t0.0.jpg" in rendered
    assert "frame_05_t150.0.jpg" in rendered


def test_transcript_limit_matches_five_hour_spec():
    from library.summary_pipeline import MAX_TRANSCRIPT_CHARS

    assert MAX_TRANSCRIPT_CHARS == 200_000
