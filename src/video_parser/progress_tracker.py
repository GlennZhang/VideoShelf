"""Progress tracking for video processing."""

import time
import logging
from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

logger = logging.getLogger(__name__)


@dataclass
class ProgressStats:
    """Statistics about processing progress."""

    total_videos: int
    processed_videos: int
    failed_videos: int
    start_time: datetime
    errors: List[str]

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.total_videos == 0:
            return 0.0
        return (self.processed_videos / self.total_videos) * 100

    @property
    def elapsed_time(self) -> timedelta:
        """Calculate elapsed time."""
        return datetime.now() - self.start_time

    @property
    def estimated_remaining(self) -> Optional[timedelta]:
        """Estimate remaining time."""
        if self.processed_videos == 0:
            return None

        avg_time_per_video = self.elapsed_time.total_seconds() / self.processed_videos
        remaining_videos = self.total_videos - self.processed_videos
        remaining_seconds = avg_time_per_video * remaining_videos

        return timedelta(seconds=remaining_seconds)


class ProgressTracker:
    """Track and display progress of video processing."""

    def __init__(self, show_progress_bars: bool = True):
        """
        Initialize ProgressTracker.

        Args:
            show_progress_bars: Whether to show progress bars
        """
        self.show_progress_bars = show_progress_bars and tqdm is not None
        self.stats: Optional[ProgressStats] = None
        self.progress_bar: Optional[tqdm] = None

    def start(self, total_videos: int) -> None:
        """
        Start tracking progress.

        Args:
            total_videos: Total number of videos to process
        """
        self.stats = ProgressStats(
            total_videos=total_videos,
            processed_videos=0,
            failed_videos=0,
            start_time=datetime.now(),
            errors=[],
        )

        if self.show_progress_bars:
            self.progress_bar = tqdm(
                total=total_videos,
                desc="Processing videos",
                unit="video",
            )

    def update(self, success: bool = True, error: Optional[str] = None) -> None:
        """
        Update progress after processing a video.

        Args:
            success: Whether processing succeeded
            error: Error message if failed
        """
        if self.stats is None:
            return

        if success:
            self.stats.processed_videos += 1
        else:
            self.stats.failed_videos += 1
            if error:
                self.stats.errors.append(error)

        if self.progress_bar:
            self.progress_bar.update(1)

            # Update description with stats
            elapsed = self.stats.elapsed_time.total_seconds()
            rate = self.stats.processed_videos / elapsed if elapsed > 0 else 0
            self.progress_bar.set_postfix(
                {
                    "success": self.stats.processed_videos,
                    "failed": self.stats.failed_videos,
                    "rate": f"{rate:.2f} v/s",
                }
            )

    def finish(self) -> None:
        """Finish tracking progress."""
        if self.progress_bar:
            self.progress_bar.close()
            self.progress_bar = None

        if self.stats:
            self._log_summary()

    def _log_summary(self) -> None:
        """Log processing summary."""
        logger.info("=" * 50)
        logger.info("Processing Summary")
        logger.info("=" * 50)
        logger.info(f"Total videos: {self.stats.total_videos}")
        logger.info(f"Successfully processed: {self.stats.processed_videos}")
        logger.info(f"Failed: {self.stats.failed_videos}")
        logger.info(f"Success rate: {self.stats.success_rate:.1f}%")
        logger.info(f"Elapsed time: {self.stats.elapsed_time}")

        if self.stats.estimated_remaining:
            logger.info(f"Estimated remaining: {self.stats.estimated_remaining}")

        if self.stats.errors:
            logger.info(f"\nErrors encountered: {len(self.stats.errors)}")
            for error in self.stats.errors[:10]:  # Show first 10 errors
                logger.error(f"  - {error}")
            if len(self.stats.errors) > 10:
                logger.info(f"  ... and {len(self.stats.errors) - 10} more errors")

        logger.info("=" * 50)

    def get_stats(self) -> Optional[ProgressStats]:
        """Get current progress statistics."""
        return self.stats
