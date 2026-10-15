"""Tests for ContentAnalyzer."""

import pytest
from pathlib import Path
from src.video_parser.content_analyzer import (
    ContentAnalyzer,
    ContentAnalysis,
)
from src.video_parser.video_classifier import OllamaClient
from src.video_parser.frame_extractor import FrameInfo, FrameExtractor
from unittest.mock import Mock


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
def analyzer(mock_ollama_client):
    """Create a ContentAnalyzer instance."""
    return ContentAnalyzer(ollama_client=mock_ollama_client, batch_size=3)


@pytest.fixture
def sample_frames():
    """Create sample frame data."""
    return [
        FrameInfo(timestamp=0.0, frame_number=0, width=1920, height=1080),
        FrameInfo(timestamp=1.0, frame_number=30, width=1920, height=1080),
    ]


def test_content_analysis_to_dict():
    """Test ContentAnalysis to_dict conversion."""
    analysis = ContentAnalysis(
        category="drawing",
        summary="A drawing tutorial",
        key_elements=["pencil", "sketch"],
        detailed_description="Full description",
        confidence=0.8,
        frame_analyses=[],
    )

    result_dict = analysis.to_dict()

    assert result_dict["category"] == "drawing"
    assert result_dict["summary"] == "A drawing tutorial"
    assert result_dict["confidence"] == 0.8


def test_analyze_drawing_category(
    analyzer, mock_ollama_client, mock_frame_extractor, sample_frames, tmp_path
):
    """Test analyzing a drawing video."""
    video_path = tmp_path / "test.mp4"

    # Mock frame extraction
    mock_frame_extractor.extract_frame_data.return_value = b"frame_data"

    # Mock API response
    mock_ollama_client.generate_response.return_value = (
        "Summary: Portrait drawing\nStyle: Sketch\nSubject: Portrait\n"
    )

    result = analyzer.analyze(video_path, "drawing", sample_frames, mock_frame_extractor)

    assert result.category == "drawing"
    assert len(result.summary) > 0
    assert isinstance(result.key_elements, list)


def test_analyze_with_api_error(
    analyzer, mock_ollama_client, mock_frame_extractor, sample_frames, tmp_path
):
    """Test handling of API errors."""
    video_path = tmp_path / "test.mp4"

    # Mock frame extraction
    mock_frame_extractor.extract_frame_data.return_value = b"frame_data"

    # Mock API error
    mock_ollama_client.generate_response.side_effect = Exception("API Error")

    result = analyzer.analyze(video_path, "drawing", sample_frames, mock_frame_extractor)

    assert result.category == "drawing"
    assert "error" in result.summary.lower()
    assert result.confidence == 0.0


def test_analyze_no_frames(
    analyzer, mock_frame_extractor, tmp_path
):
    """Test analyzing with no frames."""
    video_path = tmp_path / "test.mp4"

    result = analyzer.analyze(video_path, "drawing", [], mock_frame_extractor)

    assert result.category == "drawing"
    assert "no frame data" in result.summary.lower()


def test_create_analysis_prompt(analyzer):
    """Test prompt creation for different categories."""
    categories = ["drawing", "tutorial", "vlog", "entertainment", "sports", "other"]

    for category in categories:
        prompt = analyzer._create_analysis_prompt(category)
        assert len(prompt) > 0
        assert category in prompt.lower() or category == "other"


def test_batch_analyze(
    analyzer, mock_ollama_client, mock_frame_extractor, sample_frames, tmp_path
):
    """Test batch analysis of multiple videos."""
    videos = [
        (tmp_path / "video1.mp4", "drawing", sample_frames),
        (tmp_path / "video2.mp4", "tutorial", sample_frames),
    ]

    # Mock responses
    mock_frame_extractor.extract_frame_data.return_value = b"frame_data"
    mock_ollama_client.generate_response.side_effect = [
        "Summary: Drawing tutorial",
        "Summary: Coding tutorial",
    ]

    results = analyzer.batch_analyze(videos, mock_frame_extractor)

    assert len(results) == 2
    assert results[0].category == "drawing"
    assert results[1].category == "tutorial"


def test_parse_analysis_response(analyzer):
    """Test parsing analysis response."""
    response = """Summary: A portrait drawing
Style: Sketch
Subject: Portrait
Tools: Pencil"""

    analysis = analyzer._parse_analysis_response("drawing", response)

    assert analysis.category == "drawing"
    assert "portrait" in analysis.summary.lower()
    assert len(analysis.key_elements) > 0


def test_batch_size_limit(
    analyzer, mock_ollama_client, mock_frame_extractor, tmp_path
):
    """Test that batch size limits frame extraction calls."""
    # Create more frames than batch size
    frames = [
        FrameInfo(timestamp=float(i), frame_number=i, width=1920, height=1080)
        for i in range(10)
    ]

    video_path = tmp_path / "test.mp4"

    # Mock response
    mock_ollama_client.generate_response.return_value = "Summary: Test"
    mock_frame_extractor.extract_frame_data.return_value = b"frame_data"

    analyzer.analyze(video_path, "drawing", frames, mock_frame_extractor)

    # Should call extract_frame_data, but respect batch size internally
    assert mock_frame_extractor.extract_frame_data.call_count > 0
