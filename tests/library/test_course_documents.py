"""Editing must never turn inference/export caches into the user's only copy."""

from pathlib import Path

import pytest

from library.course_documents import (
    CourseDocuments,
    DocumentConflict,
    lesson_diagnostics,
)


@pytest.fixture
def documents(tmp_path):
    staged = tmp_path / "staged"
    (staged / "assets").mkdir(parents=True)
    (staged / "lessons").mkdir()
    (staged / "lessons/001.md").write_text("# 教程\n\n![示例](../assets/abc.jpg)\n")
    (staged / "assets/abc.jpg").write_bytes(b"image")
    store = CourseDocuments(tmp_path / "course")
    store.sync_generated(staged)
    return store, staged


def test_edits_survive_regeneration_and_export(documents, tmp_path):
    store, staged = documents
    old = store.read("lessons/001.md")
    mine = old["markdown"] + "\n我的技巧：保持角色一致。\n"
    saved = store.save(old["path"], mine, old["revision"])
    (staged / "lessons/001.md").write_text("# 新AI稿\n")
    store.sync_generated(staged)
    assert store.read(old["path"])["markdown"] == mine
    assert store.read(old["path"])["has_new_draft"]
    assert store.read(old["path"], generated=True)["markdown"] == "# 新AI稿\n"
    store.export_to(tmp_path / "export")
    assert (tmp_path / "export/lessons/001.md").read_text() == mine
    assert (tmp_path / "export/assets/abc.jpg").read_bytes() == b"image"
    assert saved["revision"] != old["revision"]


def test_existing_documents_wait_for_explicit_draft_adoption(documents):
    store, staged = documents
    (staged / "lessons/001.md").write_text("# 更新\n")
    store.sync_generated(staged)
    assert store.read("lessons/001.md")["markdown"].startswith("# 教程")
    assert store.read("lessons/001.md")["has_new_draft"]
    assert store.read("lessons/001.md", generated=True)["markdown"] == "# 更新\n"


def test_empty_document_is_a_valid_edit(documents):
    store, staged = documents
    old = store.read("lessons/001.md")
    store.save(old["path"], "", old["revision"])
    store.sync_generated(staged)
    assert store.read(old["path"])["markdown"] == ""


def test_stale_save_cannot_replace_newer_edit(documents):
    store, _ = documents
    old = store.read("lessons/001.md")
    store.save(old["path"], "第一个页面的修改", old["revision"])
    with pytest.raises(DocumentConflict):
        store.save(old["path"], "过期页面的修改", old["revision"])
    assert store.read(old["path"])["markdown"] == "第一个页面的修改"


def test_draft_requires_explicit_current_versions(documents):
    store, staged = documents
    old = store.read("lessons/001.md")
    saved = store.save(old["path"], "我的修改", old["revision"])
    (staged / "lessons/001.md").write_text("AI新稿")
    store.sync_generated(staged)
    draft = store.read(old["path"], generated=True)
    with pytest.raises(DocumentConflict):
        store.adopt(old["path"], saved["revision"], "stale")
    assert store.read(old["path"])["markdown"] == "我的修改"
    adopted = store.adopt(old["path"], saved["revision"], draft["revision"])
    assert adopted["markdown"] == "AI新稿"
    assert not adopted["has_new_draft"]


@pytest.mark.parametrize(
    "path",
    [
        "../../outside.md",
        "../generated/lessons/001.md",
        "assets/abc.jpg",
        "not-found.md",
    ],
)
def test_document_paths_are_bounded(documents, path):
    store, _ = documents
    with pytest.raises(FileNotFoundError):
        store.read(path)


def test_routine_diagnostics_are_not_actionable_reviews():
    notes = {
        "precision": "块级",
        "steps": [
            {
                "title": "设置",
                "start": 0,
                "end": 30,
                "evidence": "原文",
                "tips": [],
                "visual": {
                    "uncertainty": "无明确输入框；缺少明确的提示词输入区域依据，未收录画面文字"
                },
            }
        ],
    }
    assert lesson_diagnostics(notes)["reviews"] == []
    notes["steps"][0]["visual"][
        "uncertainty"
    ] = "提示词末尾被截断；缺少明确的提示词输入区域依据，未收录画面文字"
    review = lesson_diagnostics(notes)["reviews"]
    assert len(review) == 1 and "截断" in review[0]["message"]


def test_document_api_roundtrip_and_conflict(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from library.app import create_app, get_state
    from library.config import LibraryConfig
    from library.course_pipeline import CoursePipeline

    monkeypatch.setattr("library.worker.Worker.start", lambda self: None)
    monkeypatch.setattr("library.worker.Worker.stop", lambda self, **kwargs: None)
    videos = tmp_path / "videos"
    videos.mkdir()
    (videos / "1.mp4").write_bytes(b"video")
    with TestClient(create_app(LibraryConfig(data_dir=tmp_path / "data"))) as http:
        state = get_state()
        pipeline = CoursePipeline(state.cfg, state.client)
        course = pipeline.import_course(videos)
        staged = tmp_path / "staged"
        staged.mkdir()
        (staged / "README.md").write_text("# 课程")
        store = CourseDocuments(pipeline.course_dir(course["id"]))
        store.sync_generated(staged)
        base = f"/api/courses/{course['id']}"
        doc = http.get(base + "/document", params={"path": "README.md"}).json()
        saved = http.put(
            base + "/document",
            params={"path": "README.md"},
            json={"markdown": "我的笔记", "revision": doc["revision"]},
        )
        assert saved.status_code == 200
        assert (
            http.get(base + "/document", params={"path": "README.md"}).json()[
                "markdown"
            ]
            == "我的笔记"
        )
        assert (
            http.put(
                base + "/document",
                params={"path": "README.md"},
                json={"markdown": "过期修改", "revision": doc["revision"]},
            ).status_code
            == 409
        )
        assert (
            http.get(
                base + "/document", params={"path": "../../outside.md"}
            ).status_code
            == 404
        )
        assert http.get(base + "/document-assets/README.md").status_code == 404
        assert http.get(base + "/documents").json()[0]["path"] == "README.md"
        from library.course_documents import write_text

        write_text(store.generated / "README.md", "# AI 新稿")
        draft = http.get(
            base + "/document", params={"path": "README.md", "draft": True}
        ).json()
        assert draft["markdown"] == "# AI 新稿"
        assert (
            http.post(
                base + "/document/adopt",
                params={"path": "README.md"},
                json={"revision": doc["revision"], "draft_revision": draft["revision"]},
            ).status_code
            == 409
        )
        latest = http.get(base + "/document", params={"path": "README.md"}).json()
        adopted = http.post(
            base + "/document/adopt",
            params={"path": "README.md"},
            json={"revision": latest["revision"], "draft_revision": draft["revision"]},
        )
        assert adopted.status_code == 200 and adopted.json()["markdown"] == "# AI 新稿"
        generation = http.post(base + "/regenerate")
        assert generation.status_code == 200
        assert state.lib.list_tasks()[0]["payload"]["regenerate"] is True
        assert http.post(base + "/regenerate").status_code == 409
