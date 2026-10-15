"""Analysis pipeline: staged, cached, resumable (design.md D5).

Stages per content: probe → frames → audio → transcribe → analyze →
finalize. Every stage's artifacts are cached under
``<data>/analysis/<sha[:2]>/<sha>/`` keyed by ``PIPELINE_VERSION``; a stage
whose cached artifact exists is skipped on retry. Analysis writes only
``ai_*`` columns — user edits are protected by db.py.
"""

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from library import media
from library.config import LibraryConfig
from library.db import Library
from library.omlx_client import OmlxClient, OmlxError, extract_json

logger = logging.getLogger(__name__)

#: Bump when prompts or stage logic change so caches invalidate.
PIPELINE_VERSION = "1"

SPEECH_RATIO_THRESHOLD = 0.05


def _validate_analysis_result(result: Dict[str, Any]) -> None:
    """Reject JSON-formatted error replies masquerading as results.

    Raises OmlxError (task fails, retryable) when none of the substantive
    fields is present, e.g. {"error": ..., "status": "failed"}.
    """
    if any(result.get(k) for k in REQUIRED_ANALYSIS_KEYS):
        return
    detail = result.get("error") or result.get("message") or str(result)[:200]
    raise OmlxError(f"model returned no analysis content: {detail}")


MAX_ANALYSIS_FRAMES = 8
REPRESENTATIVE_SCREENSHOTS = 2

ROLE_VALUES = ("author", "presenter", "featured_painter", "reposting_account")

#: a parsed reply only counts as an analysis if at least one substantive
#: field is present — models sometimes answer with a JSON-formatted error
#: message like {"error": ..., "status": "failed"}, which extract_json
#: would happily accept as a result
REQUIRED_ANALYSIS_KEYS = ("title", "summary", "category", "topic", "form")

ANALYSIS_PROMPT_VERSION = "5"

ANALYSIS_SYSTEM_PROMPT = (
    "你是一个中文视频资料库的分析助手。视频可能属于任何领域（绘画、书法、"
    "编程、设计、生活、其他），如实按内容判断类别，不预设领域。用户提供视频"
    "关键帧截图和语音转写文本，你输出严格的 JSON 结构化分析结果。只依据截图"
    "和转写中有依据的信息，证据不足的字段留空或填 null，不要编造。"
    "你的回复必须以 { 开头、以 } 结尾，不要输出任何解释、思考过程或多余文字。"
)

ANALYSIS_USER_PROMPT = (
    "直接输出一个 JSON 对象（第一个字符必须是 {），分析所提供的视频关键帧"
    "（按顺序提供）与转写文本，字段如下：\n"
    '{"title": "标题建议，10字内", "summary": "内容简介，100~200字，'
    '说明内容主题、材料工具、步骤和具体技法/要点", '
    '"category": "主分类，按内容判断，如：绘画/书法/编程/设计/生活/其他", '
    '"topic": "主题，如：水彩人物、LangChain入门", '
    '"form": "视频形式，如：教程/创作过程/作品展示/访谈/其他", '
    '"medium": "创作媒介或技术栈，如：水彩/油画/Python，没有则 null", '
    '"tools": ["工具材料或软件框架"], "techniques": ["技法或技术要点"], '
    '"tags": ["标签，最多8个"], '
    '"artists": [{"name": "创作者名（绘画类为画家）", "role": "author|'
    'presenter|featured_painter 之一", "evidence": "判断依据，如落款/水印/'
    '语音自述/字幕", "confidence": "high|medium|low"}], '
    '"source_accounts": [{"name": "来源账号名", "evidence": "依据", '
    '"confidence": "high|medium|low"}], '
    '"representative_frames": [{"index": 0, "reason": "选择原因，如成品画面"}]'
    "}\n注意：发布者不等于作品作者；被介绍的画家 role 用 featured_painter；"
    "信息不足时对应数组留空。再次强调：只输出 JSON，不要任何其他文字。"
    "artists 只记录有明确证据的创作者或主讲人；访谈嘉宾、学员、幕僚、"
    "主持人和仅被口头提及的人不能因此被当成作者。非绘画视频不得使用"
    " featured_painter 角色。不得把‘主讲人’‘演讲者’等泛称当姓名；"
    "无法确认身份时留空。节目或课程所属机构保存在 source_accounts，"
    "不得仅凭发布账号猜测作者。"
    "明确的创作者或主要授课者 role 用 author；需保留的出镜嘉宾用 presenter，"
    "二者不能混淆。"
    "\n\n转写文本：\n{transcript}"
)


@dataclass
class StageProgress:
    """Progress callback payload for the worker/UI."""

    stage: str
    progress: float  # 0..1


def _stage_cache_ok(cache_file: Path, version: str) -> bool:
    if not cache_file.exists():
        return False
    try:
        return (
            cache_file.read_text(encoding="utf-8").strip()
            and json.loads(cache_file.read_text(encoding="utf-8")).get(
                "pipeline_version"
            )
            == version
        )
    except (json.JSONDecodeError, OSError):
        return False


def transcription_windows(duration: float, budget: float) -> List[tuple]:
    """Sample opening and middle sections, merging overlaps on short videos."""
    if budget <= 0:
        raise ValueError("transcribe_budget_s must be positive")
    if duration <= 0:
        return []
    if duration <= budget:
        return [(0.0, duration)]
    windows = [
        (0.0, budget * 0.4),
        (duration / 3, budget * 0.3),
        (duration / 2, budget * 0.3),
    ]
    merged = []
    for start, span in windows:
        end = min(start + span, duration)
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return [(start, end - start) for start, end in merged]


class AnalysisPipeline:
    """Runs the staged analysis for one content at a time."""

    def __init__(self, lib: Library, cfg: LibraryConfig, client: OmlxClient):
        self.lib = lib
        self.cfg = cfg
        self.client = client

    # -- helpers -----------------------------------------------------------

    def _content_file(self, content_id: int) -> Path:
        video = self.lib.get_video(content_id)
        if video is None:
            raise OmlxError(f"content {content_id} not found")
        for f in video["files"]:
            if f["status"] == "available" and Path(f["path"]).exists():
                return Path(f["path"])
        raise OmlxError(f"content {content_id} has no accessible file")

    def _cache_dir(self, sha256: str) -> Path:
        return self.cfg.analysis_cache_dir / sha256[:2] / sha256

    def _load_stage(
        self, cache_dir: Path, name: str, version: str = PIPELINE_VERSION
    ) -> Optional[Dict]:
        cache_file = cache_dir / f"{name}.json"
        if _stage_cache_ok(cache_file, version):
            return json.loads(cache_file.read_text(encoding="utf-8"))
        return None

    def _save_stage(
        self,
        cache_dir: Path,
        name: str,
        data: Dict,
        version: str = PIPELINE_VERSION,
    ) -> None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        data = {**data, "pipeline_version": version}
        (cache_dir / f"{name}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    # -- stages ------------------------------------------------------------

    def _stage_probe(self, cache_dir: Path, video_file: Path, content_id: int) -> Dict:
        cached = self._load_stage(cache_dir, "probe")
        if cached:
            return cached
        info = media.probe(video_file)
        self.lib.set_media_info(
            content_id,
            info.duration_s,
            info.width,
            info.height,
            info.video_codec,
            info.audio_codec,
            info.container,
        )
        data = {
            "duration_s": info.duration_s,
            "has_audio": info.audio_codec is not None,
        }
        self._save_stage(cache_dir, "probe", data)
        return data

    def _stage_frames(self, cache_dir: Path, video_file: Path) -> Dict:
        cached = self._load_stage(cache_dir, "frames")
        if cached and all((cache_dir / f["name"]).exists() for f in cached["frames"]):
            return cached
        settings = self.cfg.analysis
        sampled = media.sample_frames(
            video_file,
            cache_dir,
            max_frames=int(settings.get("max_frames", 10)),
            interval_s=float(settings.get("frame_interval_s", 10.0)),
        )
        data = {
            "frames": [
                {"name": f.path.name, "timestamp_s": f.timestamp_s, "index": f.index}
                for f in sampled
            ]
        }
        self._save_stage(cache_dir, "frames", data)
        return data

    def _stage_audio(self, cache_dir: Path, video_file: Path, probe: Dict) -> Dict:
        cached = self._load_stage(cache_dir, "audio")
        if cached and (cache_dir / cached["wav"]).exists():
            return cached
        if not probe.get("has_audio"):
            data = {"wav": None, "speech_ratio": 0.0, "reason": "no_audio_track"}
            self._save_stage(cache_dir, "audio", data)
            return data
        wav = cache_dir / "audio.wav"
        media.extract_audio(video_file, wav)
        ratio = media.detect_speech_ratio(wav)
        data = {"wav": wav.name, "speech_ratio": round(ratio, 3)}
        self._save_stage(cache_dir, "audio", data)
        return data

    def _stage_transcribe(
        self, cache_dir: Path, video_file: Path, probe: Dict, audio: Dict
    ) -> Dict:
        cached = self._load_stage(cache_dir, "transcribe")
        if cached:
            return cached
        if audio.get("wav") is None:
            data = {"segments": [], "status": "none"}
        elif audio["speech_ratio"] < SPEECH_RATIO_THRESHOLD:
            data = {"segments": [], "status": "no_speech"}
        else:
            chunk_s = int(self.cfg.analysis.get("transcribe_chunk_s", 600))
            duration = probe["duration_s"] or 0.0
            budget = float(self.cfg.analysis.get("transcribe_budget_s", 300))
            windows = transcription_windows(duration, budget)
            if chunk_s <= 0:
                raise ValueError("transcribe_chunk_s must be positive")
            segments: List[Dict[str, Any]] = []
            try:
                chunks = []
                for start, span in windows:
                    end = start + span
                    while start < end:
                        length = min(chunk_s, end - start)
                        chunks.append((start, length))
                        start += length
                for start, span in chunks:
                    chunk_wav = cache_dir / "chunk.wav"
                    media.extract_audio(
                        video_file, chunk_wav, start_s=start, duration_s=span
                    )
                    text = self.client.transcribe(
                        chunk_wav, self.cfg.omlx.asr_model, language="zh"
                    ).strip()
                    if text:
                        segments.append(
                            {
                                "start_s": round(start, 2),
                                "end_s": round(start + span, 2),
                                "text": text,
                            }
                        )
            except OmlxError as exc:
                # transcription service failed → visual-only fallback with an
                # explicit status (spec §4); the error stays visible in logs
                logger.warning(
                    "transcription failed for %s, falling back to " "visual-only: %s",
                    video_file,
                    exc,
                )
                data = {
                    "segments": segments,
                    "status": "failed",
                    "error": str(exc)[:500],
                }
            else:
                data = {
                    "segments": segments,
                    "status": "ok",
                    "sampled": duration > budget,
                    "sample_windows": windows,
                }
        self._save_stage(cache_dir, "transcribe", data)
        return data

    def _stage_analyze(self, cache_dir: Path, frames: Dict, transcribe: Dict) -> Dict:
        cached = self._load_stage(cache_dir, "analyze", version=ANALYSIS_PROMPT_VERSION)
        if cached:
            result = cached.get("result")
            try:
                if not isinstance(result, dict):
                    raise OmlxError("analysis cache has no result object")
                _validate_analysis_result(result)
                return result
            except OmlxError:
                logger.warning("invalid analysis cache at %s; regenerating", cache_dir)
        frame_entries = frames["frames"][:MAX_ANALYSIS_FRAMES]
        image_paths = [cache_dir / f["name"] for f in frame_entries]
        transcript_text = (
            "\n".join(
                f"[{s['start_s']:.0f}s-{s['end_s']:.0f}s] {s['text']}"
                for s in transcribe.get("segments", [])
            )
            or "（无转写文本，仅根据画面分析）"
        )
        # .replace, not .format: the template embeds literal JSON braces
        prompt = ANALYSIS_USER_PROMPT.replace("{transcript}", transcript_text)
        if transcribe.get("sampled"):
            prompt += (
                "\n转写仅来自开头和中段的采样片段，不覆盖完整视频。"
                "结合全程采样截图描述有依据的内容，不要声称涵盖完整步骤。"
            )
        for attempt in (1, 2, 3):
            user_msg = (
                prompt
                if attempt == 1
                else (
                    "你上一条回复无法解析为 JSON。请针对同样的关键帧和转写文本重新"
                    "分析，这次直接输出一个以 { 开头、以 } 结尾的 JSON 对象，符合"
                    "给定字段结构，不要任何思考过程或多余文字。\n\n" + prompt
                )
            )
            reply = self.client.chat_vision(
                self.cfg.omlx.vision_model,
                ANALYSIS_SYSTEM_PROMPT,
                user_msg,
                image_paths,
            )
            try:
                result = extract_json(reply)
                break
            except OmlxError:
                # model occasionally leads with reasoning prose (thinking
                # mode); retry the FULL request — an image-less retry leaves
                # the model nothing to analyze and it answers all-null
                logger.warning(
                    "analysis reply not JSON (attempt %d, %d chars), retrying",
                    attempt,
                    len(reply),
                )
        else:
            raise OmlxError(
                f"model did not return parseable JSON after 3 attempts:"
                f" {reply[:300]}"
            )
        _validate_analysis_result(result)
        self._save_stage(
            cache_dir,
            "analyze",
            {"result": result},
            version=ANALYSIS_PROMPT_VERSION,
        )
        return result

    def _stage_finalize(
        self, cache_dir: Path, content_id: int, frames: Dict, result: Dict
    ) -> None:
        # Reject empty/malformed results before replacing existing metadata.
        _validate_analysis_result(result)
        # copy representative frames into the screenshots dir
        picks = result.get("representative_frames") or []
        indexes = [
            p.get("index")
            for p in picks
            if isinstance(p, dict) and p.get("index") is not None
        ]
        valid = {f["index"] for f in frames["frames"]}
        indexes = [i for i in indexes if i in valid][:REPRESENTATIVE_SCREENSHOTS]
        if not indexes and frames["frames"]:
            count = len(frames["frames"])
            indexes = sorted({0, count // 2} & valid)
        shots = []
        for i, entry in enumerate(f for f in frames["frames"] if f["index"] in indexes):
            src = cache_dir / entry["name"]
            dst_dir = self.cfg.screenshots_dir / "full"
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / f"{content_id}_{i}_{entry['name']}"
            shutil.copyfile(src, dst)
            shots.append(
                {
                    "path": str(dst),
                    "timestamp_s": entry["timestamp_s"],
                    "is_representative": True,
                }
            )
        self.lib.replace_screenshots(content_id, shots)

        ai_result: Dict[str, Any] = {
            k: result.get(k) for k in ("title", "summary", "category", "topic", "form")
        }
        ai_result["tags"] = [str(t) for t in (result.get("tags") or [])][:8]
        ai_result["evidence"] = {
            "medium": result.get("medium"),
            "tools": result.get("tools"),
            "techniques": result.get("techniques"),
        }
        self.lib.write_ai_result(content_id, ai_result, PIPELINE_VERSION)

        # a painter the user set explicitly wins: re-analysis must not
        # re-add AI-guessed painters next to it (spec: user_* > ai_*)
        user_painter = self.lib.has_user_painter(content_id)
        if not user_painter:
            links = []
            for artist in result.get("artists") or []:
                if not isinstance(artist, dict) or not artist.get("name"):
                    continue
                name = str(artist["name"]).strip()
                if not name or name in {"主讲人", "演讲者", "讲师", "主持人", "嘉宾", "未知"}:
                    continue
                role = artist.get("role")
                if role not in {"author", "presenter", "featured_painter"}:
                    continue
                if role == "featured_painter" and result.get("category") not in {"绘画", "书法"}:
                    role = "presenter"
                person_id = self.lib.upsert_person(
                    "painter",
                    name,
                    evidence=artist.get("evidence"),
                )
                links.append((person_id, role))
            self.lib.replace_ai_painters(content_id, links)
        for account in result.get("source_accounts") or []:
            if not isinstance(account, dict) or not account.get("name"):
                continue
            person_id = self.lib.upsert_person(
                "source_account",
                str(account["name"]),
                evidence=account.get("evidence"),
            )
            self.lib.link_person(content_id, person_id, "reposting_account")
        self.lib.reindex_content(content_id)

    # -- entry point ---------------------------------------------------------

    def analyze_content(
        self,
        content_id: int,
        task_id: Optional[int] = None,
        progress: Optional[Callable[[StageProgress], None]] = None,
    ) -> Dict[str, Any]:
        """Run the full pipeline for one content; raises on failure."""
        report = progress or (lambda p: None)

        video = self.lib.get_video(content_id)
        if video is None:
            raise OmlxError(f"content {content_id} not found")
        self.lib.set_analysis_status(content_id, "analyzing")
        cache_dir = self._cache_dir(video["sha256"])
        video_file = self._content_file(content_id)

        stages = ("probe", "frames", "audio", "transcribe", "analyze", "finalize")
        total = len(stages)

        def report_stage(stage: str) -> None:
            if task_id is not None:
                idx = stages.index(stage)
                self.lib.update_task(
                    task_id, stage=stage, progress=round(idx / total, 2)
                )
            report(StageProgress(stage, 0.0))

        report_stage("probe")
        probe = self._stage_probe(cache_dir, video_file, content_id)
        report_stage("frames")
        frames = self._stage_frames(cache_dir, video_file)
        report_stage("audio")
        audio = self._stage_audio(cache_dir, video_file, probe)
        report_stage("transcribe")
        transcribe = self._stage_transcribe(cache_dir, video_file, probe, audio)
        self.lib.replace_transcripts(
            content_id,
            transcribe.get("segments", []),
            transcribe.get("status", "ok"),
        )
        report_stage("analyze")
        result = self._stage_analyze(cache_dir, frames, transcribe)
        report_stage("finalize")
        self._stage_finalize(cache_dir, content_id, frames, result)
        if task_id is not None:
            self.lib.update_task(task_id, progress=1.0)
        return result
