"""Tests for VideoScanner."""

import pytest
from pathlib import Path
from src.video_parser.video_scanner import VideoScanner, VideoInfo
import tempfile
import os


@pytest.fixture
def temp_video_dir(tmp_path):
    """Create a temporary directory with dummy video files."""
    # Create some dummy files with video extensions
    video_files = ["video1.mp4", "video2.avi", "video3.mov", "not_a_video.txt"]

    for filename in video_files:
        file_path = tmp_path / filename
        # Create file with some content
        file_path.write_text("dummy video content")

    # Create a subdirectory with nested video
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "nested_video.mkv").write_text("nested content")

    return tmp_path


@pytest.fixture
def scanner():
    """Create a VideoScanner instance."""
    return VideoScanner(
        supported_formats=[".mp4", ".avi", ".mov", ".mkv", ".webm"],
        recursive=True,
    )


def test_is_video_file(scanner, temp_video_dir):
    """Test video file detection."""
    # Test valid video files
    assert scanner.is_video_file(temp_video_dir / "video1.mp4") is True
    assert scanner.is_video_file(temp_video_dir / "video2.avi") is True
    assert scanner.is_video_file(temp_video_dir / "video3.mov") is True

    # Test non-video file
    assert scanner.is_video_file(temp_video_dir / "not_a_video.txt") is False


def test_scan_directory(scanner, temp_video_dir):
    """Test scanning a directory."""
    videos = scanner.scan_directory(temp_video_dir)

    # Should find all video files including nested ones
    assert len(videos) == 4  # 3 in root + 1 in subdir

    # Check that all found files are videos
    for video in videos:
        assert isinstance(video, VideoInfo)
        assert video.format in ["mp4", "avi", "mov", "mkv"]


def test_scan_non_recursive(tmp_path):
    """Test scanning without recursion."""
    scanner = VideoScanner(recursive=False)

    # Create nested structure
    (tmp_path / "root.mp4").write_text("root")
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "nested.mp4").write_text("nested")

    videos = scanner.scan_directory(tmp_path)

    # Should only find root video
    assert len(videos) == 1
    assert videos[0].path.name == "root.mp4"


def test_scan_single_file(scanner, temp_video_dir):
    """Test scanning a single file."""
    video_info = scanner.scan_single_file(temp_video_dir / "video1.mp4")

    assert video_info is not None
    assert video_info.path.name == "video1.mp4"
    assert video_info.format == "mp4"
    assert video_info.size_mb > 0


def test_scan_single_file_not_video(scanner, temp_video_dir):
    """Test scanning a non-video file."""
    video_info = scanner.scan_single_file(temp_video_dir / "not_a_video.txt")

    assert video_info is None


def test_file_size_filter(tmp_path):
    """Test file size filtering."""
    scanner = VideoScanner(max_file_size_mb=1)

    # Create small file
    small_file = tmp_path / "small.mp4"
    small_file.write_text("x" * 100)  # Very small

    # Create large file (simulate by writing more data)
    large_file = tmp_path / "large.mp4"
    large_file.write_text("x" * (2 * 1024 * 1024))  # 2 MB

    videos = scanner.scan_directory(tmp_path)

    # Should only include small file
    assert len(videos) == 1
    assert videos[0].path.name == "small.mp4"


def test_filter_by_format(scanner, temp_video_dir):
    """Test filtering videos by format."""
    videos = scanner.scan_directory(temp_video_dir)

    mp4_videos = scanner.filter_by_format(videos, "mp4")

    assert all(v.format == "mp4" for v in mp4_videos)
    assert len(mp4_videos) == 1


def test_sort_by_size(scanner, temp_video_dir):
    """Test sorting videos by size."""
    videos = scanner.scan_directory(temp_video_dir)

    sorted_videos = scanner.sort_by_size(videos, descending=True)

    # Check that sizes are in descending order
    sizes = [v.size_mb for v in sorted_videos]
    assert sizes == sorted(sizes, reverse=True)


def test_get_summary_stats(scanner, temp_video_dir):
    """Test getting summary statistics."""
    videos = scanner.scan_directory(temp_video_dir)

    stats = scanner.get_summary_stats(videos)

    assert stats["total_count"] == 4
    assert stats["total_size_mb"] > 0
    assert "mp4" in stats["formats"]
    assert stats["average_size_mb"] > 0


def test_get_summary_stats_empty(scanner):
    """Test summary stats with empty list."""
    stats = scanner.get_summary_stats([])

    assert stats["total_count"] == 0
    assert stats["total_size_mb"] == 0
    assert stats["average_size_mb"] == 0


def test_get_total_size(scanner, temp_video_dir):
    """Test calculating total size."""
    videos = scanner.scan_directory(temp_video_dir)

    total_size = scanner.get_total_size(videos)

    assert total_size > 0
    assert total_size == sum(v.size_mb for v in videos)


def test_scan_nonexistent_directory(scanner):
    """Test scanning a directory that doesn't exist."""
    with pytest.raises(FileNotFoundError):
        scanner.scan_directory(Path("/nonexistent/directory"))


def test_scan_file_not_directory(scanner, tmp_path):
    """Test scanning a file instead of directory."""
    file_path = tmp_path / "file.txt"
    file_path.write_text("test")

    with pytest.raises(NotADirectoryError):
        scanner.scan_directory(file_path)


def test_video_info_to_dict(temp_video_dir):
    """Test VideoInfo to_dict conversion."""
    file_path = temp_video_dir / "test.mp4"
    file_path.write_text("content")

    video_info = VideoInfo(
        path=file_path,
        size_mb=1.5,
        format="mp4",
        duration_seconds=120.5,
    )

    info_dict = video_info.to_dict()

    assert info_dict["size_mb"] == 1.5
    assert info_dict["format"] == "mp4"
    assert info_dict["duration_seconds"] == 120.5
    assert "path" in info_dict
