"""Backup / restore / root-rebind tests."""

from library.backup import create_backup, restore_backup
from library.config import LibraryConfig
from library.scanner import scan_folder


def _seed(tmp_path, video_bytes=b"video-data"):
    cfg = LibraryConfig(data_dir=tmp_path / "data")
    media = tmp_path / "media"
    media.mkdir()
    (media / "a.mp4").write_bytes(video_bytes)
    from library.db import Library

    lib = Library(cfg.db_path)
    fid = lib.add_folder(str(media))
    scan_folder(lib, fid, str(media), cfg, enqueue_analysis=False)
    cid = lib.list_videos()[0]["content_id"]
    lib.update_user_fields(cid, {"user_title": "人工标题"})
    lib.add_screenshot(
        cid,
        str(cfg.screenshots_dir / "x.jpg"),
        1.5,
        is_representative=True,
        chosen_by="user",
    )
    cfg.screenshots_dir.mkdir(parents=True, exist_ok=True)
    (cfg.screenshots_dir / "x.jpg").write_bytes(b"jpeg")
    lib.close()
    return cfg, media


def test_backup_restore_roundtrip(tmp_path):
    cfg, media = _seed(tmp_path)
    zip_path = create_backup(cfg, tmp_path / "bk.zip")
    assert zip_path.exists() and zip_path.stat().st_size > 0

    # wipe the data dir, then restore
    import shutil

    shutil.rmtree(cfg.data_dir)
    summary = restore_backup(cfg, zip_path)
    assert summary["videos"] == 1
    assert (cfg.screenshots_dir / "x.jpg").exists()

    from library.db import Library

    lib = Library(cfg.db_path)
    video = lib.list_videos()[0]
    assert video["title"] == "人工标题"  # manual data survived
    lib.close()


def test_restore_marks_missing(tmp_path):
    cfg, media = _seed(tmp_path)
    zip_path = create_backup(cfg, tmp_path / "bk.zip")
    (media / "a.mp4").unlink()  # file gone before restore
    summary = restore_backup(cfg, zip_path)
    assert summary["missing_files"] == 1

    from library.db import Library

    lib = Library(cfg.db_path)
    video = lib.list_videos()[0]
    assert video["title"] == "人工标题"
    assert video["files"][0]["status"] == "missing"
    lib.close()


def test_root_rebind(tmp_path):
    cfg, media = _seed(tmp_path)
    zip_path = create_backup(cfg, tmp_path / "bk.zip")

    # "move" the media to a new root and restore with rebinding
    new_media = tmp_path / "elsewhere"
    new_media.mkdir()
    (new_media / "a.mp4").write_bytes(b"video-data")
    import shutil

    shutil.rmtree(cfg.data_dir)
    summary = restore_backup(
        cfg, zip_path, old_root=str(media), new_root=str(new_media)
    )
    assert summary["missing_files"] == 0

    from library.db import Library

    lib = Library(cfg.db_path)
    video = lib.list_videos()[0]
    assert video["files"][0]["path"] == str(new_media / "a.mp4")
    assert video["files"][0]["status"] == "available"
    assert video["title"] == "人工标题"
    lib.close()
