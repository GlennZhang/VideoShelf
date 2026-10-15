"""Tag generation from content analysis."""

import re
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from src.config.tag_manager import TagManager
from src.video_parser.content_analyzer import ContentAnalysis

logger = logging.getLogger(__name__)


@dataclass
class GeneratedTags:
    """Generated tags for a video."""

    category: str
    tags: Dict[str, List[str]]  # subcategory -> list of tags
    confidence: float
    all_tags: List[str]  # Flattened list

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "category": self.category,
            "tags": self.tags,
            "confidence": round(self.confidence, 3),
            "all_tags": self.all_tags,
        }


class TagGenerator:
    """Generate tags from content analysis using tag taxonomy."""

    def __init__(
        self,
        tag_manager: TagManager,
        max_tags_per_category: int = 5,
        min_confidence: float = 0.5,
        allow_unknown_tags: bool = False,
    ):
        """
        Initialize TagGenerator.

        Args:
            tag_manager: TagManager instance
            max_tags_per_category: Maximum tags per subcategory
            min_confidence: Minimum confidence threshold
            allow_unknown_tags: Whether to allow tags not in taxonomy
        """
        self.tag_manager = tag_manager
        self.max_tags_per_category = max_tags_per_category
        self.min_confidence = min_confidence
        self.allow_unknown_tags = allow_unknown_tags

    def generate_tags(self, analysis: ContentAnalysis) -> GeneratedTags:
        """
        Generate tags from content analysis.

        Args:
            analysis: ContentAnalysis object

        Returns:
            GeneratedTags object
        """
        category = analysis.category

        # Get tag structure for this category
        category_structure = self.tag_manager.get_category_tags(category)

        if not category_structure:
            # Use "other" category if structure not found
            category = "other"
            category_structure = self.tag_manager.get_category_tags("other")

        # Extract tags from analysis
        extracted_tags = self._extract_tags_from_analysis(
            analysis, category, category_structure
        )

        # Validate tags against taxonomy
        validated_tags = self.tag_manager.validate_tags(category, extracted_tags)

        # Limit tags per subcategory
        limited_tags = self._limit_tags(validated_tags)

        # Flatten all tags
        all_tags = []
        for tag_list in limited_tags.values():
            all_tags.extend(tag_list)

        return GeneratedTags(
            category=category,
            tags=limited_tags,
            confidence=analysis.confidence,
            all_tags=all_tags,
        )

    def _extract_tags_from_analysis(
        self,
        analysis: ContentAnalysis,
        category: str,
        category_structure: Dict[str, List[str]],
    ) -> Dict[str, List[str]]:
        """Extract tags from analysis text using improved matching."""
        extracted = {}

        # Combine all text from analysis
        text = f"{analysis.summary} {analysis.detailed_description} {' '.join(analysis.key_elements)}".lower()

        # For each subcategory, find matching tags with flexible matching
        for subcategory, valid_tags in category_structure.items():
            matches = []

            for tag in valid_tags:
                # Create multiple variants for matching
                tag_variants = [
                    tag.replace("_", " ").replace("-", " "),  # watercolor
                    tag.replace("_", ""),  # watercolor (no underscores)
                    tag.replace("-", ""),  # watercolor (no hyphens)
                ]

                # Check if any variant appears in text
                for variant in tag_variants:
                    if variant in text:
                        matches.append(tag)
                        break

                # Also check partial matching for compound words
                # e.g., "water color" should match "watercolor"
                tag_parts = tag.replace("_", " ").replace("-", " ").split()
                if len(tag_parts) > 1:
                    # Check if parts appear in sequence
                    text_lower = text.lower()
                    for i in range(len(text_lower) - len(tag) + 1):
                        substring = text_lower[i:i+len(tag)+5]
                        if all(part in substring for part in tag_parts):
                            matches.append(tag)
                            break

            if matches:
                # Remove duplicates while preserving order
                seen = set()
                unique_matches = []
                for m in matches:
                    if m not in seen:
                        seen.add(m)
                        unique_matches.append(m)
                extracted[subcategory] = unique_matches

        # Also try to extract from key elements (structured fields)
        for element in analysis.key_elements:
            element_lower = element.lower()

            # Parse structured elements like "Domain: art" or "Difficulty: beginner"
            # Support both Chinese and English separators
            if ":" in element or "：" in element:
                # Try Chinese colon first
                if "：" in element:
                    parts = element.split("：", 1)
                else:
                    parts = element.split(":", 1)

                if len(parts) == 2:
                    field_name = parts[0].strip().lower()
                    field_value = parts[1].strip().lower()

                    # Map field names to subcategories (Chinese and English)
                    field_mapping = {
                        # English
                        "domain": "领域",
                        "difficulty": "难度",
                        "format": "形式",
                        "style": "风格",
                        "subject": "主题",
                        "tools": "工具",
                        "technique": "技法",
                        "level": "技能水平",
                        # Chinese
                        "领域": "领域",
                        "难度": "难度",
                        "形式": "形式",
                        "风格": "风格",
                        "主题": "主题",
                        "工具": "工具",
                        "技法": "技法",
                        "水平": "技能水平",
                    }

                    if field_name in field_mapping:
                        subcategory = field_mapping[field_name]

                        # Find matching tag
                        if subcategory in category_structure:
                            for tag in category_structure[subcategory]:
                                tag_variant = tag.replace("_", " ").replace("-", " ")
                                if tag_variant in field_value or field_value in tag_variant:
                                    if subcategory not in extracted:
                                        extracted[subcategory] = []
                                    if tag not in extracted[subcategory]:
                                        extracted[subcategory].append(tag)

        return extracted

    def _limit_tags(self, tags: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """Limit number of tags per subcategory."""
        limited = {}

        for subcategory, tag_list in tags.items():
            limited[subcategory] = tag_list[: self.max_tags_per_category]

        return limited

    def extract_free_text_tags(
        self, analysis: ContentAnalysis, subcategory: str
    ) -> List[str]:
        """
        Extract free-text tags for a subcategory that allows them.

        Args:
            analysis: ContentAnalysis object
            subcategory: Subcategory to extract tags for

        Returns:
            List of free-text tags
        """
        # This is a simplified implementation
        # In a real system, you might use NLP to extract relevant terms

        text = f"{analysis.summary} {' '.join(analysis.key_elements)}"

        # Extract potential tags (capitalized phrases, numbered items, etc.)
        potential_tags = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text)

        return potential_tags[:5]

    def batch_generate_tags(
        self, analyses: List[ContentAnalysis]
    ) -> List[GeneratedTags]:
        """Generate tags for multiple analyses."""
        return [self.generate_tags(analysis) for analysis in analyses]

    def get_tag_summary(
        self, generated_tags_list: List[GeneratedTags]
    ) -> Dict[str, Any]:
        """Get summary statistics for generated tags."""
        summary = {
            "total_videos": len(generated_tags_list),
            "category_counts": {},
            "most_common_tags": {},
            "avg_confidence": 0.0,
        }

        all_tags = {}
        total_confidence = 0.0

        for gt in generated_tags_list:
            # Count categories
            category = gt.category
            summary["category_counts"][category] = (
                summary["category_counts"].get(category, 0) + 1
            )

            # Count all tags
            for tag in gt.all_tags:
                all_tags[tag] = all_tags.get(tag, 0) + 1

            total_confidence += gt.confidence

        # Calculate average confidence
        if generated_tags_list:
            summary["avg_confidence"] = total_confidence / len(generated_tags_list)

        # Get most common tags
        sorted_tags = sorted(all_tags.items(), key=lambda x: x[1], reverse=True)
        summary["most_common_tags"] = dict(sorted_tags[:20])

        return summary
