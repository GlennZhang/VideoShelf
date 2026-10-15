"""Web API tests via FastAPI TestClient (worker stubbed out)."""

import pytest
from fastapi.testclient import TestClient

from library.app import create_app
from library.config import LibraryConfig


class StubPipeline:
    """Skips real analysis; marks content done with a canned result."""

    def analyze_content(self, content_id, task_id=None, progress=None):
        from library.pipeline import PIPELINE_VERSION

        self.lib.write_ai_result(
            content_id,
            {"title": "AI 标题", "category": "绘画", "form": "教程", "tags": ["水彩"]},
            PIPELINE_VERSION,
        )
        return {}


@pytest.fixture()
def client(tmp_path, tiny_video, monkeypatch):
    # worker: don't start the thread; run tasks inline on enqueue? Simplest:
    # patch Worker.start to a no-op and handle tasks manually in tests.
    monkeypatch.setattr("library.app.Worker", _NoStartWorker)
    cfg = LibraryConfig(data_dir=tmp_path / "data")
    app = create_app(cfg)
    app.state.lib = app  # unused; AppState is module-global

    with TestClient(app) as tc:
        from library.app import get_state

        st = get_state()
        st.pipeline = StubPipeline()
        st.pipeline.lib = st.lib
        yield tc, st


class _NoStartWorker:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def stop(self, timeout=10.0):
        pass


def _run_queue(st):
    """Drain the task queue inline (scan only; analyze via stub)."""
    from library.scanner import scan_folder

    while True:
        task = st.lib.next_task()
        if task is None:
            return
        if task["type"] == "scan":
            p = task["payload"]
            scan_folder(st.lib, p["folder_id"], p["path"], st.cfg)
            st.lib.update_task(task["id"], status="done")
        elif task["type"] == "analyze":
            st.pipeline.analyze_content(task["payload"]["content_id"])
            st.lib.update_task(task["id"], status="done")


def test_folder_add_scan_list(client, tiny_video):
    tc, st = client
    resp = tc.post("/api/folders", json={"path": str(tiny_video.parent)})
    assert resp.status_code == 200
    _run_queue(st)
    folders = tc.get("/api/folders").json()
    assert len(folders) == 1
    videos = tc.get("/api/videos").json()
    assert videos["total"] == 1
    v = videos["items"][0]
    assert v["title"] == "AI 标题" and v["category"] == "绘画"


def test_patch_video_user_fields(client, tiny_video):
    tc, st = client
    tc.post("/api/folders", json={"path": str(tiny_video.parent)})
    _run_queue(st)
    cid = tc.get("/api/videos").json()["items"][0]["content_id"]
    resp = tc.patch(
        f"/api/videos/{cid}",
        json={"title": "我的标题", "tags": ["手动"], "favorite": True},
    )
    assert resp.status_code == 200
    v = resp.json()
    assert v["title"] == "我的标题"
    assert v["tags"] == ["手动"]
    assert v["user_favorite"] == 1


def test_search_and_collections(client, tiny_video):
    tc, st = client
    tc.post("/api/folders", json={"path": str(tiny_video.parent)})
    _run_queue(st)
    hits = tc.get("/api/videos", params={"q": "AI"}).json()
    assert hits["total"] == 1

    col = tc.post("/api/collections", json={"name": "临摹"}).json()
    cid = tc.get("/api/videos").json()["items"][0]["content_id"]
    tc.post(f"/api/collections/{col['id']}/items", json={"content_id": cid})
    cols = tc.get("/api/collections").json()
    assert cols[0]["video_count"] == 1
    in_col = tc.get("/api/videos", params={"collection_id": col["id"]}).json()
    assert in_col["total"] == 1


def test_batch_actions(client, tiny_video):
    tc, st = client
    tc.post("/api/folders", json={"path": str(tiny_video.parent)})
    _run_queue(st)
    cid = tc.get("/api/videos").json()["items"][0]["content_id"]
    resp = tc.post(
        "/api/batch",
        json={
            "content_ids": [cid],
            "action": "artist",
            "value": "测试画家",
        },
    )
    assert resp.json()["updated"] == 1
    people = tc.get("/api/people", params={"kind": "painter"}).json()
    assert people[0]["canonical_name"] == "测试画家"
    assert people[0]["confirmed_status"] == "confirmed"


def test_set_artist_replaces_previous_painter(client, tiny_video):
    """换画家必须替换旧画家链接，而不是并列两个画家。"""
    tc, st = client
    tc.post("/api/folders", json={"path": str(tiny_video.parent)})
    _run_queue(st)
    cid = tc.get("/api/videos").json()["items"][0]["content_id"]

    tc.post(f"/api/videos/{cid}/artist", json={"name": "张三"})
    people = tc.get(f"/api/videos/{cid}").json()["people"]
    painters = [p for p in people if p["kind"] == "painter"]
    assert len(painters) == 1
    assert painters[0]["canonical_name"] == "张三"
    assert painters[0]["confirmed_status"] == "confirmed"

    tc.post(f"/api/videos/{cid}/artist", json={"name": "李四"})
    people = tc.get(f"/api/videos/{cid}").json()["people"]
    painters = [p for p in people if p["kind"] == "painter"]
    assert len(painters) == 1
    assert painters[0]["canonical_name"] == "李四"
    # and the video is listed under the new painter only
    lib_p = tc.get("/api/people", params={"kind": "painter"}).json()
    li = next(p for p in lib_p if p["canonical_name"] == "李四")
    hits = tc.get("/api/videos", params={"artist_id": li["id"]}).json()
    assert hits["total"] == 1


def test_rename_person_merges_same_name(client, tiny_video):
    """改名成已有同名画家时自动合并，视频归属跟着转移。"""
    tc, st = client
    tc.post("/api/folders", json={"path": str(tiny_video.parent)})
    _run_queue(st)
    lib = st.lib
    keep_id = lib.upsert_person("painter", "齐白石")
    gone_id = lib.upsert_person("painter", "齐白石临摹")
    cid = tc.get("/api/videos").json()["items"][0]["content_id"]
    lib.set_video_artist(cid, gone_id)

    resp = tc.patch(
        f"/api/people/{gone_id}",
        json={"canonical_name": "齐白石", "confirmed_status": "confirmed"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == keep_id
    people = tc.get("/api/people", params={"kind": "painter"}).json()
    assert len([p for p in people if p["canonical_name"] == "齐白石"]) == 1
    # the video followed the merge
    video_p = tc.get(f"/api/videos/{cid}").json()["people"]
    assert any(
        p["canonical_name"] == "齐白石" and p["kind"] == "painter" for p in video_p
    )


def test_stream_direct_mp4(client, tiny_video):
    tc, st = client
    tc.post("/api/folders", json={"path": str(tiny_video.parent)})
    _run_queue(st)
    video = tc.get("/api/videos").json()["items"][0]
    file_id = video["files"][0]["id"]
    resp = tc.get(f"/api/files/{file_id}/stream")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert len(resp.content) > 1000


def test_stats(client, tiny_video):
    tc, st = client
    tc.post("/api/folders", json={"path": str(tiny_video.parent)})
    _run_queue(st)
    stats = tc.get("/api/stats").json()
    assert stats["total"] == 1
    assert stats["categories"].get("绘画") == 1


def test_browse_counts_update_and_match_filtered_lists(client):
    tc, st = client
    lib = st.lib
    assert {k: tc.get("/api/stats").json()[k] for k in ("total", "pending", "favorites")} == {
        "total": 0, "pending": 0, "favorites": 0,
    }
    cid = lib.upsert_content("a" * 64, 1)
    assert tc.get("/api/stats").json()["pending"] == 1
    lib.write_ai_result(cid, {"title": "示例"}, "v1")
    a = lib.upsert_person("painter", "甲")
    b = lib.upsert_person("source_account", "乙")
    lib.link_person(cid, a, "author")
    lib.link_person(cid, b, "reposting_account")
    # Two pending associations still count as one video.
    stats = tc.get("/api/stats").json()
    assert stats["total"] == stats["pending"] == 1
    assert stats["pending"] == tc.get("/api/videos?pending=true").json()["total"]
    tc.post(f"/api/videos/{cid}/people/{a}/confirm/author")
    assert tc.get("/api/stats").json()["pending"] == 1
    tc.post(f"/api/videos/{cid}/people/{b}/confirm/reposting_account")
    assert tc.get("/api/stats").json()["pending"] == 0
    for value in (True, False):
        assert tc.patch(f"/api/videos/{cid}", json={"favorite": value}).status_code == 200
        assert tc.get("/api/stats").json()["favorites"] == int(value)
        assert tc.get("/api/videos?favorite=true").json()["total"] == int(value)
    tc.post("/api/batch", json={"content_ids": [cid], "action": "favorite", "value": True})
    assert tc.get("/api/stats").json()["favorites"] == 1
    lib.set_analysis_status(cid, "analyzing")
    assert tc.get("/api/stats").json()["pending"] == 1
    assert tc.get("/api/videos?pending=true").json()["total"] == 1


def test_pending_filter_and_totals_apply_before_pagination(client):
    tc, st = client
    for i in range(65):
        cid = st.lib.upsert_content(f"{i:064x}", 1)
        st.lib.write_ai_result(cid, {"title": str(i)}, "v1")
    # Put the only pending video beyond the first page.
    st.lib.set_analysis_status(cid, "failed", "example error")
    st.lib._conn.execute("UPDATE videos SET updated_at='2000-01-01' WHERE content_id=?", (cid,))
    st.lib._conn.commit()
    data = tc.get("/api/videos?limit=1").json()
    assert data["total"] == 65 and len(data["items"]) == 1
    pending = tc.get("/api/videos?pending=true&limit=1").json()
    assert pending["total"] == 1
    assert pending["items"][0]["content_id"] == cid
    assert tc.get("/api/stats").json()["pending"] == 1


def test_unknown_artist_counts_filter_and_assignment(client):
    tc, st = client
    lib = st.lib
    empty, guests, known = [lib.upsert_content(f"{i:064x}", 1) for i in range(3)]
    for cid in (empty, guests, known):
        lib.write_ai_result(cid, {"title": "归属测试", "category": "生活"}, "v1")
    guest = lib.upsert_person("painter", "嘉宾")
    account = lib.upsert_person("source_account", "来源")
    author = lib.upsert_person("painter", "作者")
    lib.link_person(guests, guest, "presenter")
    lib.link_person(guests, account, "reposting_account")
    lib.link_person(known, author, "author")
    for cid in (empty, guests, known):
        lib.reindex_content(cid)
    assert tc.get("/api/stats").json()["unknown_artists"] == 2
    data = tc.get("/api/videos?unknown_artist=true&limit=1").json()
    assert data["total"] == 2 and len(data["items"]) == 1
    data = tc.get("/api/videos?unknown_artist=true&offset=1&limit=1").json()
    assert data["total"] == 2 and len(data["items"]) == 1
    data = tc.get("/api/videos", params={"unknown_artist": True, "q": "归属测试"}).json()
    assert {v["content_id"] for v in data["items"]} == {empty, guests}
    # Assigning a painter removes only that video from the unknown group.
    assert tc.post(f"/api/videos/{guests}/artist", json={"name": "指定作者"}).status_code == 200
    assert tc.get("/api/stats").json()["unknown_artists"] == 1
    data = tc.get("/api/videos?unknown_artist=true").json()
    assert [v["content_id"] for v in data["items"]] == [empty]
    # Batch assignment also updates the count; the empty entry remains available.
    assert tc.post("/api/batch", json={"content_ids": [empty], "action": "artist", "value": "指定作者"}).status_code == 200
    assert tc.get("/api/stats").json()["unknown_artists"] == 0
    assert tc.get("/api/videos?unknown_artist=true").json() == {"total": 0, "items": []}
