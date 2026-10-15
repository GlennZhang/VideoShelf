# Change: Add Video Parser Core System

## Why

Develop a local vision model-based video understanding system that automatically extracts key frames and generates descriptive tags for video classification and retrieval. The system targets users managing large collections of drawing/art videos but supports multiple video types with an extensible tag taxonomy.

Current problems:
- Manual video tagging is time-consuming and error-prone
- No automated content understanding for local video collections
- Need privacy-preserving solution (no cloud API dependency)
- Drawing videos require deep content recognition beyond "someone is drawing"

## What Changes

- **Video scanning**: Discover and filter video files (MP4, AVI, MOV, MKV, WebM) from directories
- **Frame extraction**: Extract representative key frames using configurable strategies (interval-based, fixed-count, scene-detection)
- **Video classification**: Two-stage classification using qwen3-VL:8B model:
  1. Determine top-level category (drawing/tutorial/vlog/entertainment/sports/other)
  2. Category-specific content analysis
- **Tag generation**: Generate structured tags based on YAML-configured taxonomy with validation
- **Result storage**: Export to JSON/CSV/SQLite with incremental update support
- **CLI interface**: Command-line tool with progress tracking and error recovery

**Technology choices**:
- Python 3.10+ with OpenCV for video processing
- Ollama qwen3-VL:8B for local vision understanding
- YAML-based tag configuration for extensibility
- Click/Typer for CLI with tqdm progress bars

## Impact

- Affected specs:
  - `video-scanning` - NEW
  - `frame-extraction` - NEW
  - `video-classification` - NEW
  - `content-analysis` - NEW
  - `tag-generation` - NEW
  - `result-storage` - NEW

- Affected code:
  - New project: core modules under `src/video_parser/`
  - CLI entry point under `src/cli.py`
  - Configuration management under `src/config/`
  - Tag configuration in `tags_config.yaml`

- Dependencies:
  - OpenCV (cv2) for video frame extraction
  - ollama Python library for model inference
  - PyYAML for configuration
  - pandas for CSV export
  - Click/Typer for CLI
  - tqdm for progress bars

## Non-Goals

- Web UI (deferred to future enhancement)
- Video preview/thumbnails (deferred)
- Similar video detection (deferred)
- Multi-language tag support (deferred)
- Video auto-organization into folders (deferred)
