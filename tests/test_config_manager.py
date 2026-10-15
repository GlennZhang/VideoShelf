"""Tests for ConfigManager."""

import pytest
import tempfile
import yaml
from pathlib import Path
from src.config.config_manager import ConfigManager


@pytest.fixture
def temp_config_file():
    """Create a temporary config file."""
    config_data = {
        "model": {
            "name": "test-model",
            "timeout": 60,
            "max_retries": 5,
            "temperature": 0.8,
        },
        "frame_extraction": {
            "default_strategy": "fixed_count",
            "interval_seconds": 15,
        },
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(config_data, f)
        temp_path = f.name

    yield temp_path

    # Cleanup
    Path(temp_path).unlink()


def test_config_manager_load(temp_config_file):
    """Test loading configuration from file."""
    manager = ConfigManager(temp_config_file)

    assert manager.get("model.name") == "test-model"
    assert manager.get("model.timeout") == 60
    assert manager.get("model.max_retries") == 5
    assert manager.get("model.temperature") == 0.8


def test_config_get_with_defaults():
    """Test getting configuration with default values."""
    manager = ConfigManager()

    # Test existing key
    assert manager.get("model.name") is not None

    # Test non-existing key with default
    assert manager.get("nonexistent.key", "default_value") == "default_value"


def test_model_config():
    """Test model configuration object."""
    manager = ConfigManager()
    model_config = manager.model

    assert isinstance(model_config.name, str)
    assert isinstance(model_config.timeout, int)
    assert isinstance(model_config.max_retries, int)
    assert isinstance(model_config.temperature, float)


def test_frame_extraction_config():
    """Test frame extraction configuration object."""
    manager = ConfigManager()
    frame_config = manager.frame_extraction

    assert isinstance(frame_config.default_strategy, str)
    assert isinstance(frame_config.interval_seconds, int)
    assert isinstance(frame_config.fixed_count, int)
    assert frame_config.default_strategy in ["interval", "fixed_count", "scene_detection"]


def test_all_config_sections():
    """Test that all config sections are accessible."""
    manager = ConfigManager()

    # Test all config sections
    assert manager.model is not None
    assert manager.frame_extraction is not None
    assert manager.video_scanning is not None
    assert manager.classification is not None
    assert manager.tag_generation is not None
    assert manager.output is not None
    assert manager.progress is not None
    assert manager.logging is not None
    assert manager.error_handling is not None


def test_config_reload(temp_config_file):
    """Test configuration reload."""
    manager = ConfigManager(temp_config_file)

    # Initial value
    assert manager.get("model.name") == "test-model"

    # Modify file
    with open(temp_config_file, "r") as f:
        data = yaml.safe_load(f)
    data["model"]["name"] = "updated-model"

    with open(temp_config_file, "w") as f:
        yaml.dump(data, f)

    # Reload and check
    manager.reload()
    assert manager.get("model.name") == "updated-model"
