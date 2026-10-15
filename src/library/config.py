"""Library configuration loading.

Reads the ``library:`` and ``omlx:`` sections from the project config.yaml,
applies env overrides and validates values. Environment variables win over
file values so secrets and machine-specific URLs never need to be committed.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import os

import yaml

#: Video container extensions recognized by the library (case-insensitive).
SUPPORTED_EXTENSIONS = (".mp4", ".mkv", ".mov")

DEFAULT_OMLX_BASE_URL = "http://127.0.0.1:8989/v1"
DEFAULT_ASR_MODEL = "Qwen3-ASR-1.7B-8bit"
DEFAULT_VISION_MODEL = "Qwen3.5-9B-MLX-4bit"


class ConfigError(Exception):
    """Raised when library configuration is missing or invalid."""


@dataclass(frozen=True)
class OmlxConfig:
    """oMLX inference service settings."""

    base_url: str = DEFAULT_OMLX_BASE_URL
    api_key: str = "noapikey"
    asr_model: str = DEFAULT_ASR_MODEL
    vision_model: str = DEFAULT_VISION_MODEL
    timeout_s: float = 600.0
    max_retries: int = 3

    @property
    def effective_api_key(self) -> str:
        """API key with ``OMLX_API_KEY`` env override."""
        return os.environ.get("OMLX_API_KEY", self.api_key)


@dataclass(frozen=True)
class LibraryConfig:
    """Library application settings."""

    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 8900
    scan_excludes: List[str] = field(default_factory=lambda: [".DS_Store", "._*"])
    analysis: Dict[str, Any] = field(
        default_factory=lambda: {
            "max_frames": 10,
            "transcribe_chunk_s": 600,
            "transcribe_budget_s": 300,
            "frame_interval_s": 10.0,
        }
    )
    omlx: OmlxConfig = OmlxConfig()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "library.db"

    @property
    def screenshots_dir(self) -> Path:
        return self.data_dir / "screenshots"

    @property
    def analysis_cache_dir(self) -> Path:
        return self.data_dir / "analysis"


def _load_yaml_section(config_path: Path, section: str) -> Dict[str, Any]:
    if not config_path.exists():
        raise ConfigError(f"config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"config file is not a mapping: {config_path}")
    section_data = raw.get(section)
    if section_data is None:
        return {}
    if not isinstance(section_data, dict):
        raise ConfigError(f"config section '{section}' must be a mapping")
    return section_data


def _build_omlx(data: Dict[str, Any]) -> OmlxConfig:
    known = {f for f in OmlxConfig.__dataclass_fields__}
    extra = set(data) - known
    if extra:
        raise ConfigError(f"unknown omlx config keys: {sorted(extra)}")
    overrides = {
        "base_url": os.environ.get("OMLX_BASE_URL"),
        "asr_model": os.environ.get("OMLX_ASR_MODEL"),
        "vision_model": os.environ.get("OMLX_VISION_MODEL"),
    }
    values = {k: v for k, v in data.items() if v is not None}
    values.update({k: v for k, v in overrides.items() if v})
    return OmlxConfig(**values)


def load_library_config(config_path: Path | str = "config.yaml") -> LibraryConfig:
    """Load library settings from yaml with env overrides.

    Raises:
        ConfigError: on missing file or invalid section values.
    """
    config_path = Path(config_path)
    data = _load_yaml_section(config_path, "library")
    omlx = _build_omlx(_load_yaml_section(config_path, "omlx"))

    known = {f for f in LibraryConfig.__dataclass_fields__} - {"omlx"}
    extra = set(data) - known
    if extra:
        raise ConfigError(f"unknown library config keys: {sorted(extra)}")

    if "data_dir" in data:
        data["data_dir"] = Path(str(data["data_dir"])).expanduser()
    cfg = LibraryConfig(**data, omlx=omlx)

    if cfg.port < 1 or cfg.port > 65535:
        raise ConfigError(f"invalid library port: {cfg.port}")
    return cfg
