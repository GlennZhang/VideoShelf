"""Streaming full-file SHA-256 hashing.

Full-file SHA-256 is the content identity (spec/local-video-library.md §3.3);
(size, mtime) is only a rescan optimization handled by the caller.
"""

import hashlib
from pathlib import Path

CHUNK_SIZE = 1024 * 1024  # 1 MiB


def file_sha256(path: Path | str) -> str:
    """Compute the full-file SHA-256 hex digest of ``path`` in chunks.

    Raises:
        OSError: when the file cannot be read (caller marks it missing).
    """
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()
