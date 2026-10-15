"""Shared fixtures for library tests."""

import subprocess
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from library.db import Library  # noqa: E402


@pytest.fixture()
def lib(tmp_path):
    """A fresh Library backed by a temp database."""
    library = Library(tmp_path / "library.db")
    yield library
    library.close()


@pytest.fixture()
def tiny_video(tmp_path):
    """Create a 2-second MP4 via ffmpeg (testsrc + silent audio).

    Skips the test when ffmpeg is unavailable.
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    out = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=320x240:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            str(out),
        ],
        check=True,
    )
    return out
