"""Video scanning functionality for discovering video files."""

import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import mimetypes


@dataclass
class VideoInfo:
    """Information about a video file."""

    path: Path
    size_mb: float
    format: str
    duration_seconds: Optional[float] = None
    codec: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    frame_rate: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "path": str(self.path),
            "size_mb": round(self.size_mb, 2),
            "format": self.format,
            "duration_seconds": self.duration_seconds,
            "codec": self.codec,
            "width": self.width,
            "height": self.height,
            "frame_rate": self.frame_rate,
        }


class VideoScanner:
    """Scans directories for video files."""

    def __init__(
        self,
        supported_formats: Optional[List[str]] = None,
        recursive: bool = True,
        max_file_size_mb: int = 5000,
        min_duration_seconds: int = 5,
    ):
        """
        Initialize VideoScanner.

        Args:
            supported_formats: List of supported video file extensions
            recursive: Whether to scan directories recursively
            max_file_size_mb: Maximum file size in MB
            min_duration_seconds: Minimum video duration in seconds
        """
        self.supported_formats = supported_formats or [
            ".mp4",
            ".avi",
            ".mov",
            ".mkv",
            ".webm",
        ]
        self.recursive = recursive
        self.max_file_size_mb = max_file_size_mb
        self.min_duration_seconds = min_duration_seconds

    def is_video_file(self, file_path: Path) -> bool:
        """
        Check if a file is a supported video file.

        Args:
            file_path: Path to the file

        Returns:
            True if file is a supported video
        """
        # Check extension
        if file_path.suffix.lower() not in self.supported_formats:
            return False

        # Check if it's actually a file (not a directory)
        if not file_path.is_file():
            return False

        # Optional: Check MIME type
        mime_type, _ = mimetypes.guess_type(str(file_path))
        if mime_type and not mime_type.startswith("video/"):
            return False

        return True

    def scan_directory(self, directory: Path) -> List[VideoInfo]:
        """
        Scan a directory for video files.

        Args:
            directory: Directory path to scan

        Returns:
            List of VideoInfo objects
        """
        if not directory.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")

        if not directory.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {directory}")

        video_files = []

        # Walk through directory
        if self.recursive:
            entries = directory.rglob("*")
        else:
            entries = directory.glob("*")

        for entry in entries:
            if self.is_video_file(entry):
                try:
                    video_info = self._get_video_info(entry)
                    if self._should_include_video(video_info):
                        video_files.append(video_info)
                except Exception as e:
                    # Skip files that can't be read
                    continue

        return video_files

    def _get_video_info(self, file_path: Path) -> VideoInfo:
        """
        Get basic information about a video file.

        Args:
            file_path: Path to the video file

        Returns:
            VideoInfo object
        """
        # Get file size
        size_bytes = file_path.stat().st_size
        size_mb = size_bytes / (1024 * 1024)

        # Get format from extension
        format_str = file_path.suffix.lower().lstrip(".")

        # Basic VideoInfo (without duration/codec - requires OpenCV)
        return VideoInfo(
            path=file_path,
            size_mb=round(size_mb, 2),
            format=format_str,
        )

    def _should_include_video(self, video_info: VideoInfo) -> bool:
        """
        Check if video should be included based on filters.

        Args:
            video_info: VideoInfo object

        Returns:
            True if video passes all filters
        """
        # Check file size
        if video_info.size_mb > self.max_file_size_mb:
            return False

        # Check duration if available
        if (
            video_info.duration_seconds is not None
            and video_info.duration_seconds < self.min_duration_seconds
        ):
            return False

        return True

    def scan_single_file(self, file_path: Path) -> Optional[VideoInfo]:
        """
        Scan a single video file.

        Args:
            file_path: Path to the video file

        Returns:
            VideoInfo object or None if not a valid video
        """
        if not self.is_video_file(file_path):
            return None

        try:
            video_info = self._get_video_info(file_path)
            if self._should_include_video(video_info):
                return video_info
        except Exception:
            pass

        return None

    def get_total_size(self, videos: List[VideoInfo]) -> float:
        """
        Calculate total size of video files.

        Args:
            videos: List of VideoInfo objects

        Returns:
            Total size in MB
        """
        return sum(video.size_mb for video in videos)

    def filter_by_format(self, videos: List[VideoInfo], format_str: str) -> List[VideoInfo]:
        """
        Filter videos by format.

        Args:
            videos: List of VideoInfo objects
            format_str: Format to filter by (e.g., 'mp4')

        Returns:
            Filtered list of VideoInfo objects
        """
        format_str = format_str.lower().lstrip(".")
        return [v for v in videos if v.format == format_str]

    def sort_by_size(self, videos: List[VideoInfo], descending: bool = True) -> List[VideoInfo]:
        """
        Sort videos by file size.

        Args:
            videos: List of VideoInfo objects
            descending: Sort in descending order if True

        Returns:
            Sorted list of VideoInfo objects
        """
        return sorted(videos, key=lambda v: v.size_mb, reverse=descending)

    def get_summary_stats(self, videos: List[VideoInfo]) -> Dict[str, Any]:
        """
        Get summary statistics for a list of videos.

        Args:
            videos: List of VideoInfo objects

        Returns:
            Dictionary with summary statistics
        """
        if not videos:
            return {
                "total_count": 0,
                "total_size_mb": 0,
                "formats": {},
                "average_size_mb": 0,
            }

        format_counts = {}
        for video in videos:
            format_counts[video.format] = format_counts.get(video.format, 0) + 1

        return {
            "total_count": len(videos),
            "total_size_mb": round(self.get_total_size(videos), 2),
            "formats": format_counts,
            "average_size_mb": round(
                sum(v.size_mb for v in videos) / len(videos), 2
            ),
        }
