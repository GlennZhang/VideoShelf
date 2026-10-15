"""Folder scanning: discover, hash and link video files.

Rescan strategy (design.md D3): (size, mtime_ns) unchanged ⇒ skip hashing;
otherwise re-hash — same hash re-links existing content (move/duplicate),
new hash creates new content and requeues analysis. Inaccessible files are
marked missing, never deleted.
"""

import logging
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, List

from library.config import SUPPORTED_EXTENSIONS, LibraryConfig
from library.db import Library
from library.hashing import file_sha256

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Outcome of one folder scan."""

    folder_id: int
    scanned: int = 0
    new: int = 0
    rehashed: int = 0
    unchanged: int = 0
    modified: int = 0
    missing: int = 0
    errors: List[str] = field(default_factory=list)
    new_content_ids: List[int] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"scanned={self.scanned} new={self.new} rehashed={self.rehashed}"
            f" unchanged={self.unchanged} modified={self.modified}"
            f" missing={self.missing} errors={len(self.errors)}"
        )


def _excluded(name: str, patterns: List[str]) -> bool:
    return any(fnmatch(name, p) for p in patterns)


def discover_videos(folder: Path, excludes: List[str]) -> List[Path]:
    """Recursively list supported video files under ``folder``."""
    found: List[Path] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        if _excluded(path.name, excludes):
            continue
        if path.suffix.lower() in SUPPORTED_EXTENSIONS:
            found.append(path)
    return found


def scan_folder(
    lib: Library,
    folder_id: int,
    folder_path: str,
    cfg: LibraryConfig,
    enqueue_analysis: bool = True,
) -> ScanResult:
    """Scan one registered folder and update the database.

    Files whose content is new or changed get an 'analyze' task enqueued when
    ``enqueue_analysis`` is set (the worker calls with False to avoid loops).
    """
    result = ScanResult(folder_id=folder_id)
    folder = Path(folder_path)
    if not folder.is_dir():
        result.errors.append(f"folder not accessible: {folder_path}")
        result.missing = lib.mark_missing_files([], folder_id=folder_id)
        return result

    seen: List[str] = []
    for path in discover_videos(folder, cfg.scan_excludes):
        result.scanned += 1
        try:
            stat = path.stat()
        except OSError as exc:
            result.errors.append(f"{path}: {exc}")
            continue
        seen.append(str(path))

        existing = lib.find_file_by_path(str(path))
        if (
            existing is not None
            and existing["size_bytes"] == stat.st_size
            and existing["mtime_ns"] == stat.st_mtime_ns
            and existing["status"] == "available"
        ):
            result.unchanged += 1
            continue

        try:
            digest = file_sha256(path)
        except OSError as exc:
            result.errors.append(f"{path}: hash failed: {exc}")
            continue
        result.rehashed += 1

        content = lib.find_content_by_hash(digest)
        is_new_content = content is None
        content_id = lib.upsert_content(digest, stat.st_size)
        lib.upsert_file(
            content_id, str(path), folder_id, stat.st_size, stat.st_mtime_ns
        )

        if is_new_content:
            result.new += 1
            result.new_content_ids.append(content_id)
        elif existing is not None and existing["content_id"] != content_id:
            # same path now holds different content → old analysis is stale
            result.modified += 1
        else:
            # re-linked to existing content (file moved/duplicated here)
            result.new += 1
        if is_new_content or (
            existing is not None and existing["content_id"] != content_id
        ):
            if enqueue_analysis:
                lib.enqueue_task("analyze", {"content_id": content_id})
            lib.set_analysis_status(content_id, "pending")

    result.missing = lib.mark_missing_files(seen, folder_id=folder_id)
    lib.record_scan(folder_id, result.summary())
    logger.info("scan folder %s: %s", folder_path, result.summary())
    return result


def scan_all_folders(
    lib: Library, cfg: LibraryConfig, enqueue_analysis: bool = True
) -> Dict[int, ScanResult]:
    """Rescan every registered folder."""
    results: Dict[int, ScanResult] = {}
    for folder in lib.list_folders():
        results[folder["id"]] = scan_folder(
            lib,
            folder["id"],
            folder["path"],
            cfg,
            enqueue_analysis=enqueue_analysis,
        )
    return results
