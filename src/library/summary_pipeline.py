"""Generic single-video summary: transcript polish + chaptered study notes."""

from contextlib import nullcontext
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Callable
from uuid import uuid4

from json_repair import loads as repair_json
from pydantic import BaseModel, Field, ValidationError

from library import media
from library.config import LibraryConfig, SUPPORTED_EXTENSIONS
from library.course_documents import write_text
from library.course_pipeline import Segment
from library.hashing import file_sha256
from library.omlx_client import OmlxClient, OmlxError, extract_json

VERSION = 1
ASR_CHUNK_S = 15
# 分块处理上限：约 20 万字（高语速约 5 小时），超过则失败提示拆分。
# 优化稿按 8 千字/块分段润色，笔记按 1.2 万字/块先提取草稿再汇总。
MAX_TRANSCRIPT_CHARS = 200_000
POLISH_CHUNK_CHARS = 8_000
NOTES_CHUNK_CHARS = 12_000
POLISH_MAX_TOKENS = 16_384


class Chapter(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    points: list[str] = Field(min_length=1, max_length=50)


class DraftResult(BaseModel):
    """单块转写提取出的章节草稿。"""

    chapters: list[Chapter] = Field(min_length=1, max_length=40)


class Notes(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    chapters: list[Chapter] = Field(min_length=1, max_length=60)
    takeaways: list[str] = Field(default_factory=list, max_length=30)


def chunk_segments(segments: list[dict], limit: int) -> list[list[dict]]:
    """按累计字数切块，块边界落在转写分段上。"""
    chunks, current, size = [], [], 0
    for segment in segments:
        current.append(segment)
        size += len(segment.get("text") or "")
        if size >= limit:
            chunks.append(current)
            current, size = [], 0
    if current:
        chunks.append(current)
    return chunks


def dump(path: Path, value: object) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def format_timecode(seconds: float) -> str:
    total = max(0, int(seconds))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _frame_timestamp(path: Path) -> float:
    """Recover the sample time from a media.sample_frames filename."""
    try:
        return float(path.stem.split("_t")[-1])
    except ValueError:
        return 0.0


class SummaryPipeline:
    """Creates and processes single-video summaries under data/summaries."""

    def __init__(
        self,
        cfg: LibraryConfig,
        client: OmlxClient,
        model_factory=None,
    ):
        self.cfg = cfg
        self.client = client
        self.model_factory = model_factory
        self.root = cfg.data_dir / "summaries"

    def folder(self, summary_id: str) -> Path:
        if len(summary_id) != 32 or any(
            c not in "0123456789abcdef" for c in summary_id
        ):
            raise FileNotFoundError("总结不存在")
        return self.root / summary_id

    def read(self, summary_id: str) -> dict:
        return load(self.folder(summary_id) / "summary.json")

    def list_items(self) -> list[dict]:
        return sorted(
            [load(p) for p in self.root.glob("*/summary.json")],
            key=lambda item: item["created"],
            reverse=True,
        )

    def create(self, video: Path, include_frames: bool = True) -> dict:
        video = Path(video).expanduser().resolve()
        if not video.is_file() or video.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError("请选择本机 MP4、MKV 或 MOV 视频文件")
        try:
            with video.open("rb") as file:
                file.read(1)
        except PermissionError as exc:
            raise ValueError(
                "系统拒绝读取视频，请将文件复制到项目目录，或为启动服务的终端授予文件访问权限"
            ) from exc
        value = {
            "id": uuid4().hex,
            "title": video.stem,
            "path": str(video),
            "source_sha256": file_sha256(video),
            "created": datetime.now(timezone.utc).isoformat(),
            "status": "queued",
            "stage": "等待处理",
            "progress": 0,
            "error": "",
            "include_frames": include_frames,
        }
        dump(self.folder(value["id"]) / "summary.json", value)
        return value

    def delete(self, summary_id: str) -> None:
        shutil.rmtree(self.folder(summary_id))

    # -- processing ------------------------------------------------------------

    def run(
        self,
        summary_id: str,
        progress: Callable[[str, float], None] = lambda *_: None,
    ) -> None:
        item = self.read(summary_id)
        folder = self.folder(summary_id)
        runtime = (
            self.model_factory()
            if self.model_factory
            else nullcontext((self.cfg, self.client))
        )
        item.update(status="running", error="")

        def report(stage: str, fraction: float) -> None:
            item.update(stage=stage, progress=fraction)
            dump(folder / "summary.json", item)
            progress(stage, fraction)

        try:
            with runtime as (cfg, client):
                report("读取视频", 0.01)
                video = Path(item["path"])
                sha = file_sha256(video)
                item["source_sha256"] = sha
                info = media.probe(video)
                if not math.isfinite(info.duration_s) or info.duration_s <= 0:
                    raise ValueError("无法读取视频时长")
                item["duration"] = info.duration_s
                cache = folder / "cache" / sha[:20]

                transcript = self.transcribe(video, cache, info, cfg, client, report)
                dump(folder / "transcript.json", transcript)
                total_chars = sum(len(s["text"]) for s in transcript["segments"])
                if total_chars > MAX_TRANSCRIPT_CHARS:
                    raise ValueError(
                        "视频过长：转写文本超出单次总结可处理范围，请拆分视频后分别总结"
                    )

                frames: list[tuple[Path, float]] = []
                frames_dir = folder / "frames"
                if item["include_frames"]:
                    report("采样关键帧", 0.42)
                    if not any(frames_dir.glob("*.jpg")):
                        media.sample_frames(
                            video,
                            frames_dir,
                            max_frames=int(cfg.analysis.get("max_frames", 10)),
                        )
                    frames = [
                        (path, _frame_timestamp(path))
                        for path in sorted(
                            frames_dir.glob("*.jpg"), key=lambda p: p.name
                        )
                    ]

                report("生成优化稿", 0.5)
                polished = self.polish(transcript, cache, cfg, client)
                write_text(
                    folder / "transcript-polished.md",
                    f"# 优化稿 · {item['title']}\n\n{polished}\n",
                )

                report("生成学习笔记", 0.7)
                notes = self.notes(
                    transcript, frames, info.duration_s, cache, cfg, client
                )
                write_text(folder / "notes.md", render_notes(item, notes, frames))
                item.update(status="done", error="")
                report("完成", 1)
        except Exception as exc:
            item.update(status="failed", error=str(exc)[:1000])
            dump(folder / "summary.json", item)
            raise

    def transcribe(
        self, video, cache, info, cfg, client, report
    ) -> dict:
        if not info.audio_codec:
            raise ValueError("视频没有音轨，未检测到语音内容")
        identity = {
            "model": cfg.omlx.asr_model,
            "endpoint": cfg.omlx.base_url,
            "chunk": ASR_CHUNK_S,
            "version": 1,
        }
        key = hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest()[:20]
        asr_dir = cache / ("asr-" + key)
        segments = []
        count = math.ceil(info.duration_s / ASR_CHUNK_S)
        for i in range(count):
            report(f"音频转写 {i + 1}/{count}", 0.05 + 0.35 * i / count)
            target = asr_dir / f"{i:05}.json"
            if target.exists():
                segment = Segment.model_validate(load(target))
            else:
                wav = asr_dir / f"{i:05}.wav"
                wav.parent.mkdir(parents=True, exist_ok=True)
                start, end = i * ASR_CHUNK_S, min(
                    (i + 1) * ASR_CHUNK_S, info.duration_s
                )
                media.extract_audio(video, wav, start, end - start)
                text = client.transcribe(wav, cfg.omlx.asr_model, language="zh").strip()
                segment = Segment(start=start, end=end, text=text)
                dump(target, segment.model_dump())
                wav.unlink(missing_ok=True)
            segments.append(segment.model_dump())
        if not any(s["text"] for s in segments):
            raise ValueError("未检测到语音内容")
        return {
            "precision": "音频块级近似时间（最长 15 秒），非句级时间戳",
            "segments": segments,
        }

    def _vision_cache(self, cache: Path, cfg) -> Path:
        identity = {
            "model": cfg.omlx.vision_model,
            "url": cfg.omlx.base_url,
            "version": VERSION,
        }
        key = hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest()[:20]
        target = cache / ("vision-" + key)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def polish(self, transcript: dict, cache, cfg, client) -> str:
        """Chunked polish: segments are split by cumulative chars, each chunk
        is polished independently (cached per chunk) and results are joined."""
        vision_dir = self._vision_cache(cache, cfg)
        chunks = chunk_segments(transcript["segments"], POLISH_CHUNK_CHARS)
        parts = []
        for position, chunk in enumerate(chunks):
            target = vision_dir / f"polish-{position:02d}.md"
            if target.exists():
                parts.append(target.read_text(encoding="utf-8"))
                continue
            reply = client.chat(
                cfg.omlx.vision_model,
                [
                    {
                        "role": "system",
                        "content": "你是视频文稿编辑，只输出润色后的正文。",
                    },
                    {
                        "role": "user",
                        "content": "下面是教程视频语音转写的一段（大视频的第 "
                        f"{position + 1}/{len(chunks)} 段，含时间戳仅供理解上下文，"
                        "前后段另有上下文）。请输出这一段的优化稿：纠正错别字与口误、"
                        "去掉语气词和重复、按内容分段，保留全部实质信息；"
                        "不要输出时间戳、段号或小标题，直接输出 Markdown 正文。\n\n"
                        + json.dumps(
                            [
                                {
                                    "start": s["start"],
                                    "end": s["end"],
                                    "text": s["text"],
                                }
                                for s in chunk
                            ],
                            ensure_ascii=False,
                        ),
                    },
                ],
                max_tokens=POLISH_MAX_TOKENS,
            )
            write_text(target, reply.strip())
            parts.append(reply.strip())
        return "\n\n".join(parts)

    def _chat_json(self, cfg, client, prompt: str, schema, raw_prefix: str, cache_dir):
        """One JSON chat call with a single retry; raw replies kept for diagnosis."""
        error = ""
        for attempt in range(2):
            request = prompt + (
                "\n上次输出未通过校验，请修正：" + error if error else ""
            )
            reply = client.chat(
                cfg.omlx.vision_model,
                [
                    {"role": "system", "content": "你是视频笔记助手，只输出 JSON。"},
                    {"role": "user", "content": request},
                ],
            )
            write_text(
                cache_dir / f"{raw_prefix}-{attempt}-{uuid4().hex[:8]}.txt", reply
            )
            try:
                try:
                    value = extract_json(reply)
                except OmlxError:
                    candidate = reply.strip().removesuffix("```").strip()
                    start = candidate.find("{")
                    if start < 0 or not candidate.endswith("}"):
                        raise
                    value = repair_json(candidate[start:], skip_json_loads=True)
                return schema.model_validate(value)
            except (OmlxError, ValidationError, ValueError) as exc:
                error = str(exc)[:1600]
        raise ValueError("模型未返回有效笔记结构（已重试一次）。" + error[:300])

    def notes(
        self, transcript, frames, duration_s, cache, cfg, client
    ) -> Notes:
        """Two-stage notes: per-chunk chapter drafts, then one merge call."""
        vision_dir = self._vision_cache(cache, cfg)
        target = vision_dir / "notes.json"
        if target.exists():
            return Notes.model_validate(load(target))
        segments = transcript["segments"]
        chunks = chunk_segments(segments, NOTES_CHUNK_CHARS)
        drafts = []
        for position, chunk in enumerate(chunks):
            draft_file = vision_dir / f"notes-draft-{position:02d}.json"
            if draft_file.exists():
                drafts.append(DraftResult.model_validate(load(draft_file)))
                continue
            draft = self._chat_json(
                cfg,
                client,
                "以下是教程视频转写的第 "
                f"{position + 1}/{len(chunks)} 段（{chunk[0]['start']:.0f}–"
                f"{chunk[-1]['end']:.0f} 秒）。请提取该段内的章节草稿："
                "title 简短；start_s/end_s 取自转写时间戳（秒）且必须落在该段时间内，"
                "不得编造；points 是可学习的要点或操作步骤。\n"
                "严格输出以下 JSON schema 对应的对象：\n"
                + json.dumps(DraftResult.model_json_schema(), ensure_ascii=False)
                + "\n转写分段："
                + json.dumps(
                    [
                        {"start": s["start"], "end": s["end"], "text": s["text"]}
                        for s in chunk
                    ],
                    ensure_ascii=False,
                ),
                DraftResult,
                f"notes-raw-draft-{position}",
                vision_dir,
            )
            dump(draft_file, draft.model_dump())
            drafts.append(draft)
        merged = self._chat_json(
            cfg,
            client,
            "以下是同一教程视频分段提取出的章节草稿（含起止秒数，按时间排列）。"
            "请汇总为整个视频的最终学习笔记：合并重复或过碎的相邻章节、"
            "保留准确时间码、整理要点去重，并给出 topic 与 takeaways。\n"
            "严格输出以下 JSON schema 对应的对象：\n"
            + json.dumps(Notes.model_json_schema(), ensure_ascii=False)
            + "\n章节草稿："
            + json.dumps(
                [c.model_dump() for d in drafts for c in d.chapters],
                ensure_ascii=False,
            ),
            Notes,
            "notes-raw-merge",
            vision_dir,
        )
        dump(target, merged.model_dump())
        return merged


def sanitize_chapters(
    notes: Notes, duration_s: float
) -> list[tuple[float, float, Chapter]]:
    """Clamp timecodes into the video and drop empty chapters."""
    valid = []
    for chapter in notes.chapters:
        start = max(0.0, min(chapter.start_s, duration_s))
        end = max(0.0, min(chapter.end_s, duration_s))
        if end - start <= 0.01:
            continue
        valid.append((start, end, chapter))
    valid.sort(key=lambda entry: entry[0])
    return valid


def match_frame(start: float, end: float, frames) -> int | None:
    """Nearest sampled frame to the chapter midpoint (code, not model, choice)."""
    if not frames:
        return None
    midpoint = (start + end) / 2
    return min(
        range(len(frames)), key=lambda index: abs(frames[index][1] - midpoint)
    )


def render_notes(item: dict, notes: Notes, frames) -> str:
    text = f"# {notes.topic}\n\n> 来源：{Path(item['path']).name}\n\n"
    for start, end, chapter in sanitize_chapters(notes, item.get("duration", 0)):
        text += (
            f"## {chapter.title}（{format_timecode(start)}–{format_timecode(end)}）\n\n"
        )
        for point in chapter.points:
            text += f"- {point}\n"
        text += "\n"
        frame = match_frame(start, end, frames)
        if frame is not None:
            text += f"![{chapter.title}](frames/{frames[frame][0].name})\n\n"
    if notes.takeaways:
        text += "## 要点总结\n\n"
        for takeaway in notes.takeaways:
            text += f"- {takeaway}\n"
    return text
