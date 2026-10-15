"""Tests for VideoClassifier."""

import pytest
from pathlib import Path
from src.video_parser.video_classifier import (
    VideoClassifier,
    OllamaClient,
    ClassificationResult,
)
from src.video_parser.frame_extractor import FrameInfo, FrameExtractor, ExtractionStrategy
from unittest.mock import Mock, MagicMock


@pytest.fixture
def mock_ollama_client():
    """Create a mock OllamaClient."""
    client = Mock(spec=OllamaClient)
    return client


@pytest.fixture
def mock_frame_extractor():
    """Create a mock FrameExtractor."""
    extractor = Mock(spec=FrameExtractor)
    return extractor


@pytest.fixture
def sample_frames():
    """Create sample frame data."""
    return [
        FrameInfo(timestamp=0.0, frame_number=0, width=1920, height=1080),
        FrameInfo(timestamp=1.0, frame_number=30, width=1920, height=1080),
        FrameInfo(timestamp=2.0, frame_number=60, width=1920, height=1080),
    ]


@pytest.fixture
def classifier(mock_ollama_client):
    """Create a VideoClassifier instance."""
    return VideoClassifier(
        ollama_client=mock_ollama_client,
        confidence_threshold=0.6,
        max_frames_for_classification=5,
    )


def test_ollama_client_init():
    """Test OllamaClient initialization."""
    try:
        client = OllamaClient(
            model_name="qwen3-vl:8b",
            timeout=30,
            max_retries=3,
        )
        assert client.model_name == "qwen3-vl:8b"
        assert client.timeout == 30
        assert client.max_retries == 3
    except ImportError:
        pytest.skip("Ollama not installed")


def test_ollama_client_is_model_available():
    """Test checking model availability."""
    try:
        client = OllamaClient()
        # This will fail if Ollama is not running, but we can test the method exists
        assert hasattr(client, "is_model_available")
    except ImportError:
        pytest.skip("Ollama not installed")


def test_classification_result_to_dict():
    """Test ClassificationResult to_dict conversion."""
    result = ClassificationResult(
        category="drawing",
        confidence=0.85,
        description="A drawing tutorial video",
        raw_response="Category: drawing",
    )

    result_dict = result.to_dict()

    assert result_dict["category"] == "drawing"
    assert result_dict["confidence"] == 0.85
    assert result_dict["description"] == "A drawing tutorial video"
    assert "raw_response" in result_dict


def test_parse_classification_response(classifier):
    """Test parsing classification response."""
    response = """Category: drawing
Confidence: high
Description: This video shows someone creating artwork"""

    category, confidence, description = classifier._parse_classification_response(response)

    assert category == "drawing"
    assert confidence > 0.7  # "high" should give high confidence
    assert "artwork" in description.lower()


def test_parse_classification_response_low_confidence(classifier):
    """Test parsing response with low confidence."""
    response = """Category: other
Confidence: low
Description: Not sure what this is"""

    category, confidence, description = classifier._parse_classification_response(response)

    assert category == "other"
    assert confidence < 0.5  # "low" should give low confidence


def test_parse_classification_response_category_detection(classifier):
    """Test category detection in response."""
    # Test with "tutorial" category
    response = "This looks like a tutorial video teaching something"
    category, _, _ = classifier._parse_classification_response(response)
    assert category == "tutorial"

    # Test with "vlog" category
    response = "Appears to be a vlog about daily life"
    category, _, _ = classifier._parse_classification_response(response)
    assert category == "vlog"


def test_classify_no_frames(classifier, mock_frame_extractor):
    """Test classification with no frames."""
    video_path = Path("/fake/video.mp4")

    result = classifier.classify(video_path, [], mock_frame_extractor)

    assert result.category == "other"
    assert result.confidence == 0.0
    assert "no frames" in result.description.lower()


def test_classify_with_mock_response(
    classifier, mock_ollama_client, mock_frame_extractor, sample_frames, tmp_path
):
    """Test classification with mocked API response."""
    video_path = tmp_path / "test.mp4"

    # Mock frame data extraction
    mock_frame_extractor.extract_frame_data.return_value = b"fake_frame_data"

    # Mock API response
    mock_ollama_client.generate_response.return_value = (
        "Category: drawing\nConfidence: high\nDescription: Art tutorial"
    )

    result = classifier.classify(video_path, sample_frames, mock_frame_extractor)

    assert result.category == "drawing"
    assert result.confidence > 0.7
    assert "art" in result.description.lower()


def test_classify_api_error(
    classifier, mock_ollama_client, mock_frame_extractor, sample_frames, tmp_path
):
    """Test classification handling API errors."""
    video_path = tmp_path / "test.mp4"

    # Mock frame data extraction
    mock_frame_extractor.extract_frame_data.return_value = b"fake_frame_data"

    # Mock API error
    mock_ollama_client.generate_response.side_effect = Exception("API Error")

    result = classifier.classify(video_path, sample_frames, mock_frame_extractor)

    assert result.category == "other"
    assert result.confidence == 0.0
    assert "error" in result.description.lower()


def test_batch_classify(
    classifier, mock_ollama_client, mock_frame_extractor, sample_frames, tmp_path
):
    """Test batch classification of multiple videos."""
    videos = [
        (tmp_path / "video1.mp4", sample_frames),
        (tmp_path / "video2.mp4", sample_frames),
    ]

    # Mock frame data extraction
    mock_frame_extractor.extract_frame_data.return_value = b"fake_frame_data"

    # Mock API responses
    mock_ollama_client.generate_response.side_effect = [
        "Category: drawing\nConfidence: high",
        "Category: tutorial\nConfidence: medium",
    ]

    results = classifier.batch_classify(videos, mock_frame_extractor)

    assert len(results) == 2
    assert results[0].category == "drawing"
    assert results[1].category == "tutorial"


def test_get_category_stats(classifier):
    """Test getting category statistics."""
    results = [
        ClassificationResult("drawing", 0.8, "desc1", ""),
        ClassificationResult("drawing", 0.6, "desc2", ""),
        ClassificationResult("tutorial", 0.9, "desc3", ""),
        ClassificationResult("other", 0.5, "desc4", ""),
    ]

    stats = classifier.get_category_stats(results)

    assert stats["drawing"]["count"] == 2
    assert stats["drawing"]["avg_confidence"] == 0.7
    assert stats["tutorial"]["count"] == 1
    assert stats["other"]["count"] == 1


def test_max_frames_limit(classifier, mock_ollama_client, mock_frame_extractor, tmp_path):
    """Test that max_frames_for_classification is respected."""
    # Create more frames than the limit
    frames = [
        FrameInfo(timestamp=float(i), frame_number=i, width=1920, height=1080)
        for i in range(10)
    ]

    video_path = tmp_path / "test.mp4"

    # Mock responses
    mock_ollama_client.generate_response.return_value = "Category: other"
    mock_frame_extractor.extract_frame_data.return_value = b"frame_data"

    result = classifier.classify(video_path, frames, mock_frame_extractor)

    # Should only call extract_frame_data for max_frames (5)
    assert mock_frame_extractor.extract_frame_data.call_count <= 5


def test_create_classification_prompt(classifier):
    """Test classification prompt creation."""
    prompt = classifier._create_classification_prompt()

    assert "drawing" in prompt
    assert "tutorial" in prompt
    assert "vlog" in prompt
    assert "entertainment" in prompt
    assert "sports" in prompt
    assert "other" in prompt
    assert "Category:" in prompt
    assert "Confidence:" in prompt
    assert "Description:" in prompt
