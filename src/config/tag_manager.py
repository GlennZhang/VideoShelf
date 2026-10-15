"""Tag taxonomy management for VideoShelf."""

import os
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class TagManager:
    """Manages tag taxonomy and validation."""

    def __init__(self, tags_config_path: Optional[str] = None):
        """
        Initialize TagManager.

        Args:
            tags_config_path: Path to tags_config.yaml file. If None, looks in default locations.
        """
        self.config_path = self._find_tags_config(tags_config_path)
        self._tags_data: Dict[str, Any] = {}
        self._load_tags_config()

    def _find_tags_config(self, config_path: Optional[str]) -> Path:
        """Find the tags configuration file in standard locations."""
        if config_path:
            path = Path(config_path)
            if path.exists():
                return path
            raise FileNotFoundError(f"Tags config file not found: {config_path}")

        # Check default locations
        default_locations = [
            Path("tags_config.yaml"),
            Path("../tags_config.yaml"),
            Path("../../tags_config.yaml"),
            Path.home() / ".video_parser" / "tags_config.yaml",
        ]

        for location in default_locations:
            if location.exists():
                return location

        raise FileNotFoundError(
            "Tags config file not found. Please create tags_config.yaml"
        )

    def _load_tags_config(self) -> None:
        """Load tag configuration from YAML file."""
        with open(self.config_path, "r") as f:
            self._tags_data = yaml.safe_load(f)

    def reload(self) -> None:
        """Reload tag configuration from file."""
        self._load_tags_config()

    def get_categories(self) -> List[str]:
        """Get all top-level video categories."""
        return self._tags_data.get("tag_taxonomy", {}).get("categories", [])

    def get_category_tags(self, category: str) -> Dict[str, List[str]]:
        """
        Get tag subcategories for a specific category.

        Args:
            category: Video category (e.g., 'drawing', 'tutorial')

        Returns:
            Dictionary of subcategory -> list of tags
        """
        return self._tags_data.get("tag_taxonomy", {}).get(category, {})

    def get_subcategory_tags(self, category: str, subcategory: str) -> List[str]:
        """
        Get tags for a specific category and subcategory.

        Args:
            category: Video category
            subcategory: Tag subcategory

        Returns:
            List of valid tags
        """
        category_tags = self.get_category_tags(category)
        return category_tags.get(subcategory, [])

    def is_valid_tag(self, category: str, subcategory: str, tag: str) -> bool:
        """
        Check if a tag is valid for the given category and subcategory.

        Args:
            category: Video category
            subcategory: Tag subcategory
            tag: Tag to validate

        Returns:
            True if tag is valid or if free-text tags are allowed
        """
        valid_tags = self.get_subcategory_tags(category, subcategory)

        # Check if it's a predefined tag
        if tag in valid_tags:
            return True

        # Check if free-text tags are allowed for this subcategory
        return self.allows_free_text(category, subcategory)

    def allows_free_text(self, category: str, subcategory: str) -> bool:
        """
        Check if free-text tags are allowed for a subcategory.

        Args:
            category: Video category
            subcategory: Tag subcategory

        Returns:
            True if free-text tags are allowed
        """
        free_text_config = self._tags_data.get("allow_free_text_tags", {})
        category_config = free_text_config.get(category, {})
        return category_config.get(subcategory, False)

    def get_all_tags_for_category(self, category: str) -> Set[str]:
        """
        Get all predefined tags for a category (all subcategories).

        Args:
            category: Video category

        Returns:
            Set of all valid tags for the category
        """
        all_tags = set()
        category_tags = self.get_category_tags(category)

        for subcategory, tags in category_tags.items():
            all_tags.update(tags)

        return all_tags

    def validate_tags(
        self, category: str, tags: Dict[str, List[str]]
    ) -> Dict[str, List[str]]:
        """
        Validate tags against taxonomy and filter invalid ones.

        Args:
            category: Video category
            tags: Dictionary of subcategory -> list of tags

        Returns:
            Dictionary with only valid tags
        """
        validated_tags = {}

        for subcategory, tag_list in tags.items():
            valid_tags = [
                tag
                for tag in tag_list
                if self.is_valid_tag(category, subcategory, tag)
            ]
            if valid_tags:
                validated_tags[subcategory] = valid_tags

        return validated_tags

    def get_tag_hierarchy(self) -> Dict[str, Any]:
        """
        Get the complete tag taxonomy hierarchy.

        Returns:
            Full tag taxonomy dictionary
        """
        return self._tags_data.get("tag_taxonomy", {})

    def get_predefined_tags_summary(self) -> Dict[str, int]:
        """
        Get a summary count of predefined tags per category.

        Returns:
            Dictionary mapping category to tag count
        """
        summary = {}
        taxonomy = self._tags_data.get("tag_taxonomy", {})

        for category in self.get_categories():
            if category in taxonomy:
                all_tags = self.get_all_tags_for_category(category)
                summary[category] = len(all_tags)

        return summary

    def suggest_tags(
        self, category: str, search_term: str, limit: int = 10
    ) -> List[str]:
        """
        Suggest tags based on partial match.

        Args:
            category: Video category
            search_term: Partial tag name to search for
            limit: Maximum number of suggestions

        Returns:
            List of matching tags
        """
        all_tags = self.get_all_tags_for_category(category)
        search_term = search_term.lower()

        matches = [
            tag for tag in all_tags if search_term in tag.lower()
        ][:limit]

        return matches
