"""Library backup & restore (design.md D7).

Backup = zip of the SQLite snapshot (VACUUM INTO), the screenshots directory
and an analysis-cache manifest. Original videos are never included. Restore
re-creates the data directory; missing files become 'missing' records with
manual data intact. Root rebinding maps old→new roots and re-links by hash
via a rescan.
"""

import json
import logging
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from library.config import LibraryConfig
from library.db import Library

logger = logging.getLogger(__name__)

DB_ENTRY = "library.db"
SCREENSHOTS_ENTRY = "screenshots/"
MANIFEST_ENTRY = "analysis-manifest.json"


def _manifest(cache_dir: Path) -> List[str]:
    if not cache_dir.exists():
        return []
    return sorted(
        str(p.relative_to(cache_dir)) for p in cache_dir.rglob("*") if p.is_file()
    )


def create_backup(cfg: LibraryConfig, out_zip: Optional[Path] = None) -> Path:
    """Create a backup zip; returns its path."""
    out_zip = out_zip or Path(f"library-backup-{datetime.now():%Y%m%d-%H%M%S}.zip")
    lib = Library(cfg.db_path)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "library.db"
            # VACUUM INTO gives a consistent, defragmented copy
            lib._conn.execute("VACUUM INTO ?", (str(snapshot),))
            with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(snapshot, DB_ENTRY)
                if cfg.screenshots_dir.exists():
                    for f in sorted(cfg.screenshots_dir.rglob("*")):
                        if f.is_file():
                            zf.write(
                                f,
                                SCREENSHOTS_ENTRY
                                + str(f.relative_to(cfg.screenshots_dir)),
                            )
                zf.writestr(
                    MANIFEST_ENTRY,
                    json.dumps(
                        _manifest(cfg.analysis_cache_dir),
                        ensure_ascii=False,
                    ),
                )
    finally:
        lib.close()
    logger.info("backup written: %s", out_zip)
    return out_zip


def restore_backup(
    cfg: LibraryConfig,
    zip_path: Path,
    old_root: Optional[str] = None,
    new_root: Optional[str] = None,
) -> dict:
    """Restore data dir from a backup, optionally rebinding roots.

    Returns a summary dict with restored counts. Any running app must be
    stopped before calling this.
    """
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            if DB_ENTRY not in names:
                raise ValueError(f"not a library backup: {zip_path}")
            zf.extractall(tmp)

        if cfg.db_path.exists():
            cfg.db_path.unlink()
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(tmp) / DB_ENTRY, cfg.db_path)
        src_shots = Path(tmp) / "screenshots"
        if src_shots.exists():
            if cfg.screenshots_dir.exists():
                shutil.rmtree(cfg.screenshots_dir)
            shutil.copytree(src_shots, cfg.screenshots_dir)

    lib = Library(cfg.db_path)
    try:
        if old_root and new_root:
            rebind_roots(lib, old_root, new_root)
        # validate file paths: contents without accessible files go missing
        seen = [p for p in lib.list_file_paths() if Path(p).exists()]
        missing = lib.mark_missing_files(seen)
        total = len(lib.list_videos())
        return {"videos": total, "missing_files": missing}
    finally:
        lib.close()


def rebind_roots(lib: Library, old_root: str, new_root: str) -> int:
    """Point file records under ``old_root`` at ``new_root``.

    Returns the number of rebound paths. Nonexistent new paths are still
    recorded — the next rescan re-links by SHA-256 or marks them missing.
    """
    old_root = str(Path(old_root).resolve())
    new_root = str(Path(new_root).expanduser().resolve())
    rebound = 0
    with lib._lock:
        rows = lib._conn.execute("SELECT id, path FROM files").fetchall()
        for row in rows:
            path = row["path"]
            if path.startswith(old_root + "/") or path == old_root:
                new_path = new_root + path[len(old_root) :]
                dupe = lib._conn.execute(
                    "SELECT id FROM files WHERE path=? AND id<>?",
                    (new_path, row["id"]),
                ).fetchone()
                if dupe:
                    # same path already tracked → drop the stale row
                    lib._conn.execute("DELETE FROM files WHERE id=?", (row["id"],))
                else:
                    lib._conn.execute(
                        "UPDATE files SET path=? WHERE id=?",
                        (new_path, row["id"]),
                    )
                rebound += 1
        # folders under the old root move too
        lib._conn.execute(
            "UPDATE folders SET path=REPLACE(path, ?, ?)" " WHERE path LIKE ?",
            (old_root + "/", new_root + "/", old_root + "/%"),
        )
        lib._conn.commit()
    logger.info("rebound %d file paths: %s -> %s", rebound, old_root, new_root)
    return rebound
