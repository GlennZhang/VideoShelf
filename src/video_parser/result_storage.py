"""Result storage in multiple formats (JSON, CSV, SQLite)."""

import json
import sqlite3
import csv
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class VideoResult:
    """Complete result for a processed video."""

    video_path: str
    category: str
    confidence: float
    description: str
    tags: Dict[str, List[str]]
    all_tags: List[str]
    analysis_summary: str
    key_elements: List[str]
    frame_count: int
    duration_seconds: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    processed_at: str = ""

    def __post_init__(self):
        """Set processed timestamp if not provided."""
        if not self.processed_at:
            self.processed_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


class ResultStorage:
    """Store video processing results in various formats."""

    def __init__(self, output_dir: str = "results"):
        """
        Initialize ResultStorage.

        Args:
            output_dir: Directory to store output files
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save_json(
        self,
        results: List[VideoResult],
        output_file: Optional[Path] = None,
        pretty: bool = True,
    ) -> Path:
        """
        Save results to JSON file.

        Args:
            results: List of VideoResult objects
            output_file: Output file path (optional)
            pretty: Whether to format JSON nicely

        Returns:
            Path to saved file
        """
        if output_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = self.output_dir / f"video_results_{timestamp}.json"

        # Convert to dict
        data = {
            "generated_at": datetime.now().isoformat(),
            "total_videos": len(results),
            "results": [r.to_dict() for r in results],
        }

        # Write to file
        with open(output_file, "w", encoding="utf-8") as f:
            if pretty:
                json.dump(data, f, indent=2, ensure_ascii=False)
            else:
                json.dump(data, f, ensure_ascii=False)

        logger.info(f"Saved {len(results)} results to {output_file}")
        return output_file

    def load_json(self, input_file: Path) -> List[VideoResult]:
        """
        Load results from JSON file.

        Args:
            input_file: Path to JSON file

        Returns:
            List of VideoResult objects
        """
        with open(input_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        results = []
        for result_dict in data.get("results", []):
            results.append(VideoResult(**result_dict))

        return results

    def save_csv(
        self,
        results: List[VideoResult],
        output_file: Optional[Path] = None,
    ) -> Path:
        """
        Save results to CSV file.

        Args:
            results: List of VideoResult objects
            output_file: Output file path (optional)

        Returns:
            Path to saved file
        """
        if output_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = self.output_dir / f"video_results_{timestamp}.csv"

        # Flatten results for CSV
        flattened = []
        for result in results:
            row = {
                "video_path": result.video_path,
                "category": result.category,
                "confidence": result.confidence,
                "description": result.description,
                "tags": ", ".join(result.all_tags),
                "analysis_summary": result.analysis_summary,
                "key_elements": "; ".join(result.key_elements),
                "frame_count": result.frame_count,
                "duration_seconds": result.duration_seconds,
                "width": result.width,
                "height": result.height,
                "processed_at": result.processed_at,
            }
            flattened.append(row)

        # Write to CSV
        if flattened:
            with open(output_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=flattened[0].keys())
                writer.writeheader()
                writer.writerows(flattened)

        logger.info(f"Saved {len(results)} results to {output_file}")
        return output_file

    def save_sqlite(
        self,
        results: List[VideoResult],
        output_file: Optional[Path] = None,
        table_name: str = "video_results",
    ) -> Path:
        """
        Save results to SQLite database.

        Args:
            results: List of VideoResult objects
            output_file: Output database file path (optional)
            table_name: Name of the table

        Returns:
            Path to saved database
        """
        if output_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = self.output_dir / f"video_results_{timestamp}.db"

        conn = sqlite3.connect(str(output_file))
        cursor = conn.cursor()

        # Create table
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_path TEXT NOT NULL,
                category TEXT,
                confidence REAL,
                description TEXT,
                tags TEXT,
                all_tags TEXT,
                analysis_summary TEXT,
                key_elements TEXT,
                frame_count INTEGER,
                duration_seconds REAL,
                width INTEGER,
                height INTEGER,
                processed_at TEXT
            )
        """)

        # Insert results
        for result in results:
            cursor.execute(f"""
                INSERT INTO {table_name} (
                    video_path, category, confidence, description, tags,
                    all_tags, analysis_summary, key_elements, frame_count,
                    duration_seconds, width, height, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                result.video_path,
                result.category,
                result.confidence,
                result.description,
                json.dumps(result.tags),
                ", ".join(result.all_tags),
                result.analysis_summary,
                "; ".join(result.key_elements),
                result.frame_count,
                result.duration_seconds,
                result.width,
                result.height,
                result.processed_at,
            ))

        conn.commit()
        conn.close()

        logger.info(f"Saved {len(results)} results to {output_file}")
        return output_file

    def load_sqlite(
        self,
        input_file: Path,
        table_name: str = "video_results",
    ) -> List[VideoResult]:
        """
        Load results from SQLite database.

        Args:
            input_file: Path to database file
            table_name: Name of the table

        Returns:
            List of VideoResult objects
        """
        conn = sqlite3.connect(str(input_file))
        cursor = conn.cursor()

        cursor.execute(f"SELECT * FROM {table_name}")
        rows = cursor.fetchall()

        # Get column names
        columns = [desc[0] for desc in cursor.description]

        results = []
        for row in rows:
            row_dict = dict(zip(columns, row))

            # Parse JSON fields
            if isinstance(row_dict.get("tags"), str):
                row_dict["tags"] = json.loads(row_dict["tags"])

            if isinstance(row_dict.get("all_tags"), str):
                row_dict["all_tags"] = row_dict["all_tags"].split(", ")

            if isinstance(row_dict.get("key_elements"), str):
                row_dict["key_elements"] = row_dict["key_elements"].split("; ")

            # Remove id field
            row_dict.pop("id", None)

            results.append(VideoResult(**row_dict))

        conn.close()
        return results

    def get_processed_videos(self, input_file: Path) -> set[str]:
        """
        Get set of already processed video paths from storage.

        Args:
            input_file: Path to storage file (JSON or SQLite)

        Returns:
            Set of video paths
        """
        if input_file.suffix == ".json":
            results = self.load_json(input_file)
            return {r.video_path for r in results}
        elif input_file.suffix == ".db":
            results = self.load_sqlite(input_file)
            return {r.video_path for r in results}
        else:
            logger.warning(f"Unsupported file format: {input_file.suffix}")
            return set()

    def append_results(
        self,
        new_results: List[VideoResult],
        output_file: Path,
        format: str = "json",
    ) -> None:
        """
        Append new results to existing file.

        Args:
            new_results: New results to append
            output_file: Path to existing file
            format: File format (json, csv, sqlite)
        """
        if not output_file.exists():
            # Create new file
            if format == "json":
                self.save_json(new_results, output_file)
            elif format == "csv":
                self.save_csv(new_results, output_file)
            elif format == "sqlite":
                self.save_sqlite(new_results, output_file)
            return

        # Load existing results
        if format == "json":
            existing_results = self.load_json(output_file)
            all_results = existing_results + new_results
            self.save_json(all_results, output_file)
        elif format == "sqlite":
            # For SQLite, we can just append
            self.save_sqlite(new_results, output_file)
        elif format == "csv":
            # For CSV, we need to rewrite
            existing_results = self.load_json(output_file.parent / f"{output_file.stem}.json")
            all_results = existing_results + new_results
            self.save_csv(all_results, output_file)
