"""Tests for FrameExtractor."""

import pytest
from pathlib import Path
from src.video_parser.frame_extractor import (
    FrameExtractor,
    ExtractionStrategy,
    FrameInfo,
)
import tempfile
import numpy as np


@pytest.fixture
def sample_video(tmp_path):
    """Create a sample video file for testing."""
    try:
        import cv2

        # Create a simple test video
        video_path = tmp_path / "test_video.mp4"

        # Video properties
        fps = 30
        width, height = 640, 480
        duration = 3  # seconds
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")

        out = cv2.VideoWriter(
            str(video_path), fourcc, fps, (width, height)
        )

        # Generate frames with changing content
        for i in range(int(fps * duration)):
            # Create a frame with varying content
            frame = np.zeros((height, width, 3), dtype=np.uint8)

            # Add some visual changes
            color_intensity = int((i / (fps * duration)) * 255)
            frame[:, :] = [color_intensity, 100, 150]

            # Add frame number as text
            cv2.putText(
                frame,
                f"Frame {i}",
                (50, 240),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                (255, 255, 255),
                2,
            )

            out.write(frame)

        out.release()

        return video_path

    except ImportError:
        pytest.skip("OpenCV not available")


@pytest.fixture
def extractor():
    """Create a FrameExtractor instance."""
    return FrameExtractor(
        strategy=ExtractionStrategy.INTERVAL,
        interval_seconds=1.0,
        max_frames=10,
    )


def test_extract_by_interval(extractor, sample_video):
    """Test extracting frames at intervals."""
    frames, metadata = extractor.extract_frames(sample_video)

    assert len(frames) > 0
    assert len(frames) <= extractor.max_frames
    assert metadata["fps"] > 0
    assert metadata["duration_seconds"] > 0

    # Check frame info
    for frame in frames:
        assert isinstance(frame, FrameInfo)
        assert frame.timestamp >= 0
        assert frame.width > 0
        assert frame.height > 0


def test_extract_fixed_count(sample_video):
    """Test extracting fixed number of frames."""
    extractor = FrameExtractor(
        strategy=ExtractionStrategy.FIXED_COUNT,
        fixed_count=5,
    )

    frames, _ = extractor.extract_frames(sample_video)

    assert len(frames) <= 5

    # Check that frames are distributed
    timestamps = [f.timestamp for f in frames]
    assert timestamps == sorted(timestamps)


def test_extract_by_scene_detection(sample_video):
    """Test scene detection extraction."""
    extractor = FrameExtractor(
        strategy=ExtractionStrategy.SCENE_DETECTION,
        scene_threshold=20.0,
    )

    frames, _ = extractor.extract_frames(sample_video)

    # Should detect some scene changes
    assert len(frames) > 0


def test_max_frames_limit(sample_video):
    """Test that max_frames limit is respected."""
    extractor = FrameExtractor(
        strategy=ExtractionStrategy.INTERVAL,
        interval_seconds=0.1,  # Very small interval
        max_frames=3,
    )

    frames, _ = extractor.extract_frames(sample_video)

    assert len(frames) <= 3


def test_frame_info_to_dict():
    """Test FrameInfo to_dict conversion."""
    frame_info = FrameInfo(
        timestamp=1.5,
        frame_number=45,
        width=1920,
        height=1080,
    )

    info_dict = frame_info.to_dict()

    assert info_dict["timestamp"] == 1.5
    assert info_dict["frame_number"] == 45
    assert info_dict["width"] == 1920
    assert info_dict["height"] == 1080


def test_extract_nonexistent_video(extractor):
    """Test extracting from non-existent video."""
    with pytest.raises(RuntimeError):
        extractor.extract_frames(Path("/nonexistent/video.mp4"))


def test_save_frame(extractor, sample_video, tmp_path):
    """Test saving a frame to file."""
    frames, _ = extractor.extract_frames(sample_video)

    if frames:
        output_path = tmp_path / "frame.jpg"

        result = extractor.save_frame(sample_video, frames[0], output_path)

        assert result is True
        assert output_path.exists()


def test_metadata_extraction(extractor, sample_video):
    """Test video metadata extraction."""
    _, metadata = extractor.extract_frames(sample_video)

    assert "fps" in metadata
    assert "frame_count" in metadata
    assert "width" in metadata
    assert "height" in metadata
    assert "duration_seconds" in metadata
    assert "codec" in metadata

    assert metadata["fps"] > 0
    assert metadata["frame_count"] > 0
    assert metadata["width"] > 0
    assert metadata["height"] > 0
    assert metadata["duration_seconds"] > 0


def test_extract_frame_data(extractor, sample_video):
    """Test extracting raw frame data."""
    frames, _ = extractor.extract_frames(sample_video)

    if frames:
        frame_data = extractor.extract_frame_data(sample_video, frames[0])

        assert frame_data is not None
        assert len(frame_data) > 0


def test_extract_from_corrupted_video(tmp_path, extractor):
    """Test handling of corrupted video file."""
    # Create a file that's not a valid video
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_text("This is not a video")

    with pytest.raises(RuntimeError):
        extractor.extract_frames(fake_video)


def test_different_strategies(sample_video):
    """Test all extraction strategies."""
    strategies = [
        ExtractionStrategy.INTERVAL,
        ExtractionStrategy.FIXED_COUNT,
        ExtractionStrategy.SCENE_DETECTION,
    ]

    for strategy in strategies:
        extractor = FrameExtractor(strategy=strategy)
        frames, _ = extractor.extract_frames(sample_video)

        assert len(frames) > 0
        assert all(isinstance(f, FrameInfo) for f in frames)
