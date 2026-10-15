"""Content analysis for detailed video understanding."""

import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from pathlib import Path

from src.video_parser.frame_extractor import FrameInfo, FrameExtractor
from src.video_parser.video_classifier import OllamaClient

logger = logging.getLogger(__name__)


@dataclass
class ContentAnalysis:
    """Result of content analysis."""

    category: str
    summary: str
    key_elements: List[str]
    detailed_description: str
    confidence: float
    frame_analyses: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "category": self.category,
            "summary": self.summary,
            "key_elements": self.key_elements,
            "detailed_description": self.detailed_description,
            "confidence": round(self.confidence, 3),
            "frame_analyses": self.frame_analyses,
        }


class ContentAnalyzer:
    """Analyze video content based on classification category."""

    def __init__(
        self,
        ollama_client: OllamaClient,
        batch_size: int = 3,
    ):
        """
        Initialize ContentAnalyzer.

        Args:
            ollama_client: OllamaClient instance
            batch_size: Number of frames to analyze in one batch
        """
        self.client = ollama_client
        self.batch_size = batch_size

    def analyze(
        self,
        video_path: Path,
        category: str,
        frames: List[FrameInfo],
        frame_extractor: FrameExtractor,
    ) -> ContentAnalysis:
        """
        Analyze video content based on its category.

        Args:
            video_path: Path to video file
            category: Video category from classification
            frames: List of extracted frames
            frame_extractor: FrameExtractor instance

        Returns:
            ContentAnalysis object
        """
        # Extract frame data in batches
        all_frame_data = []
        for i in range(0, len(frames), self.batch_size):
            batch_frames = frames[i : i + self.batch_size]
            batch_data = self._extract_batch_frames(video_path, batch_frames, frame_extractor)
            all_frame_data.extend(batch_data)

        if not all_frame_data:
            return ContentAnalysis(
                category=category,
                summary="Unable to analyze content",
                key_elements=[],
                detailed_description="No frame data available",
                confidence=0.0,
                frame_analyses=[],
            )

        # Limit frames to avoid context overflow - max 3 frames for analysis
        all_frame_data = all_frame_data[:3]

        # Generate category-specific prompt
        prompt = self._create_analysis_prompt(category)

        # Get analysis from model
        try:
            response = self.client.generate_response(prompt, all_frame_data)
            logger.info(f"Raw AI response for {category}:\n{response}\n")  # Log full response
        except Exception as e:
            logger.error(f"Content analysis failed: {e}")
            return ContentAnalysis(
                category=category,
                summary=f"Analysis error: {e}",
                key_elements=[],
                detailed_description="",
                confidence=0.0,
                frame_analyses=[],
            )

        # Parse response
        analysis = self._parse_analysis_response(category, response)

        return analysis

    def _extract_batch_frames(
        self,
        video_path: Path,
        frames: List[FrameInfo],
        frame_extractor: FrameExtractor,
    ) -> List[bytes]:
        """Extract frame data for a batch of frames."""
        frame_data_list = []

        for frame_info in frames:
            try:
                data = frame_extractor.extract_frame_data(video_path, frame_info)
                if data:
                    frame_data_list.append(data)
            except Exception as e:
                logger.warning(f"Failed to extract frame {frame_info.frame_number}: {e}")

        return frame_data_list

    def _create_analysis_prompt(self, category: str) -> str:
        """Create category-specific analysis prompt."""

        prompts = {
            "绘画": """分析这个绘画/艺术视频并提供：

1. 摘要：正在绘制什么？
2. 关键元素：艺术风格（素描、水彩、数字艺术等）、主题、技能水平
3. 媒介：使用了什么工具/媒介？
4. 技法：展示了什么技法？

请按以下格式回复：
摘要：[简要描述]
风格：[从以下选择：素描、水彩、油画、数字艺术、炭笔、铅笔、墨水、粉彩、混合媒介]
主题：[主要主题：人物、风景、静物、抽象、动漫、卡通、写实、奇幻、动物、建筑]
工具：[使用的工具/媒介：铅笔、画笔、数位板、炭笔、粉彩、马克笔、钢笔]
技法：[展示的技法：明暗、晕染、分层、排线、点彩、上釉、洗涤]
水平：[入门/中级/高级/专业]""",

            "教程": """分析这个教程视频并提供：

1. 主题：在教授什么？
2. 领域：属于哪个领域（艺术、烹饪、科技、手工、健身、音乐、语言）？
3. 难度：入门/中级/高级
4. 形式：逐步教学、技巧、完整课程、快速技巧

请按以下格式回复：
主题：[正在教授的内容]
领域：[从以下选择：艺术、烹饪、科技、手工、健身、音乐、语言、其他领域]
难度：[入门/中级/高级]
形式：[逐步教学/技巧/完整课程/快速技巧]
关键概念：[主要话题列表]""",

            "vlog": """分析这个vlog并提供：

1. 类型：日常生活、旅行、评论、观点等
2. 主题：主要内容
3. 氛围：信息性、娱乐性、情感、励志、幽默、轻松
4. 场景：室内、户外、工作室、活动、旅行地

请按以下格式回复：
类型：[日常生活/旅行/评论/观点/问答/故事/幕后]
主题：[主要话题]
氛围：[信息性/娱乐性/情感/励志/幽默/轻松]
场景：[室内/户外/工作室/活动/旅行地]""",

            "娱乐": """分析这个娱乐视频并提供：

1. 类型：喜剧、游戏、音乐、短剧等
2. 内容描述：视频中发生了什么
3. 制作质量：专业、半专业、业余
4. 受众：全年龄、青少年、成人、亲子

请按以下格式回复：
类型：[喜剧/游戏/音乐/短剧/挑战/反应/动画]
描述：[简要描述]
制作：[专业/半专业/业余]
受众：[全年龄/青少年/成人/亲子]""",

            "体育": """分析这个体育视频并提供：

1. 类型：团队运动、个人运动、极限运动等
2. 运动项目：展示了什么运动
3. 水平：专业、业余、娱乐
4. 活动：正在发生什么（比赛、教学、集锦等）

请按以下格式回复：
类型：[团队运动/个人运动/极限运动/水上运动/冬季运动/格斗运动/赛车]
运动项目：[具体运动]
水平：[专业/业余/娱乐]
活动：[正在发生什么]""",

            "其他": """分析这个视频并提供：

1. 内容类型：纪录片、访谈、演示等
2. 主题：主要内容
3. 描述：展示/讨论了什么
4. 显著特征：任何独特的特征

请按以下格式回复：
类型：[内容类型]
主题：[主要话题]
描述：[展示了什么]
显著特征：[独特特征]""",
        }

        return prompts.get(category, prompts["其他"])

    def _parse_analysis_response(self, category: str, response: str) -> ContentAnalysis:
        """Parse the model's analysis response with improved extraction."""

        # Extract summary/topic with better parsing (support both Chinese and English)
        summary = ""
        markers = ["摘要：", "摘要:", "summary:", "主题：", "topic:", "描述：", "description:", "类型：", "type:"]

        for marker in markers:
            if marker in response:
                parts = response.split(marker, 1)
                if len(parts) > 1:
                    # Extract content after marker
                    content = parts[1].strip()
                    # Get first line or sentence
                    summary = content.split("\n")[0].split("。")[0].split(".")[0].strip()
                    if len(summary) > 5:  # Only use if we got something meaningful
                        break

        # Extract key elements (lines with colons)
        key_elements = []
        for line in response.split("\n"):
            line = line.strip()
            # Look for structured key-value pairs (contains colon)
            if "：" in line or ":" in line:
                if line and 3 < len(line) < 200:  # Reasonable line length
                    key_elements.append(line)

        # Calculate confidence based on response quality
        confidence = 0.5
        if len(summary) > 10:
            confidence = 0.7
        if len(key_elements) >= 3:
            confidence = 0.8
        if len(summary) > 20 and len(key_elements) >= 3:
            confidence = 0.9

        return ContentAnalysis(
            category=category,
            summary=summary,
            key_elements=key_elements[:15],  # Limit to 15 elements
            detailed_description=response,
            confidence=confidence,
            frame_analyses=[],
        )

    def batch_analyze(
        self,
        videos: List[tuple[Path, str, List[FrameInfo]]],
        frame_extractor: FrameExtractor,
    ) -> List[ContentAnalysis]:
        """Analyze multiple videos."""
        results = []

        for video_path, category, frames in videos:
            try:
                analysis = self.analyze(video_path, category, frames, frame_extractor)
                results.append(analysis)
            except Exception as e:
                logger.error(f"Failed to analyze {video_path}: {e}")
                results.append(
                    ContentAnalysis(
                        category=category,
                        summary=f"Error: {e}",
                        key_elements=[],
                        detailed_description="",
                        confidence=0.0,
                        frame_analyses=[],
                    )
                )

        return results
