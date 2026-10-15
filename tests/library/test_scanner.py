"""Scanner tests: discovery, hashing strategy, re-linking, missing marking."""

import pytest

from library import scanner
from library.config import LibraryConfig
from library.scanner import discover_videos, scan_folder


@pytest.fixture()
def cfg(tmp_path):
    return LibraryConfig(data_dir=tmp_path / "data")


def _mkvideo(folder, name, data=b"x"):
    p = folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_discover_case_insensitive(tmp_path):
    _mkvideo(tmp_path, "a.MP4")
    _mkvideo(tmp_path, "b.Mov")
    _mkvideo(tmp_path, "c.mkv")
    _mkvideo(tmp_path, "d.avi")  # not supported in v1
    _mkvideo(tmp_path, ".DS_Store")  # excluded by default patterns
    found = discover_videos(tmp_path, [".DS_Store", "._*"])
    assert sorted(p.name.lower() for p in found) == ["a.mp4", "b.mov", "c.mkv"]


def test_scan_new_file_enqueues_analysis(lib, cfg, tmp_path):
    folder = tmp_path / "lib1"
    folder.mkdir()
    _mkvideo(folder, "v.mp4", b"content-a")
    fid = lib.add_folder(str(folder))
    result = scan_folder(lib, fid, str(folder), cfg)
    assert result.new == 1 and result.scanned == 1 and result.missing == 0
    tasks = lib.list_tasks()
    assert len(tasks) == 1 and tasks[0]["type"] == "analyze"


def test_scan_unchanged_skips_hash(lib, cfg, tmp_path, monkeypatch):
    folder = tmp_path / "lib2"
    folder.mkdir()
    _mkvideo(folder, "v.mp4", b"content-a")
    fid = lib.add_folder(str(folder))
    scan_folder(lib, fid, str(folder), cfg)

    def boom(path):
        raise AssertionError("hash must be skipped for unchanged file")

    monkeypatch.setattr(scanner, "file_sha256", boom)
    result = scan_folder(lib, fid, str(folder), cfg)
    assert result.unchanged == 1 and result.rehashed == 0


def test_scan_modified_file_new_content(lib, cfg, tmp_path):
    folder = tmp_path / "lib3"
    folder.mkdir()
    p = _mkvideo(folder, "v.mp4", b"old")
    fid = lib.add_folder(str(folder))
    scan_folder(lib, fid, str(folder), cfg)
    old_tasks = len(lib.list_tasks())

    p.write_bytes(b"new content")  # same path, different content
    import os

    os.utime(p, ns=(0, p.stat().st_mtime_ns + 10**9))
    result = scan_folder(lib, fid, str(folder), cfg)
    assert result.rehashed == 1
    assert len(lib.list_tasks()) == old_tasks + 1  # requeued analysis


def test_scan_moved_file_relinks(lib, cfg, tmp_path):
    folder = tmp_path / "lib4"
    folder.mkdir()
    p = _mkvideo(folder, "v.mp4", b"same-data")
    fid = lib.add_folder(str(folder))
    scan_folder(lib, fid, str(folder), cfg)
    video = lib.list_videos()[0]
    cid = video["content_id"]

    moved = folder / "sub" / "renamed.mp4"
    moved.parent.mkdir()
    p.rename(moved)
    scan_folder(lib, fid, str(folder), cfg)
    # same hash → same content id; new path available, old path kept missing
    video = lib.get_video(cid)
    by_path = {f["path"]: f["status"] for f in video["files"]}
    assert by_path[str(moved)] == "available"
    assert by_path[str(p)] == "missing"


def test_scan_duplicate_content_two_paths(lib, cfg, tmp_path):
    folder = tmp_path / "lib5"
    folder.mkdir()
    _mkvideo(folder, "a.mp4", b"dup")
    _mkvideo(folder, "nested/b.mp4", b"dup")
    fid = lib.add_folder(str(folder))
    scan_folder(lib, fid, str(folder), cfg)
    videos = lib.list_videos()
    assert len(videos) == 1
    assert len(videos[0]["files"]) == 2


def test_scan_marks_missing_only_in_scope(lib, cfg, tmp_path):
    fa, fb = tmp_path / "fa", tmp_path / "fb"
    fa.mkdir()
    fb.mkdir()
    _mkvideo(fa, "a.mp4", b"a")
    _mkvideo(fb, "b.mp4", b"b")
    id_a, id_b = lib.add_folder(str(fa)), lib.add_folder(str(fb))
    scan_folder(lib, id_a, str(fa), cfg)
    scan_folder(lib, id_b, str(fb), cfg)

    (fa / "a.mp4").unlink()
    scan_folder(lib, id_a, str(fa), cfg)
    va, vb = lib.list_videos()
    statuses = {f["path"]: f["status"] for f in va["files"] + vb["files"]}
    assert statuses[str(fa / "a.mp4")] == "missing"
    assert statuses[str(fb / "b.mp4")] == "available"
