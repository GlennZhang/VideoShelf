"""Video classification using Ollama vision model."""

import logging
import time
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path

from src.video_parser.frame_extractor import FrameInfo, FrameExtractor

logger = logging.getLogger(__name__)


@dataclass
class ClassificationResult:
    """Result of video classification."""

    category: str
    confidence: float
    description: str
    raw_response: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "category": self.category,
            "confidence": round(self.confidence, 3),
            "description": self.description,
            "raw_response": self.raw_response,
        }


class OllamaClient:
    """Client for interacting with Ollama API."""

    def __init__(
        self,
        model_name: str = "qwen3-vl:8b",
        timeout: int = 30,
        max_retries: int = 3,
        temperature: float = 0.7,
    ):
        """
        Initialize Ollama client.

        Args:
            model_name: Name of the Ollama model
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
            temperature: Temperature for generation
        """
        try:
            import ollama
        except ImportError:
            raise RuntimeError(
                "Ollama library is required. Install with: pip install ollama"
            )

        self.client = ollama
        self.model_name = model_name
        self.timeout = timeout
        self.max_retries = max_retries
        self.temperature = temperature

    def is_model_available(self) -> bool:
        """
        Check if the model is available in Ollama.

        Returns:
            True if model is available
        """
        try:
            models = self.client.list()

            # Check both 'name' and 'model' fields in response
            model_list = models.get("models", [])
            for m in model_list:
                # Check 'name' field
                if "name" in m:
                    model_name = m["name"]
                    if self.model_name in model_name or model_name in self.model_name:
                        return True
                # Check 'model' field (some versions use this)
                if "model" in m:
                    model_id = m["model"]
                    if self.model_name in model_id or model_id in self.model_name:
                        return True

            logger.warning(f"Model {self.model_name} not found. Available models:")
            for m in model_list[:10]:  # Show first 10
                logger.warning(f"  - {m.get('name', m.get('model', 'unknown'))}")

            return False
        except Exception as e:
            logger.error(f"Failed to check model availability: {e}")
            return False

    def generate_response(
        self,
        prompt: str,
        images: Optional[List[bytes]] = None,
    ) -> str:
        """
        Generate a response from the model.

        Args:
            prompt: Text prompt
            images: Optional list of image data (bytes)

        Returns:
            Model response text

        Raises:
            RuntimeError: If generation fails after retries
        """
        kwargs = {
            "model": self.model_name,
            "prompt": prompt,
            "options": {"temperature": self.temperature},
        }

        if images:
            kwargs["images"] = images

        for attempt in range(self.max_retries):
            try:
                response = self.client.generate(**kwargs)
                return response.get("response", "")
            except Exception as e:
                logger.warning(
                    f"Attempt {attempt + 1}/{self.max_retries} failed: {e}"
                )
                if attempt == self.max_retries - 1:
                    raise RuntimeError(
                        f"Failed to generate response after {self.max_retries} attempts: {e}"
                    )
                time.sleep(1 * (attempt + 1))  # Exponential backoff

        return ""


class VideoClassifier:
    """Classify videos into categories using vision model."""

    # Valid categories (Chinese)
    CATEGORIES = [
        "绘画",
        "教程",
        "vlog",
        "娱乐",
        "体育",
        "其他",
    ]

    def __init__(
        self,
        ollama_client: OllamaClient,
        confidence_threshold: float = 0.6,
        max_frames_for_classification: int = 5,
    ):
        """
        Initialize VideoClassifier.

        Args:
            ollama_client: OllamaClient instance
            confidence_threshold: Minimum confidence for classification
            max_frames_for_classification: Max frames to use for classification
        """
        self.client = ollama_client
        self.confidence_threshold = confidence_threshold
        self.max_frames_for_classification = max_frames_for_classification

    def classify(
        self,
        video_path: Path,
        frames: List[FrameInfo],
        frame_extractor: FrameExtractor,
    ) -> ClassificationResult:
        """
        Classify a video into a category.

        Args:
            video_path: Path to video file
            frames: List of extracted frames
            frame_extractor: FrameExtractor instance

        Returns:
            ClassificationResult object
        """
        # Limit frames for classification
        frames_to_use = frames[: self.max_frames_for_classification]

        if not frames_to_use:
            # Default to "other" if no frames
            return ClassificationResult(
                category="other",
                confidence=0.0,
                description="No frames available for classification",
                raw_response="",
            )

        # Extract frame data
        frame_images = []
        for frame_info in frames_to_use:
            try:
                frame_data = frame_extractor.extract_frame_data(
                    video_path, frame_info
                )
                if frame_data:
                    frame_images.append(frame_data)
            except Exception as e:
                logger.warning(f"Failed to extract frame data: {e}")
                continue

        if not frame_images:
            return ClassificationResult(
                category="other",
                confidence=0.0,
                description="Failed to extract frame data",
                raw_response="",
            )

        # Generate classification prompt
        prompt = self._create_classification_prompt()

        # Get model response
        try:
            response = self.client.generate_response(prompt, frame_images)
        except Exception as e:
            logger.error(f"Classification failed: {e}")
            return ClassificationResult(
                category="other",
                confidence=0.0,
                description=f"Classification error: {e}",
                raw_response="",
            )

        # Parse response
        category, confidence, description = self._parse_classification_response(
            response
        )

        return ClassificationResult(
            category=category,
            confidence=confidence,
            description=description,
            raw_response=response,
        )

    def _create_classification_prompt(self) -> str:
        """
        Create a prompt for video classification.

        Returns:
            Prompt string
        """
        categories_str = "、".join(self.CATEGORIES)

        prompt = f"""请将这个视频分类到以下类别之一：{categories_str}

分析提供的视频帧，确定最合适的类别。

类别说明：
- 绘画：展示绘画、素描、上色或其他艺术创作的视频
- 教程：教授技能或概念的教育性或指导性内容
- vlog：个人视频博客、评论或生活方式内容
- 娱乐：喜剧、游戏、音乐视频或其他娱乐内容
- 体育：运动、体育活动或健身内容
- 其他：不符合以上类别的内容

请按以下格式回复：
类别：[类别名称]
置信度：[高/中/低]
描述：[简要说明你的选择]

请提供分析："""

        return prompt

    def _parse_classification_response(
        self, response: str
    ) -> Tuple[str, float, str]:
        """
        Parse the model's classification response.

        Args:
            response: Raw response text from model

        Returns:
            Tuple of (category, confidence, description)
        """
        response_lower = response.lower()

        # Extract category
        category = "其他"
        for cat in self.CATEGORIES:
            if cat.lower() in response_lower or cat in response:
                category = cat
                break

        # Extract confidence
        confidence = 0.5
        if "高" in response or "high" in response_lower:
            confidence = 0.8
        elif "中" in response or "medium" in response_lower:
            confidence = 0.6
        elif "低" in response or "low" in response_lower:
            confidence = 0.4

        # Extract description (everything after "描述：" or "Description:")
        description = response
        for marker in ["描述：", "description:", "描述:"]:
            if marker in response_lower:
                parts = response_lower.split(marker, 1)
                if len(parts) > 1:
                    description = parts[1].strip()
                    break

        return category, confidence, description.strip()

    def batch_classify(
        self,
        videos: List[Tuple[Path, List[FrameInfo]]],
        frame_extractor: FrameExtractor,
    ) -> List[ClassificationResult]:
        """
        Classify multiple videos.

        Args:
            videos: List of (video_path, frames) tuples
            frame_extractor: FrameExtractor instance

        Returns:
            List of ClassificationResult objects
        """
        results = []

        for video_path, frames in videos:
            try:
                result = self.classify(video_path, frames, frame_extractor)
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to classify {video_path}: {e}")
                results.append(
                    ClassificationResult(
                        category="other",
                        confidence=0.0,
                        description=f"Error: {e}",
                        raw_response="",
                    )
                )

        return results

    def get_category_stats(
        self, results: List[ClassificationResult]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get statistics about classification results.

        Args:
            results: List of ClassificationResult objects

        Returns:
            Dictionary with category statistics
        """
        stats = {}

        for result in results:
            category = result.category

            if category not in stats:
                stats[category] = {
                    "count": 0,
                    "total_confidence": 0.0,
                    "descriptions": [],
                }

            stats[category]["count"] += 1
            stats[category]["total_confidence"] += result.confidence
            stats[category]["descriptions"].append(result.description)

        # Calculate average confidence
        for category in stats:
            if stats[category]["count"] > 0:
                stats[category]["avg_confidence"] = (
                    stats[category]["total_confidence"] / stats[category]["count"]
                )

        return stats
