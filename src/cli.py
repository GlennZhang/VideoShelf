"""VideoShelf command line interface."""

import sys
import logging
from pathlib import Path
from typing import Optional

import click

# make src/library importable when run as `python -m src.cli` from the
# project root (only the project root is on sys.path in that case)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config.config_manager import ConfigManager  # noqa: E402
from src.config.tag_manager import TagManager  # noqa: E402
from src.video_parser.video_scanner import VideoScanner  # noqa: E402
from src.video_parser.frame_extractor import (  # noqa: E402
    FrameExtractor,
    ExtractionStrategy,
)
from src.video_parser.video_classifier import (  # noqa: E402
    VideoClassifier,
    OllamaClient,
)
from src.video_parser.content_analyzer import ContentAnalyzer  # noqa: E402
from src.video_parser.tag_generator import TagGenerator  # noqa: E402
from src.video_parser.result_storage import (  # noqa: E402
    ResultStorage,
    VideoResult,
)
from src.video_parser.progress_tracker import ProgressTracker  # noqa: E402
from library.cli import library_cli  # noqa: E402


def setup_logging(config_manager: ConfigManager) -> None:
    """Setup logging configuration."""
    log_config = config_manager.logging

    logging.basicConfig(
        level=getattr(logging, log_config.level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_config.log_file),
            logging.StreamHandler(),
        ],
    )


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """VideoShelf CLI - AI-assisted local video classification and tagging."""
    pass


cli.add_command(library_cli)


@cli.command()
@click.argument("directory", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output", "-o", type=click.Path(path_type=Path), help="Output file path"
)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["json", "csv", "sqlite"]),
    default="json",
    help="Output format",
)
@click.option("--recursive", "-r", is_flag=True, help="Scan directories recursively")
@click.option("--force", is_flag=True, help="Reprocess already processed videos")
@click.option("--config", type=click.Path(path_type=Path), help="Custom config file")
def parse(
    directory: Path,
    output: Optional[Path],
    format: str,
    recursive: bool,
    force: bool,
    config: Optional[Path],
):
    """Parse videos in DIRECTORY and generate tags."""
    # Load configuration
    config_manager = ConfigManager(config)
    setup_logging(config_manager)

    logger = logging.getLogger(__name__)
    logger.info(f"Starting video parsing in: {directory}")

    # Initialize components
    tag_manager = TagManager()
    scanner = VideoScanner(
        supported_formats=config_manager.video_scanning.supported_formats,
        recursive=recursive or config_manager.video_scanning.recursive,
        max_file_size_mb=config_manager.video_scanning.max_file_size_mb,
        min_duration_seconds=config_manager.video_scanning.min_duration_seconds,
    )

    frame_extractor = FrameExtractor(
        strategy=ExtractionStrategy(config_manager.frame_extraction.default_strategy),
        interval_seconds=config_manager.frame_extraction.interval_seconds,
        fixed_count=config_manager.frame_extraction.fixed_count,
        max_frames=config_manager.frame_extraction.max_frames,
    )

    ollama_client = OllamaClient(
        model_name=config_manager.model.name,
        timeout=config_manager.model.timeout,
        max_retries=config_manager.model.max_retries,
        temperature=config_manager.model.temperature,
    )

    # Check if model is available
    if not ollama_client.is_model_available():
        logger.error(f"Model {config_manager.model.name} is not available in Ollama")
        logger.error("Please run: ollama pull qwen3-vl:8b")
        sys.exit(1)

    classifier = VideoClassifier(
        ollama_client=ollama_client,
        confidence_threshold=config_manager.classification.confidence_threshold,
        max_frames_for_classification=config_manager.classification.max_frames_for_classification,
    )

    analyzer = ContentAnalyzer(
        ollama_client=ollama_client,
        batch_size=config_manager.classification.batch_size,
    )

    tag_generator = TagGenerator(
        tag_manager=tag_manager,
        max_tags_per_category=config_manager.tag_generation.max_tags_per_category,
        min_confidence=config_manager.tag_generation.min_confidence,
        allow_unknown_tags=config_manager.tag_generation.allow_unknown_tags,
    )

    storage = ResultStorage(output_dir=config_manager.output.output_dir)
    progress = ProgressTracker(
        show_progress_bars=config_manager.progress.show_progress_bars
    )

    # Scan for videos
    logger.info("Scanning for videos...")
    videos = scanner.scan_directory(directory)
    logger.info(f"Found {len(videos)} video files")

    if not videos:
        logger.warning("No videos found")
        return

    # Check for existing results
    if output and output.exists() and not force:
        processed = storage.get_processed_videos(output)
        videos = [v for v in videos if str(v.path) not in processed]
        logger.info(f"Skipping {len(processed)} already processed videos")

    if not videos:
        logger.info("No new videos to process")
        return

    # Start processing
    progress.start(len(videos))
    results = []

    for video_info in videos:
        try:
            logger.info(f"Processing: {video_info.path.name}")

            # Extract frames
            frames, metadata = frame_extractor.extract_frames(video_info.path)

            # Classify
            classification = classifier.classify(
                video_info.path, frames, frame_extractor
            )
            logger.info(
                f"  Category: {classification.category}"
                f" (confidence: {classification.confidence:.2f})"
            )

            # Analyze content
            analysis = analyzer.analyze(
                video_info.path, classification.category, frames, frame_extractor
            )

            # Generate tags
            tags = tag_generator.generate_tags(analysis)

            # Create result
            result = VideoResult(
                video_path=str(video_info.path),
                category=tags.category,
                confidence=tags.confidence,
                description=analysis.summary,
                tags=tags.tags,
                all_tags=tags.all_tags,
                analysis_summary=analysis.summary,
                key_elements=analysis.key_elements,
                frame_count=len(frames),
                duration_seconds=metadata.get("duration_seconds"),
                width=metadata.get("width"),
                height=metadata.get("height"),
            )

            results.append(result)
            progress.update(success=True)

        except Exception as e:
            logger.error(f"Failed to process {video_info.path}: {e}")
            progress.update(success=False, error=str(e))

    progress.finish()

    # Save results
    if results:
        logger.info(f"Saving {len(results)} results...")

        if format == "json":
            storage.save_json(results, output)
        elif format == "csv":
            storage.save_csv(results, output)
        elif format == "sqlite":
            storage.save_sqlite(results, output)

        logger.info(f"Results saved to {output or 'default location'}")
    else:
        logger.warning("No results to save")


@cli.command()
@click.argument("directory", type=click.Path(exists=True, path_type=Path))
@click.option("--recursive", "-r", is_flag=True, help="Scan recursively")
def scan(directory: Path, recursive: bool):
    """Scan DIRECTORY for video files without processing."""
    config_manager = ConfigManager()
    setup_logging(config_manager)

    scanner = VideoScanner(recursive=recursive)
    videos = scanner.scan_directory(directory)

    click.echo(f"Found {len(videos)} video files:")
    for video in videos:
        click.echo(f"  {video.path.name} ({video.size_mb:.2f} MB, {video.format})")

    stats = scanner.get_summary_stats(videos)
    click.echo(f"\nTotal size: {stats['total_size_mb']:.2f} MB")
    click.echo(f"Formats: {stats['formats']}")


@cli.command()
@click.argument("results_file", type=click.Path(exists=True, path_type=Path))
def stats(results_file: Path):
    """Show statistics from results file."""
    storage = ResultStorage()

    if results_file.suffix == ".json":
        results = storage.load_json(results_file)
    elif results_file.suffix == ".db":
        results = storage.load_sqlite(results_file)
    else:
        click.echo("Unsupported file format", err=True)
        return

    # Calculate statistics
    categories = {}
    total_confidence = 0.0
    all_tags = {}

    for result in results:
        cat = result.category
        categories[cat] = categories.get(cat, 0) + 1
        total_confidence += result.confidence

        for tag in result.all_tags:
            all_tags[tag] = all_tags.get(tag, 0) + 1

    # Display statistics
    click.echo(f"Total videos: {len(results)}")
    click.echo(f"Average confidence: {total_confidence / len(results):.2f}")
    click.echo("\nCategories:")
    for cat, count in sorted(categories.items(), key=lambda x: x[1], reverse=True):
        click.echo(f"  {cat}: {count}")

    click.echo("\nTop 20 tags:")
    for tag, count in sorted(all_tags.items(), key=lambda x: x[1], reverse=True)[:20]:
        click.echo(f"  {tag}: {count}")


@cli.command()
@click.argument("results_file", type=click.Path(exists=True, path_type=Path))
@click.argument("tag", type=str)
def filter(results_file: Path, tag: str):
    """Filter results by tag."""
    storage = ResultStorage()

    if results_file.suffix == ".json":
        results = storage.load_json(results_file)
    elif results_file.suffix == ".db":
        results = storage.load_sqlite(results_file)
    else:
        click.echo("Unsupported file format", err=True)
        return

    # Filter results
    filtered = [r for r in results if tag.lower() in str(r.all_tags).lower()]

    click.echo(f"Found {len(filtered)} videos with tag '{tag}':")
    for result in filtered:
        click.echo(f"  {result.video_path}")
        click.echo(f"    Category: {result.category}")
        click.echo(f"    Tags: {', '.join(result.all_tags)}")


if __name__ == "__main__":
    cli()
