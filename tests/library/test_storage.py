"""Tests for config loading, hashing and the storage layer."""

import pytest

from library.config import ConfigError, load_library_config
from library.hashing import file_sha256


# -- config -----------------------------------------------------------------


def test_config_defaults(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("library: {}\nomlx: {}\n", encoding="utf-8")
    cfg = load_library_config(cfg_file)
    assert cfg.host == "127.0.0.1"
    assert cfg.db_path.name == "library.db"
    assert cfg.omlx.base_url == "http://127.0.0.1:8989/v1"
    assert cfg.omlx.effective_api_key == "noapikey"


def test_config_env_override(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("omlx: {api_key: from-file}\n", encoding="utf-8")
    monkeypatch.setenv("OMLX_API_KEY", "from-env")
    cfg = load_library_config(cfg_file)
    assert cfg.omlx.effective_api_key == "from-env"


def test_config_rejects_unknown_keys(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("library: {bogus: 1}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_library_config(cfg_file)


def test_config_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_library_config(tmp_path / "nope.yaml")


# -- hashing ----------------------------------------------------------------


def test_sha256_stable_and_sensitive(tmp_path):
    a = tmp_path / "a.bin"
    a.write_bytes(b"hello library")
    b = tmp_path / "b.bin"
    b.write_bytes(b"hello library")
    c = tmp_path / "c.bin"
    c.write_bytes(b"hello library!")
    assert file_sha256(a) == file_sha256(b)
    assert file_sha256(a) != file_sha256(c)


# -- storage ----------------------------------------------------------------


def _import_file(lib, path, folder_id=None, sha=None):
    sha = sha or ("h" * 64)
    stat = path.stat()
    content_id = lib.upsert_content(sha, stat.st_size)
    lib.upsert_file(content_id, str(path), folder_id, stat.st_size, stat.st_mtime_ns)
    return content_id


def test_duplicate_paths_share_content(lib, tmp_path):
    p1 = tmp_path / "v1.mp4"
    p2 = tmp_path / "nested" / "v2.mp4"
    p2.parent.mkdir()
    p1.write_bytes(b"data")
    p2.write_bytes(b"data")
    c1 = _import_file(lib, p1)
    c2 = _import_file(lib, p2)
    assert c1 == c2
    video = lib.get_video(c1)
    assert len(video["files"]) == 2


def test_user_value_overrides_ai(lib, tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"data")
    cid = _import_file(lib, p)
    lib.write_ai_result(cid, {"title": "AI 标题", "tags": ["水彩"]}, "v1")
    video = lib.get_video(cid)
    assert video["title"] == "AI 标题"
    lib.update_user_fields(cid, {"user_title": "我的标题"})
    video = lib.get_video(cid)
    assert video["title"] == "我的标题"
    # re-analysis must not clobber the user value
    lib.write_ai_result(cid, {"title": "新 AI 标题"}, "v2")
    video = lib.get_video(cid)
    assert video["title"] == "我的标题"
    assert video["ai_title"] == "新 AI 标题"


def test_missing_file_keeps_records(lib, tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"data")
    cid = _import_file(lib, p)
    lib.update_user_fields(cid, {"user_title": "保留我"})
    missing = lib.mark_missing_files([])  # rescan saw nothing
    assert missing == 1
    video = lib.get_video(cid)
    assert video["title"] == "保留我"
    assert video["files"][0]["status"] == "missing"
    # file reappears
    lib.mark_missing_files([str(p)])
    assert lib.get_video(cid)["files"][0]["status"] == "available"


def test_fts_chinese_search(lib, tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"data")
    cid = _import_file(lib, p)
    lib.write_ai_result(cid, {"summary": "画家示范水彩人物，讲解湿画法"}, "v1")
    lib.replace_transcripts(
        cid,
        [{"start_s": 0.0, "end_s": 5.0, "text": "肤色调配的技巧"}],
        "ok",
    )
    hits = lib.search_videos("湿画法")
    assert [h["content_id"] for h in hits] == [cid]
    hits = lib.search_videos("肤色")
    assert [h["content_id"] for h in hits] == [cid]
    assert lib.search_videos("不存在的词") == []


def test_fts_alias_search(lib, tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"data")
    cid = _import_file(lib, p)
    person = lib.upsert_person("painter", "张大千", aliases=["大千居士"])
    lib.link_person(cid, person, "featured_painter")
    lib.reindex_content(cid)
    hits = lib.search_videos("大千居士")
    assert [h["content_id"] for h in hits] == [cid]


def test_people_video_counts_follow_artist_replacement(lib, tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"data")
    cid = _import_file(lib, p)
    old = lib.upsert_person("painter", "张俊佳")
    new = lib.upsert_person("painter", "一堂")
    lib.link_person(cid, old, "author")
    lib.link_person(cid, old, "featured_painter")
    assert lib.get_person(old)["video_count"] == 1
    lib.set_video_artist(cid, new)
    assert lib.get_person(old)["video_count"] == 0
    assert lib.get_person(new)["video_count"] == 1
    assert lib.list_videos(artist_id=old) == []
    assert [v["content_id"] for v in lib.list_videos(artist_id=new)] == [cid]


def test_related_people_do_not_create_artist_groups(lib, tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"data")
    cid = _import_file(lib, p)
    lib.write_ai_result(cid, {"category": "生活"}, "v1")
    author = lib.upsert_person("painter", "大馨")
    guest = lib.upsert_person("painter", "田田")
    lib.link_person(cid, author, "author")
    lib.link_person(cid, guest, "presenter")
    assert lib.get_person(author)["video_count"] == 1
    assert lib.get_person(guest)["video_count"] == 0
    assert lib.list_videos(artist_id=guest) == []
    assert any(p["id"] == guest and not p["is_attribution"] for p in lib.video_people(cid))
    # A real art introduction remains browsable by the featured painter.
    lib.link_person(cid, guest, "featured_painter")
    assert lib.get_person(guest)["video_count"] == 0
    lib.update_user_fields(cid, {"user_category": "绘画"})
    assert lib.get_person(guest)["video_count"] == 1
    assert len(lib.list_videos(artist_id=guest)) == 1


def test_ai_candidates_replace_stale_links_and_respect_manual_choice(lib, tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"data")
    cid = _import_file(lib, p)
    old = lib.upsert_person("painter", "旧候选")
    new = lib.upsert_person("painter", "新候选")
    lib.link_person(cid, old, "author")
    lib.replace_ai_painters(cid, [(new, "author")])
    assert [p["id"] for p in lib.video_people(cid)] == [new]
    lib.replace_ai_painters(cid, [])
    assert lib.video_people(cid) == []
    lib.set_video_artist(cid, old)
    lib.replace_ai_painters(cid, [(new, "author")])
    assert [p["id"] for p in lib.video_people(cid)] == [old]


def test_people_merge(lib, tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"data")
    cid = _import_file(lib, p)
    a = lib.upsert_person("painter", "张三")
    b = lib.upsert_person("painter", "老张", aliases=["张三丰"])
    lib.link_person(cid, b, "author")
    lib.merge_people(a, b)
    merged = lib.get_person(a)
    assert "老张" in merged["aliases"] and "张三丰" in merged["aliases"]
    assert any(pp["canonical_name"] == "张三" for pp in lib.video_people(cid))


def test_list_videos_filters(lib, tmp_path):
    p1 = tmp_path / "a.mp4"
    p2 = tmp_path / "b.mp4"
    p1.write_bytes(b"1")
    p2.write_bytes(b"22")
    c1 = _import_file(lib, p1, sha="a" * 64)
    c2 = _import_file(lib, p2, sha="b" * 64)
    lib.write_ai_result(c1, {"category": "绘画", "form": "教程"}, "v1")
    lib.write_ai_result(c2, {"category": "vlog"}, "v1")
    lib.update_user_fields(c1, {"user_favorite": True})
    assert [v["content_id"] for v in lib.list_videos(category="绘画")] == [c1]
    assert [v["content_id"] for v in lib.list_videos(favorite=True)] == [c1]
    assert len(lib.list_videos()) == 2


def test_task_queue(lib):
    t1 = lib.enqueue_task("scan", {"folder_id": 1})
    t2 = lib.enqueue_task("analyze", {"content_id": 9})
    # scan runs first
    task = lib.next_task()
    assert task["id"] == t1 and task["type"] == "scan"
    assert lib.next_task()["id"] == t2
    assert lib.next_task() is None
    lib.update_task(t2, status="failed", error="boom")
    lib.requeue_stale_running()
    tasks = {t["id"]: t for t in lib.list_tasks()}
    # the stale 'running' task went back to 'queued'; 'failed' is untouched
    assert tasks[t1]["status"] == "queued"
    assert tasks[t2]["status"] == "failed"


def test_migrations_idempotent(tmp_path):
    from library.db import connect

    conn = connect(tmp_path / "m.db")
    version = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"]
    assert version == "1"
    connect(tmp_path / "m.db").close()
    conn.close()
