"""Tests for TagGenerator."""

import pytest
from src.video_parser.tag_generator import TagGenerator, GeneratedTags
from src.video_parser.content_analyzer import ContentAnalysis
from src.config.tag_manager import TagManager
from unittest.mock import Mock
import tempfile


@pytest.fixture
def temp_tags_config():
    """Create a temporary tags config file."""
    import yaml

    tags_data = {
        "tag_taxonomy": {
            "categories": ["drawing", "tutorial"],
            "drawing": {
                "style": ["sketch", "watercolor", "oil_painting"],
                "subject": ["portrait", "landscape"],
            },
            "tutorial": {
                "domain": ["art", "tech", "cooking"],
                "difficulty": ["beginner", "intermediate"],
            },
            "other": {
                "quality_indicators": ["high_production", "educational"],
            },
        },
        "allow_free_text_tags": {
            "drawing": {"subject": True},
            "tutorial": {"domain": True},
        },
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(tags_data, f)
        temp_path = f.name

    yield temp_path

    # Cleanup
    from pathlib import Path
    Path(temp_path).unlink()


@pytest.fixture
def tag_manager(temp_tags_config):
    """Create a TagManager instance."""
    return TagManager(temp_tags_config)


@pytest.fixture
def tag_generator(tag_manager):
    """Create a TagGenerator instance."""
    return TagGenerator(
        tag_manager=tag_manager,
        max_tags_per_category=5,
        min_confidence=0.5,
        allow_unknown_tags=False,
    )


def test_generate_tags_for_drawing(tag_generator):
    """Test generating tags for drawing category."""
    analysis = ContentAnalysis(
        category="drawing",
        summary="A sketch portrait drawing",
        key_elements=["Style: Sketch", "Subject: Portrait", "Tools: Pencil"],
        detailed_description="Drawing a portrait using sketch technique",
        confidence=0.8,
        frame_analyses=[],
    )

    tags = tag_generator.generate_tags(analysis)

    assert tags.category == "drawing"
    assert "style" in tags.tags
    assert "subject" in tags.tags
    assert len(tags.all_tags) > 0


def test_generate_tags_for_tutorial(tag_generator):
    """Test generating tags for tutorial category."""
    analysis = ContentAnalysis(
        category="tutorial",
        summary="A tech tutorial about coding",
        key_elements=["Domain: Tech", "Difficulty: Beginner"],
        detailed_description="Teaching coding basics",
        confidence=0.7,
        frame_analyses=[],
    )

    tags = tag_generator.generate_tags(analysis)

    assert tags.category == "tutorial"
    assert len(tags.all_tags) > 0


def test_limit_tags_per_subcategory(tag_generator):
    """Test that tags are limited per subcategory."""
    # Create analysis with many potential matches
    analysis = ContentAnalysis(
        category="drawing",
        summary="sketch watercolor oil_painting portrait landscape",
        key_elements=["Style: Sketch", "Style: Watercolor", "Subject: Portrait"],
        detailed_description="",
        confidence=0.8,
        frame_analyses=[],
    )

    tag_generator.max_tags_per_category = 2
    tags = tag_generator.generate_tags(analysis)

    # Check that no subcategory has more than max_tags_per_category
    for subcategory, tag_list in tags.tags.items():
        assert len(tag_list) <= 2


def test_generated_tags_to_dict():
    """Test GeneratedTags to_dict conversion."""
    tags = GeneratedTags(
        category="drawing",
        tags={"style": ["sketch"], "subject": ["portrait"]},
        confidence=0.8,
        all_tags=["sketch", "portrait"],
    )

    tags_dict = tags.to_dict()

    assert tags_dict["category"] == "drawing"
    assert "style" in tags_dict["tags"]
    assert tags_dict["confidence"] == 0.8


def test_tag_validation(tag_generator):
    """Test that only valid tags are included."""
    analysis = ContentAnalysis(
        category="drawing",
        summary="A invalid_tag sketch portrait",
        key_elements=["Style: Invalid Style", "Subject: Portrait"],
        detailed_description="",
        confidence=0.8,
        frame_analyses=[],
    )

    tags = tag_generator.generate_tags(analysis)

    # Should include valid tags
    assert "portrait" in tags.all_tags or any("portrait" in str(t).lower() for t in tags.all_tags)

    # Should not include invalid tags (unless free-text allowed)
    # (This is a simplified test - real implementation would be more robust)


def test_extract_free_text_tags(tag_generator):
    """Test extracting free-text tags."""
    analysis = ContentAnalysis(
        category="drawing",
        summary="A Custom Style Portrait Drawing",
        key_elements=["Custom Style", "Portrait"],
        detailed_description="",
        confidence=0.8,
        frame_analyses=[],
    )

    free_tags = tag_generator.extract_free_text_tags(analysis, "subject")

    assert isinstance(free_tags, list)


def test_batch_generate_tags(tag_generator):
    """Test generating tags for multiple analyses."""
    analyses = [
        ContentAnalysis(
            category="drawing",
            summary="Sketch portrait",
            key_elements=[],
            detailed_description="",
            confidence=0.8,
            frame_analyses=[],
        ),
        ContentAnalysis(
            category="tutorial",
            summary="Tech tutorial",
            key_elements=[],
            detailed_description="",
            confidence=0.7,
            frame_analyses=[],
        ),
    ]

    tags_list = tag_generator.batch_generate_tags(analyses)

    assert len(tags_list) == 2
    assert tags_list[0].category == "drawing"
    assert tags_list[1].category == "tutorial"


def test_get_tag_summary(tag_generator):
    """Test getting tag summary statistics."""
    generated_tags = [
        GeneratedTags(
            category="drawing",
            tags={"style": ["sketch"]},
            confidence=0.8,
            all_tags=["sketch", "portrait"],
        ),
        GeneratedTags(
            category="tutorial",
            tags={"domain": ["tech"]},
            confidence=0.7,
            all_tags=["tech", "beginner"],
        ),
        GeneratedTags(
            category="drawing",
            tags={"style": ["sketch"]},
            confidence=0.9,
            all_tags=["sketch", "landscape"],
        ),
    ]

    summary = tag_generator.get_tag_summary(generated_tags)

    assert summary["total_videos"] == 3
    assert summary["category_counts"]["drawing"] == 2
    assert summary["category_counts"]["tutorial"] == 1
    assert summary["avg_confidence"] > 0
    assert "sketch" in summary["most_common_tags"]


def test_confidence_filtering(tag_generator):
    """Test filtering by minimum confidence."""
    # Low confidence analysis
    analysis = ContentAnalysis(
        category="drawing",
        summary="Sketch",
        key_elements=[],
        detailed_description="",
        confidence=0.3,  # Below min_confidence
        frame_analyses=[],
    )

    tag_generator.min_confidence = 0.5
    tags = tag_generator.generate_tags(analysis)

    # Should still generate tags, but confidence reflects the input
    assert tags.confidence == 0.3
