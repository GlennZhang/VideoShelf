"""Configuration management for VideoShelf."""

import os
import yaml
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass, field


@dataclass
class ModelConfig:
    """Model configuration settings."""

    name: str = "qwen3-vl:8b"
    timeout: int = 30
    max_retries: int = 3
    temperature: float = 0.7


@dataclass
class FrameExtractionConfig:
    """Frame extraction configuration."""

    default_strategy: str = "interval"
    interval_seconds: int = 10
    fixed_count: int = 8
    max_frames: int = 20
    scene_threshold: int = 30


@dataclass
class VideoScanningConfig:
    """Video scanning configuration."""

    recursive: bool = True
    supported_formats: list = field(default_factory=lambda: [".mp4", ".avi", ".mov", ".mkv", ".webm"])
    max_file_size_mb: int = 5000
    min_duration_seconds: int = 5


@dataclass
class ClassificationConfig:
    """Classification configuration."""

    confidence_threshold: float = 0.6
    max_frames_for_classification: int = 5
    batch_size: int = 3


@dataclass
class TagGenerationConfig:
    """Tag generation configuration."""

    max_tags_per_category: int = 5
    min_confidence: float = 0.5
    allow_unknown_tags: bool = False


@dataclass
class OutputConfig:
    """Output configuration."""

    default_format: str = "json"
    output_dir: str = "results"
    include_timestamps: bool = True
    include_frames_data: bool = False


@dataclass
class ProgressConfig:
    """Progress tracking configuration."""

    show_progress_bars: bool = True
    update_interval: int = 1
    estimate_time_remaining: bool = True


@dataclass
class LoggingConfig:
    """Logging configuration."""

    level: str = "INFO"
    log_file: str = "video_parser.log"
    max_log_size_mb: int = 10
    backup_count: int = 5


@dataclass
class ErrorHandlingConfig:
    """Error handling configuration."""

    max_retries_per_video: int = 2
    skip_corrupted_videos: bool = True
    continue_on_error: bool = True
    save_error_log: bool = True


class ConfigManager:
    """Manages application configuration."""

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize ConfigManager.

        Args:
            config_path: Path to config.yaml file. If None, looks in default locations.
        """
        self.config_path = self._find_config_file(config_path)
        self._config_data: Dict[str, Any] = {}
        self._load_config()

    def _find_config_file(self, config_path: Optional[str]) -> Path:
        """Find the configuration file in standard locations."""
        if config_path:
            path = Path(config_path)
            if path.exists():
                return path
            raise FileNotFoundError(f"Config file not found: {config_path}")

        # Check default locations
        default_locations = [
            Path("config.yaml"),
            Path("../config.yaml"),
            Path("../../config.yaml"),
            Path.home() / ".video_parser" / "config.yaml",
        ]

        for location in default_locations:
            if location.exists():
                return location

        # If not found, create default config
        return Path("config.yaml")

    def _load_config(self) -> None:
        """Load configuration from YAML file."""
        try:
            with open(self.config_path, "r") as f:
                self._config_data = yaml.safe_load(f) or {}
        except FileNotFoundError:
            # Use default configuration
            self._config_data = {}

    def reload(self) -> None:
        """Reload configuration from file."""
        self._load_config()

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value by key (supports dot notation).

        Args:
            key: Configuration key (e.g., 'model.name')
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        keys = key.split(".")
        value = self._config_data

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    @property
    def model(self) -> ModelConfig:
        """Get model configuration."""
        return ModelConfig(
            name=self.get("model.name", "qwen3-vl:8b"),
            timeout=self.get("model.timeout", 30),
            max_retries=self.get("model.max_retries", 3),
            temperature=self.get("model.temperature", 0.7),
        )

    @property
    def frame_extraction(self) -> FrameExtractionConfig:
        """Get frame extraction configuration."""
        return FrameExtractionConfig(
            default_strategy=self.get("frame_extraction.default_strategy", "interval"),
            interval_seconds=self.get("frame_extraction.interval_seconds", 10),
            fixed_count=self.get("frame_extraction.fixed_count", 8),
            max_frames=self.get("frame_extraction.max_frames", 20),
            scene_threshold=self.get("frame_extraction.scene_threshold", 30),
        )

    @property
    def video_scanning(self) -> VideoScanningConfig:
        """Get video scanning configuration."""
        return VideoScanningConfig(
            recursive=self.get("video_scanning.recursive", True),
            supported_formats=self.get(
                "video_scanning.supported_formats",
                [".mp4", ".avi", ".mov", ".mkv", ".webm"],
            ),
            max_file_size_mb=self.get("video_scanning.max_file_size_mb", 5000),
            min_duration_seconds=self.get("video_scanning.min_duration_seconds", 5),
        )

    @property
    def classification(self) -> ClassificationConfig:
        """Get classification configuration."""
        return ClassificationConfig(
            confidence_threshold=self.get("classification.confidence_threshold", 0.6),
            max_frames_for_classification=self.get(
                "classification.max_frames_for_classification", 5
            ),
            batch_size=self.get("classification.batch_size", 3),
        )

    @property
    def tag_generation(self) -> TagGenerationConfig:
        """Get tag generation configuration."""
        return TagGenerationConfig(
            max_tags_per_category=self.get("tag_generation.max_tags_per_category", 5),
            min_confidence=self.get("tag_generation.min_confidence", 0.5),
            allow_unknown_tags=self.get("tag_generation.allow_unknown_tags", False),
        )

    @property
    def output(self) -> OutputConfig:
        """Get output configuration."""
        return OutputConfig(
            default_format=self.get("output.default_format", "json"),
            output_dir=self.get("output.output_dir", "results"),
            include_timestamps=self.get("output.include_timestamps", True),
            include_frames_data=self.get("output.include_frames_data", False),
        )

    @property
    def progress(self) -> ProgressConfig:
        """Get progress configuration."""
        return ProgressConfig(
            show_progress_bars=self.get("progress.show_progress_bars", True),
            update_interval=self.get("progress.update_interval", 1),
            estimate_time_remaining=self.get("progress.estimate_time_remaining", True),
        )

    @property
    def logging(self) -> LoggingConfig:
        """Get logging configuration."""
        return LoggingConfig(
            level=self.get("logging.level", "INFO"),
            log_file=self.get("logging.log_file", "video_parser.log"),
            max_log_size_mb=self.get("logging.max_log_size_mb", 10),
            backup_count=self.get("logging.backup_count", 5),
        )

    @property
    def error_handling(self) -> ErrorHandlingConfig:
        """Get error handling configuration."""
        return ErrorHandlingConfig(
            max_retries_per_video=self.get("error_handling.max_retries_per_video", 2),
            skip_corrupted_videos=self.get("error_handling.skip_corrupted_videos", True),
            continue_on_error=self.get("error_handling.continue_on_error", True),
            save_error_log=self.get("error_handling.save_error_log", True),
        )
