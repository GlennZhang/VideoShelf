"""Course contracts: full coverage, evidence checks, cache and portable output."""

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from library.config import LibraryConfig
from library.course_pipeline import CoursePipeline, read_subtitles


@pytest.fixture
def course(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("10.mp4", "2.mp4"):
        (source / name).write_bytes(name.encode())
    client = Mock()
    client.transcribe.return_value = "保持人物一致，参考图强度设置为0.8"
    client.chat.return_value = json.dumps(
        {
            "steps": [
                {
                    "title": "人物一致性",
                    "detail": "设置参考图强度0.8",
                    "segment": 0,
                    "evidence": "参考图",
                    "topic": "角色与人物一致性",
                    "prompts": [{"text": "参考人物", "source": "口述"}],
                }
            ]
        }
    )
    client.chat_vision.return_value = json.dumps(
        {
            "selected": 1,
            "caption": "参数界面",
            "screen_prompts": ["参考图中的人物"],
            "prompt_context": "即梦提示词输入框",
            "uncertainty": "版本待核对",
        }
    )
    pipeline = CoursePipeline(LibraryConfig(data_dir=tmp_path / "data"), client)
    return pipeline, pipeline.import_course(source), client


def mock_media(monkeypatch, duration=65):
    monkeypatch.setattr(
        "library.course_pipeline.media.probe",
        lambda p: SimpleNamespace(duration_s=duration, audio_codec="aac"),
    )

    def audio(path, wav, start, span):
        wav.write_bytes(b"audio")

    monkeypatch.setattr("library.course_pipeline.media.extract_audio", audio)

    def frame(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"image")

    monkeypatch.setattr("library.course_pipeline.subprocess.run", frame)


def test_natural_order_and_boundary(course):
    pipeline, manifest, _ = course
    assert [l["title"] for l in manifest["lessons"]] == ["2", "10"]
    with pytest.raises(ValueError):
        pipeline.course_dir("../outside")


def test_full_transcription_cache_and_portable_export(course, monkeypatch):
    pipeline, manifest, client = course
    mock_media(monkeypatch)
    pipeline.run(manifest["id"])
    assert client.transcribe.call_count == 6  # Both videos: 0..30,30..60,60..65.
    saved = pipeline.load(manifest["id"])
    cache = pipeline.course_dir(saved["id"]) / saved["lessons"][0]["cache"]
    transcript = json.loads((cache / "transcript.json").read_text())
    assert [(s["start"], s["end"]) for s in transcript["segments"]] == [
        (0, 30),
        (30, 60),
        (60, 65),
    ]
    pipeline.run(saved["id"])
    assert client.transcribe.call_count == 6
    assert client.chat.call_count == 2
    with zipfile.ZipFile(pipeline.archive(saved["id"])) as archive:
        assert any(n.startswith("assets/001-001-") for n in archive.namelist())
        lesson = archive.read("lessons/001.md").decode()
        assert "../assets/001-001-" in lesson
        assert "口述" in lesson and "画面原文" in lesson and "待核对" not in lesson
        assert not any(
            n.startswith("/") or ".." in Path(n).parts for n in archive.namelist()
        )


def test_failed_chunk_resume_and_other_lessons_continue(course, monkeypatch):
    pipeline, manifest, client = course
    mock_media(monkeypatch)
    client.transcribe.side_effect = [
        "第一块参考图",
        RuntimeError("ASR offline"),
        "下一课1参考图",
        "下一课2参考图",
        "下一课3参考图",
    ]
    with pytest.raises(ValueError, match="部分课程"):
        pipeline.run(manifest["id"])
    saved = pipeline.load(manifest["id"])
    assert [l["status"] for l in saved["lessons"]] == ["failed", "done"]
    client.transcribe.side_effect = None
    client.transcribe.return_value = "恢复的内容参考图"
    before = client.transcribe.call_count
    pipeline.run(manifest["id"])
    assert client.transcribe.call_count == before + 2


def test_invalid_evidence_not_cached(course, monkeypatch):
    pipeline, manifest, client = course
    mock_media(monkeypatch)
    client.chat.return_value = json.dumps(
        {
            "steps": [
                {
                    "title": "错误",
                    "detail": "错误",
                    "segment": 999,
                    "evidence": "参考图",
                }
            ]
        }
    )
    with pytest.raises(ValueError):
        pipeline.run(manifest["id"])
    assert not list(pipeline.root.rglob("notes-*.json"))
    assert not client.chat_vision.called


def test_subtitles(tmp_path):
    srt = tmp_path / "clip.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n<b>提示词</b>\n\n2\n00:00:02,000 --> 00:00:03,500\n第二句\n"
    )
    segments = read_subtitles(srt, 4)
    assert [s.text for s in segments] == ["提示词", "第二句"]
    vtt = tmp_path / "clip.vtt"
    vtt.write_text("WEBVTT\n\n00:00.000 --> 00:02.000\n内容\n")
    assert read_subtitles(vtt, 4)[0].end == 2
    vtt.write_text("broken")
    with pytest.raises(ValueError):
        read_subtitles(vtt, 4)


def test_subtitles_prevent_asr(course, monkeypatch):
    pipeline, manifest, client = course
    mock_media(monkeypatch)
    for lesson in manifest["lessons"]:
        Path(lesson["path"]).with_suffix(".srt").write_text(
            "1\n00:00:00,000 --> 00:00:02,000\n字幕内容参考图\n"
        )
    pipeline.run(manifest["id"])
    client.transcribe.assert_not_called()


def test_source_change_invalidates_cache(course, monkeypatch):
    pipeline, manifest, client = course
    mock_media(monkeypatch)
    pipeline.run(manifest["id"])
    Path(manifest["lessons"][0]["path"]).write_bytes(b"changed video")
    pipeline.run(manifest["id"])
    assert client.transcribe.call_count == 9


def test_real_ffmpeg_end_to_end(tmp_path, tiny_video):
    client = Mock()
    client.transcribe.return_value = "人物一致性需要参考图"
    client.chat.return_value = json.dumps(
        {
            "steps": [
                {
                    "title": "参考图",
                    "detail": "选择人物参考图",
                    "segment": 0,
                    "evidence": "参考图",
                }
            ]
        }
    )
    client.chat_vision.return_value = json.dumps(
        {"selected": 0, "caption": "视频原始画面"}
    )
    pipeline = CoursePipeline(LibraryConfig(data_dir=tmp_path / "data"), client)
    manifest = pipeline.import_course(tiny_video.parent)
    pipeline.run(manifest["id"])
    images = list(pipeline.course_dir(manifest["id"]).glob("export/assets/*.jpg"))
    assert images and all(p.read_bytes().startswith(b"\xff\xd8") for p in images)
    assert client.transcribe.call_count == 1


def test_api_and_queue(tmp_path, tiny_video, monkeypatch):
    from fastapi.testclient import TestClient
    from library.app import create_app, get_state

    monkeypatch.setattr("library.worker.Worker.start", lambda self: None)
    monkeypatch.setattr("library.worker.Worker.stop", lambda self, **kwargs: None)
    mock_media(monkeypatch, 2)
    app = create_app(LibraryConfig(data_dir=tmp_path / "data"))
    with TestClient(app) as http:
        state = get_state()
        state.client.transcribe = Mock(return_value="参考图")
        state.client.chat = Mock(
            return_value=json.dumps(
                {
                    "steps": [
                        {
                            "title": "参考图",
                            "detail": "选择参考图",
                            "segment": 0,
                            "evidence": "参考图",
                        }
                    ]
                }
            )
        )
        state.client.chat_vision = Mock(
            return_value=json.dumps({"selected": 0, "caption": "原图"})
        )
        assert (
            http.post(
                "/api/courses", json={"path": str(tmp_path / "missing")}
            ).status_code
            == 400
        )
        response = http.post("/api/courses", json={"path": str(tiny_video.parent)})
        assert response.status_code == 200
        cid = response.json()["id"]
        assert http.post(f"/api/courses/{cid}/retry").status_code == 409
        assert http.get(f"/api/courses/{cid}/download").status_code == 409
        task = state.lib.next_task()
        state.worker._handle(task)
        assert http.get(f"/api/courses/{cid}").json()["status"] == "done"
        lesson = http.get(f"/api/courses/{cid}/lessons/001").json()
        image = lesson["notes"]["steps"][0]["image"]
        assert http.get(f"/api/courses/{cid}/assets/{image}").status_code == 200
        assert http.get(f"/api/courses/{cid}/assets/course.json").status_code == 404
        assert http.get(f"/api/courses/{cid}/download").content.startswith(b"PK")
        source = http.get(
            f"/api/courses/{cid}/lessons/001/source", headers={"Range": "bytes=0-15"}
        )
        assert source.status_code == 206
        assert len(source.content) == 16
        assert http.get("/api/courses/not-valid").status_code == 404
        assert http.post(f"/api/courses/{cid}/retry").status_code == 200
        assert http.get("/static/courses.html").status_code == 200
        # A restart requeues a claimed task and preserves successful chunk caches.
        retry = state.lib.next_task()
        state.lib.requeue_stale_running()
        assert state.lib.next_task()["id"] == retry["id"]


def test_visual_failure_resumes_without_retranscription(course, monkeypatch):
    pipeline, manifest, client = course
    mock_media(monkeypatch)
    client.chat_vision.side_effect = RuntimeError("vision unavailable")
    with pytest.raises(ValueError):
        pipeline.run(manifest["id"])
    count = client.transcribe.call_count
    client.chat_vision.side_effect = None
    client.chat_vision.return_value = json.dumps(
        {"selected": None, "caption": "无匹配画面", "uncertainty": "无法确认"}
    )
    pipeline.run(manifest["id"])
    assert client.transcribe.call_count == count
    notes = pipeline.load(manifest["id"])
    assert all(l["status"] == "done" for l in notes["lessons"])


def test_non_teaching_has_transcript_without_invented_steps(course, monkeypatch):
    pipeline, manifest, client = course
    mock_media(monkeypatch)
    client.chat.return_value = '{"steps":[]}'
    pipeline.run(manifest["id"])
    assert not client.chat_vision.called
    text = (pipeline.course_dir(manifest["id"]) / "export/lessons/001.md").read_text()
    assert "未提取到可确认的教学步骤" in text


@pytest.mark.parametrize(
    "context", ["", "无明确输入框或提示词文档", "四、人物设定 -> 主角"]
)
def test_screen_text_without_prompt_context_is_not_prompt(course, monkeypatch, context):
    pipeline, manifest, client = course
    mock_media(monkeypatch)
    client.chat_vision.return_value = json.dumps(
        {
            "selected": 0,
            "caption": "字幕画面",
            "screen_prompts": ["bilibili"],
            "prompt_context": context,
        }
    )
    pipeline.run(manifest["id"])
    text = (
        pipeline.course_dir(manifest["id"]) / "export/prompts/README.md"
    ).read_text()
    assert "bilibili" not in text


def test_vision_model_change_reuses_transcription(course, monkeypatch):
    from dataclasses import replace

    pipeline, manifest, client = course
    mock_media(monkeypatch)
    pipeline.run(manifest["id"])
    updated = replace(
        pipeline.cfg, omlx=replace(pipeline.cfg.omlx, vision_model="another-model")
    )
    CoursePipeline(updated, client).run(manifest["id"])
    assert client.transcribe.call_count == 6
    assert client.chat.call_count == 4


def test_regeneration_preserves_persistent_document_assets(course, monkeypatch):
    pipeline, manifest, client = course
    mock_media(monkeypatch)
    pipeline.run(manifest["id"])
    out = pipeline.course_dir(manifest["id"]) / "export"
    assert list((out / "assets").glob("*.jpg"))
    from dataclasses import replace

    updated = replace(
        pipeline.cfg, omlx=replace(pipeline.cfg.omlx, vision_model="new-model")
    )
    client.chat.return_value = '{"steps":[]}'
    CoursePipeline(updated, client).run(manifest["id"])
    assert list((out / "assets").glob("*.jpg"))
    assert not list(
        (pipeline.course_dir(manifest["id"]) / "generated/assets").glob("*.jpg")
    )


def test_quote_corrects_wrong_segment_number(course, monkeypatch):
    pipeline, manifest, client = course
    mock_media(monkeypatch)
    client.transcribe.side_effect = ["开场内容", "参考图出现在这里", "结束内容"] * 2
    pipeline.run(manifest["id"])
    saved = pipeline.load(manifest["id"])
    notes = json.loads(
        (
            pipeline.course_dir(saved["id"])
            / saved["lessons"][0]["cache"]
            / "lesson.json"
        ).read_text()
    )
    assert notes["steps"][0]["segment"] == 1
    assert notes["steps"][0]["start"] == 30
    assert 30 <= notes["steps"][0]["image_time"] <= 60


def test_unverifiable_quote_is_rejected_after_bounded_retries(course, monkeypatch):
    pipeline, manifest, client = course
    mock_media(monkeypatch)
    client.chat.return_value = json.dumps(
        {
            "steps": [
                {
                    "title": "内容",
                    "detail": "内容说明",
                    "segment": 0,
                    "evidence": "不存在的原文",
                }
            ]
        }
    )
    pipeline.run(manifest["id"])
    assert client.chat.call_count == 6
    assert not client.chat_vision.called
    saved = pipeline.load(manifest["id"])
    assert saved["status"] == "done"
    notes = json.loads(
        (
            pipeline.course_dir(saved["id"])
            / saved["lessons"][0]["cache"]
            / "lesson.json"
        ).read_text()
    )
    assert notes["steps"] == []
    assert notes["unverified"][0]["evidence"] == "不存在的原文"


def test_malformed_model_response_is_corrected_with_bounded_retry(course, monkeypatch):
    pipeline, manifest, client = course
    mock_media(monkeypatch)
    good = client.chat.return_value
    client.chat.side_effect = ['{"error":"bad schema"}', good, good]
    pipeline.run(manifest["id"])
    assert client.chat.call_count == 3
    assert pipeline.load(manifest["id"])["status"] == "done"


def test_evidence_spanning_asr_boundary_keeps_full_time_window(course, monkeypatch):
    pipeline, manifest, client = course
    mock_media(monkeypatch)
    client.transcribe.side_effect = ["先选择参考", "图并保持一致", "结束"] * 2
    pipeline.run(manifest["id"])
    saved = pipeline.load(manifest["id"])
    notes = json.loads(
        (
            pipeline.course_dir(saved["id"])
            / saved["lessons"][0]["cache"]
            / "lesson.json"
        ).read_text()
    )
    assert notes["steps"][0]["start"] == 0
    assert notes["steps"][0]["end"] == 60
    assert notes["steps"][0]["evidence"] == "参考图"


def test_whitespace_equivalent_quote_uses_actual_source():
    from library.course_pipeline import Segment, locate_evidence

    segments = [Segment(start=0, end=30, text="像脚本的撰写、AI配音、画面生成。")]
    assert locate_evidence(segments, [0], "脚本的撰写、AI 配音、画面生成", 0) == (
        0,
        0,
        "脚本的撰写、AI配音、画面生成",
    )
    assert locate_evidence(segments, [0], "脚本的撰写、AI视频、画面生成", 0) is None


def test_unknown_topic_preserves_content():
    from library.course_pipeline import Notes

    notes = Notes.model_validate(
        {
            "steps": [
                {
                    "title": "场景造型",
                    "detail": "调整服装",
                    "segment": 0,
                    "evidence": "调整服装",
                    "topic": "场景造型修改技巧",
                }
            ]
        }
    )
    assert notes.steps[0].topic == "其他"
    assert notes.steps[0].detail == "调整服装"


def test_retry_does_not_repeat_completed_lessons(course, monkeypatch):
    from library.course_pipeline import write_json

    pipeline, manifest, client = course
    mock_media(monkeypatch)
    pipeline.run(manifest["id"])
    saved = pipeline.load(manifest["id"])
    saved["lessons"][0]["status"] = "failed"
    write_json(pipeline.course_dir(manifest["id"]) / "course.json", saved)
    process = Mock(wraps=pipeline.process_lesson)
    monkeypatch.setattr(pipeline, "process_lesson", process)
    pipeline.run(manifest["id"], retry_only=True)
    assert process.call_count == 1
    assert process.call_args.args[1]["id"] == "001"


@pytest.mark.parametrize("status", ["queued", "retry", "running", "failed", "done"])
def test_delete_course_preserves_sources_and_other_courses(
    tmp_path, tiny_video, monkeypatch, status
):
    from fastapi.testclient import TestClient
    from library.app import create_app, get_state

    monkeypatch.setattr("library.worker.Worker.start", lambda self: None)
    monkeypatch.setattr("library.worker.Worker.stop", lambda self, **kwargs: None)
    with TestClient(create_app(LibraryConfig(data_dir=tmp_path / "data"))) as http:
        course = http.post("/api/courses", json={"path": str(tiny_video.parent)}).json()
        other = http.post("/api/courses", json={"path": str(tiny_video.parent)}).json()
        state = get_state()
        state.lib.update_task(course["task_id"], status=status)
        folder = state.cfg.data_dir / "courses" / course["id"]
        (folder / "generated.txt").write_text("cache and tutorial")
        response = http.delete("/api/courses/" + course["id"])
        assert tiny_video.is_file()
        assert http.get("/api/courses/" + other["id"]).status_code == 200
        if status == "running":
            assert response.status_code == 409
            assert (folder / "generated.txt").exists()
        else:
            assert response.status_code == 200
            assert not folder.exists()
            assert http.get("/api/courses/" + course["id"]).status_code == 404
            assert http.delete("/api/courses/" + course["id"]).status_code == 404
            assert all(
                t["payload"].get("course_id") != course["id"]
                for t in state.lib.list_tasks()
            )
            assert any(
                t["payload"].get("course_id") == other["id"]
                for t in state.lib.list_tasks()
            )


def test_asr_sentence_stop_does_not_break_cross_chunk_quote():
    from library.course_pipeline import Segment, locate_evidence

    segments = [
        Segment(start=0, end=30, text="人与人。"),
        Segment(start=30, end=60, text="之间设置0.8"),
    ]
    assert locate_evidence(segments, [0, 1], "人与人之间设置0.8", 0) == (
        0,
        1,
        "人与人。之间设置0.8",
    )
    assert locate_evidence(segments, [0, 1], "人与人之间设置08", 0) is None


def test_model_response_cache_is_scoped_to_each_text_batch(course, monkeypatch):
    pipeline, manifest, client = course
    mock_media(monkeypatch)
    client.transcribe.return_value = "参考图" + "文" * 5000
    client.chat.side_effect = [
        json.dumps(
            {
                "steps": [
                    {
                        "title": "操作",
                        "detail": "使用参考图",
                        "segment": i,
                        "evidence": "参考图",
                    }
                ]
            }
        )
        for i in (0, 1, 2, 0, 1, 2)
    ]
    pipeline.run(manifest["id"])
    assert client.chat.call_count == 6
    assert len(list(pipeline.root.rglob("model-response-*.json"))) == 6
    saved = pipeline.load(manifest["id"])
    notes = json.loads(
        (
            pipeline.course_dir(saved["id"])
            / saved["lessons"][0]["cache"]
            / "lesson.json"
        ).read_text()
    )
    assert [s["segment"] for s in notes["steps"]] == [0, 1, 2]


def test_pipeline_export_uses_saved_markdown(course, monkeypatch):
    from library.course_documents import CourseDocuments

    pipeline, manifest, client = course
    mock_media(monkeypatch)
    pipeline.run(manifest["id"])
    store = CourseDocuments(pipeline.course_dir(manifest["id"]))
    doc = store.read("lessons/001.md")
    mine = doc["markdown"] + "\n## 我的学习心得\n\n人物设定先固定。\n"
    store.save(doc["path"], mine, doc["revision"])
    with zipfile.ZipFile(pipeline.archive(manifest["id"])) as archive:
        assert archive.read("lessons/001.md").decode() == mine
    pipeline.run(manifest["id"])
    assert store.read(doc["path"])["markdown"] == mine


def test_regenerate_reuses_asr_and_offers_draft_without_overwriting_edits(
    course, monkeypatch
):
    from library.course_documents import CourseDocuments

    pipeline, manifest, client = course
    mock_media(monkeypatch)
    pipeline.run(manifest["id"])
    store = CourseDocuments(pipeline.course_dir(manifest["id"]))
    doc = store.read("lessons/001.md")
    mine = doc["markdown"] + "\n我的实战经验。\n"
    store.save(doc["path"], mine, doc["revision"])
    client.chat.return_value = json.dumps(
        {
            "steps": [
                {
                    "title": "重新整理后的技巧",
                    "detail": "使用参考图",
                    "segment": 0,
                    "evidence": "参考图",
                }
            ]
        }
    )
    pipeline.run(manifest["id"], regenerate=True)
    assert client.transcribe.call_count == 6
    assert client.chat.call_count == 4
    assert store.read(doc["path"])["markdown"] == mine
    assert store.read(doc["path"])["has_new_draft"]
    assert "重新整理后的技巧" in store.read(doc["path"], generated=True)["markdown"]


def test_failed_regeneration_resumes_its_own_draft_cache(course, monkeypatch):
    pipeline, manifest, client = course
    mock_media(monkeypatch)
    pipeline.run(manifest["id"])
    client.chat.return_value = json.dumps(
        {
            "steps": [
                {
                    "title": "新的AI知识点",
                    "detail": "使用参考图",
                    "segment": 0,
                    "evidence": "参考图",
                }
            ]
        }
    )
    client.chat_vision.side_effect = RuntimeError("vision unavailable")
    with pytest.raises(ValueError):
        pipeline.run(manifest["id"], regenerate=True)
    calls = client.chat.call_count
    client.chat_vision.side_effect = None
    pipeline.run(manifest["id"], retry_only=True)
    assert client.chat.call_count == calls
    assert client.transcribe.call_count == 6
    saved = pipeline.load(manifest["id"])
    notes = json.loads(
        (
            pipeline.course_dir(saved["id"])
            / saved["lessons"][0]["cache"]
            / "lesson.json"
        ).read_text()
    )
    assert notes["steps"][0]["title"] == "新的AI知识点"
