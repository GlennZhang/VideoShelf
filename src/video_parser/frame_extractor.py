"""Frame extraction from video files using OpenCV."""

from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import logging
import cv2

logger = logging.getLogger(__name__)


class ExtractionStrategy(Enum):
    """Frame extraction strategies."""

    INTERVAL = "interval"  # Extract frames at regular intervals
    FIXED_COUNT = "fixed_count"  # Extract fixed number of frames
    SCENE_DETECTION = "scene_detection"  # Detect scene changes


@dataclass
class FrameInfo:
    """Information about an extracted frame."""

    timestamp: float  # Timestamp in seconds
    frame_number: int
    width: int
    height: int
    data: Optional[bytes] = None  # Raw frame data (optional)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": round(self.timestamp, 2),
            "frame_number": self.frame_number,
            "width": self.width,
            "height": self.height,
        }


class FrameExtractor:
    """Extract frames from video files using OpenCV."""

    def __init__(
        self,
        strategy: ExtractionStrategy = ExtractionStrategy.INTERVAL,
        interval_seconds: float = 10.0,
        fixed_count: int = 8,
        max_frames: int = 20,
        scene_threshold: float = 30.0,
    ):
        """
        Initialize FrameExtractor.

        Args:
            strategy: Extraction strategy to use
            interval_seconds: Interval between frames for INTERVAL strategy
            fixed_count: Number of frames for FIXED_COUNT strategy
            max_frames: Maximum frames to extract regardless of strategy
            scene_threshold: Threshold for scene detection (0-100)
        """
        self.strategy = strategy
        self.interval_seconds = interval_seconds
        self.fixed_count = fixed_count
        self.max_frames = max_frames
        self.scene_threshold = scene_threshold

    def extract_frames(self, video_path: Path) -> Tuple[List[FrameInfo], Dict[str, Any]]:
        """
        Extract frames from a video file.

        Args:
            video_path: Path to video file

        Returns:
            Tuple of (list of FrameInfo, video metadata)

        Raises:
            RuntimeError: If video cannot be opened or processed
        """
        try:
            import cv2
        except ImportError:
            raise RuntimeError("OpenCV (cv2) is required for frame extraction")

        # Open video file
        cap = cv2.VideoCapture(str(video_path))

        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video file: {video_path}")

        try:
            # Get video properties
            metadata = self._get_video_metadata(cap)

            # Extract frames based on strategy
            if self.strategy == ExtractionStrategy.INTERVAL:
                frames = self._extract_by_interval(cap, metadata)
            elif self.strategy == ExtractionStrategy.FIXED_COUNT:
                frames = self._extract_fixed_count(cap, metadata)
            elif self.strategy == ExtractionStrategy.SCENE_DETECTION:
                frames = self._extract_by_scene_detection(cap, metadata)
            else:
                raise ValueError(f"Unknown strategy: {self.strategy}")

            # Limit to max_frames
            frames = frames[: self.max_frames]

            return frames, metadata

        finally:
            cap.release()

    def _get_video_metadata(self, cap) -> Dict[str, Any]:
        """
        Extract video metadata from OpenCV VideoCapture.

        Args:
            cap: OpenCV VideoCapture object

        Returns:
            Dictionary with video metadata
        """
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        duration_seconds = frame_count / fps if fps > 0 else 0

        # Get codec
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        codec = "".join([chr((fourcc >> 8 * i) & 0xFF) for i in range(4)])

        return {
            "fps": round(fps, 2),
            "frame_count": frame_count,
            "width": width,
            "height": height,
            "duration_seconds": round(duration_seconds, 2),
            "codec": codec,
        }

    def _extract_by_interval(
        self, cap, metadata: Dict[str, Any]
    ) -> List[FrameInfo]:
        """
        Extract frames at regular intervals.

        Args:
            cap: OpenCV VideoCapture object
            metadata: Video metadata

        Returns:
            List of FrameInfo objects
        """
        frames = []
        fps = metadata["fps"]
        duration = metadata["duration_seconds"]

        if fps <= 0:
            logger.warning("Invalid FPS, cannot extract by interval")
            return []

        # Calculate frame interval
        frame_interval = int(self.interval_seconds * fps)

        current_frame = 0
        frame_number = 0

        while True:
            # Set frame position
            cap.set(cv2.CAP_PROP_POS_FRAMES, current_frame)

            ret, frame = cap.read()
            if not ret:
                break

            height, width = frame.shape[:2]
            timestamp = current_frame / fps

            frames.append(
                FrameInfo(
                    timestamp=timestamp,
                    frame_number=current_frame,
                    width=width,
                    height=height,
                )
            )

            current_frame += frame_interval
            frame_number += 1

            # Stop if we've exceeded video duration
            if timestamp >= duration:
                break

        return frames

    def _extract_fixed_count(
        self, cap, metadata: Dict[str, Any]
    ) -> List[FrameInfo]:
        """
        Extract fixed number of frames evenly distributed.

        Args:
            cap: OpenCV VideoCapture object
            metadata: Video metadata

        Returns:
            List of FrameInfo objects
        """
        frames = []
        fps = metadata["fps"]
        frame_count = metadata["frame_count"]

        if frame_count <= 0 or fps <= 0:
            logger.warning("Invalid video metadata, cannot extract fixed count")
            return []

        # Calculate frame positions
        frame_positions = []
        for i in range(self.fixed_count):
            position = int((i / self.fixed_count) * frame_count)
            frame_positions.append(position)

        # Extract frames at calculated positions
        for position in frame_positions:
            cap.set(cv2.CAP_PROP_POS_FRAMES, position)

            ret, frame = cap.read()
            if not ret:
                break

            height, width = frame.shape[:2]
            timestamp = position / fps

            frames.append(
                FrameInfo(
                    timestamp=timestamp,
                    frame_number=position,
                    width=width,
                    height=height,
                )
            )

        return frames

    def _extract_by_scene_detection(
        self, cap, metadata: Dict[str, Any]
    ) -> List[FrameInfo]:
        """
        Extract frames based on scene changes.

        Args:
            cap: OpenCV VideoCapture object
            metadata: Video metadata

        Returns:
            List of FrameInfo objects
        """
        try:
            import cv2
        except ImportError:
            raise RuntimeError("OpenCV is required for scene detection")

        frames = []
        fps = metadata["fps"]

        if fps <= 0:
            logger.warning("Invalid FPS, cannot detect scenes")
            return []

        # Read first frame
        ret, frame = cap.read()
        if not ret:
            return []

        # Add first frame
        height, width = frame.shape[:2]
        frames.append(
            FrameInfo(
                timestamp=0.0,
                frame_number=0,
                width=width,
                height=height,
            )
        )

        prev_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        frame_number = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame_number += 1

            # Convert to grayscale for comparison
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # Calculate difference
            diff = cv2.absdiff(prev_gray, gray)
            diff_score = diff.mean()

            # If difference exceeds threshold, it's a new scene
            if diff_score > self.scene_threshold:
                timestamp = frame_number / fps
                height, width = frame.shape[:2]

                frames.append(
                    FrameInfo(
                        timestamp=timestamp,
                        frame_number=frame_number,
                        width=width,
                        height=height,
                    )
                )

                prev_gray = gray

        return frames

    def extract_frame_data(self, video_path: Path, frame_info: FrameInfo) -> Optional[bytes]:
        """
        Extract raw frame data from video.

        Args:
            video_path: Path to video file
            frame_info: FrameInfo object with frame position

        Returns:
            Raw frame data as bytes (in JPEG format) or None
        """
        try:
            import cv2
        except ImportError:
            raise RuntimeError("OpenCV (cv2) is required for frame extraction")

        cap = cv2.VideoCapture(str(video_path))

        if not cap.isOpened():
            return None

        try:
            # Set frame position
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_info.frame_number)

            ret, frame = cap.read()
            if not ret:
                return None

            # Encode as JPEG
            ret, buffer = cv2.imencode(".jpg", frame)
            if not ret:
                return None

            return buffer.tobytes()

        finally:
            cap.release()

    def save_frame(
        self, video_path: Path, frame_info: FrameInfo, output_path: Path
    ) -> bool:
        """
        Save a frame to an image file.

        Args:
            video_path: Path to video file
            frame_info: FrameInfo object
            output_path: Path to save the frame

        Returns:
            True if successful, False otherwise
        """
        try:
            import cv2
        except ImportError:
            raise RuntimeError("OpenCV (cv2) is required for frame extraction")

        cap = cv2.VideoCapture(str(video_path))

        if not cap.isOpened():
            return False

        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_info.frame_number)

            ret, frame = cap.read()
            if not ret:
                return False

            # Save frame
            cv2.imwrite(str(output_path), frame)
            return True

        finally:
            cap.release()
