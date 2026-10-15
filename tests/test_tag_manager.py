"""Tests for TagManager."""

import pytest
import tempfile
import yaml
from pathlib import Path
from src.config.tag_manager import TagManager


@pytest.fixture
def temp_tags_config():
    """Create a temporary tags config file."""
    tags_data = {
        "tag_taxonomy": {
            "categories": ["drawing", "tutorial", "other"],
            "drawing": {
                "style": ["sketch", "watercolor", "oil_painting"],
                "subject": ["portrait", "landscape", "abstract"],
            },
            "tutorial": {
                "domain": ["art", "cooking", "tech"],
                "difficulty": ["beginner", "intermediate", "advanced"],
            },
            "other": {
                "quality_indicators": ["high_production", "educational"],
            },
        },
        "allow_free_text_tags": {
            "drawing": {
                "subject": True,
                "style": False,
            },
            "tutorial": {
                "domain": True,
            },
        },
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(tags_data, f)
        temp_path = f.name

    yield temp_path

    # Cleanup
    Path(temp_path).unlink()


def test_tag_manager_load(temp_tags_config):
    """Test loading tags configuration."""
    manager = TagManager(temp_tags_config)

    categories = manager.get_categories()
    assert "drawing" in categories
    assert "tutorial" in categories
    assert "other" in categories


def test_get_category_tags(temp_tags_config):
    """Test getting tags for a category."""
    manager = TagManager(temp_tags_config)

    drawing_tags = manager.get_category_tags("drawing")
    assert "style" in drawing_tags
    assert "subject" in drawing_tags
    assert "sketch" in drawing_tags["style"]
    assert "watercolor" in drawing_tags["style"]


def test_get_subcategory_tags(temp_tags_config):
    """Test getting tags for a specific subcategory."""
    manager = TagManager(temp_tags_config)

    style_tags = manager.get_subcategory_tags("drawing", "style")
    assert style_tags == ["sketch", "watercolor", "oil_painting"]

    domain_tags = manager.get_subcategory_tags("tutorial", "domain")
    assert domain_tags == ["art", "cooking", "tech"]


def test_is_valid_tag(temp_tags_config):
    """Test tag validation."""
    manager = TagManager(temp_tags_config)

    # Valid predefined tag
    assert manager.is_valid_tag("drawing", "style", "sketch") is True

    # Invalid tag (not in list)
    assert manager.is_valid_tag("drawing", "style", "invalid_style") is False

    # Free-text allowed
    assert manager.is_valid_tag("drawing", "subject", "custom_subject") is True
    assert manager.is_valid_tag("tutorial", "domain", "custom_domain") is True


def test_allows_free_text(temp_tags_config):
    """Test free-text tag permission checking."""
    manager = TagManager(temp_tags_config)

    assert manager.allows_free_text("drawing", "subject") is True
    assert manager.allows_free_text("drawing", "style") is False
    assert manager.allows_free_text("tutorial", "domain") is True


def test_get_all_tags_for_category(temp_tags_config):
    """Test getting all tags for a category."""
    manager = TagManager(temp_tags_config)

    drawing_tags = manager.get_all_tags_for_category("drawing")
    assert "sketch" in drawing_tags
    assert "watercolor" in drawing_tags
    assert "portrait" in drawing_tags
    assert "landscape" in drawing_tags
    assert len(drawing_tags) == 6  # 3 styles + 3 subjects


def test_validate_tags(temp_tags_config):
    """Test tag validation and filtering."""
    manager = TagManager(temp_tags_config)

    input_tags = {
        "style": ["sketch", "invalid_style", "watercolor"],
        "subject": ["portrait", "custom_subject"],
    }

    validated = manager.validate_tags("drawing", input_tags)

    # style should only have valid tags (free-text not allowed)
    assert validated["style"] == ["sketch", "watercolor"]

    # subject should have both (free-text allowed)
    assert set(validated["subject"]) == {"portrait", "custom_subject"}


def test_get_predefined_tags_summary(temp_tags_config):
    """Test getting tags summary."""
    manager = TagManager(temp_tags_config)

    summary = manager.get_predefined_tags_summary()

    assert summary["drawing"] == 6  # 3 styles + 3 subjects
    assert summary["tutorial"] == 5  # 3 domains + 2 difficulties
    assert summary["other"] == 2


def test_suggest_tags(temp_tags_config):
    """Test tag suggestions."""
    manager = TagManager(temp_tags_config)

    # Test partial match
    suggestions = manager.suggest_tags("drawing", "sk")
    assert "sketch" in suggestions

    # Test with limit
    suggestions = manager.suggest_tags("drawing", "a", limit=2)
    assert len(suggestions) <= 2


def test_tag_manager_reload(temp_tags_config):
    """Test configuration reload."""
    manager = TagManager(temp_tags_config)

    # Initial categories
    assert len(manager.get_categories()) == 3

    # Modify config
    with open(temp_tags_config, "r") as f:
        data = yaml.safe_load(f)
    data["tag_taxonomy"]["categories"].append("new_category")

    with open(temp_tags_config, "w") as f:
        yaml.dump(data, f)

    # Reload and check
    manager.reload()
    assert "new_category" in manager.get_categories()
