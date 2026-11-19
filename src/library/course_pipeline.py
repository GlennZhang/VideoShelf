"""Full-course processing, independent of the library's sampled analysis."""

import hashlib
from contextlib import nullcontext
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError, field_validator
from json_repair import loads as repair_json_loads

from library import media
from library.config import LibraryConfig
from library.hashing import file_sha256
from library.omlx_client import OmlxClient, OmlxError, extract_json
from library.scanner import discover_videos

VERSION = "5"
CHUNK_SECONDS = 30
TOPICS = (
    "剧本",
    "角色与人物一致性",
    "分镜与镜头",
    "静帧生成",
    "视频生成",
    "配音",
    "剪辑",
    "其他",
)


class Segment(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    text: str


class Prompt(BaseModel):
    text: str = Field(min_length=1)
    source: Literal["口述", "画面原文", "整理模板"]
    purpose: str = ""


class Step(BaseModel):
    title: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    segment: int = Field(ge=0)
    evidence: str = Field(min_length=2)
    end_segment: int | None = None
    topic: Literal[
        "剧本",
        "角色与人物一致性",
        "分镜与镜头",
        "静帧生成",
        "视频生成",
        "配音",
        "剪辑",
        "其他",
    ] = "其他"
    tips: list[str] = Field(default_factory=list)
    prompts: list[Prompt] = Field(default_factory=list)

    @field_validator("topic", mode="before")
    @classmethod
    def normalize_topic(cls, value: object) -> object:
        # Model-generated labels are useful text, but not new export categories.
        return "其他" if isinstance(value, str) and value not in TOPICS else value


class Notes(BaseModel):
    steps: list[Step]
    unverified: list[Step] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


def parse_course_notes(reply: str) -> Notes:
    """Repair complete but malformed JSON; never invent a truncated ending."""
    try:
        value = extract_json(reply)
    except OmlxError:
        candidate = reply.strip().removesuffix("```").strip()
        start = candidate.find("{")
        if start < 0 or not candidate.endswith("}"):
            raise
        value = repair_json_loads(candidate[start:], skip_json_loads=True)
    return Notes.model_validate(value)


class Visual(BaseModel):
    selected: int | None = Field(default=None, ge=0)
    caption: str
    screen_prompts: list[str] = Field(default_factory=list)
    uncertainty: str = ""
    prompt_context: str = ""


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_subtitles(path: Path, duration: float) -> list[Segment]:
    """Read standard SRT/WebVTT cues; reject malformed/empty subtitle files."""
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    pattern = r"(?m)^(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)"
    segments = []
    for match in re.finditer(pattern, text, re.S):
        h, m, s, ms, eh, em, es, ems, body = match.groups()
        start = int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
        end = int(eh or 0) * 3600 + int(em) * 60 + int(es) + int(ems) / 1000
        body = re.sub(r"<[^>]*>", "", body).strip()
        if not (0 <= start < end <= duration + 1) or not body:
            raise ValueError(f"字幕时间或文本无效: {path.name}")
        segments.append(Segment(start=start, end=min(end, duration), text=body))
    if not segments:
        raise ValueError(f"未找到有效字幕: {path.name}")
    return sorted(segments, key=lambda s: s.start)


def locate_evidence(
    segments: list[Segment], indexes: list[int], evidence: str, preferred: int
) -> tuple[int, int, str] | None:
    """Ignore spacing and ASR sentence stops; return the actual source quote and window."""
    joined = "".join(segments[j].text for j in indexes)
    characters = []
    locations = []
    offsets = {}
    raw_offset = 0
    for j in indexes:
        start = len(characters)
        for k, char in enumerate(segments[j].text):
            if not char.isspace() and char != "。":
                characters.append(char)
                locations.append((j, raw_offset + k))
        offsets[j] = (start, len(characters))
        raw_offset += len(segments[j].text)
    quote = "".join(evidence.split()).replace("。", "")
    if not quote:
        return None
    normalized = "".join(characters)
    start, end = offsets[preferred]
    position = normalized.find(quote, start, end)
    if position < 0:
        position = normalized.find(quote)
    if position < 0:
        return None
    first, last = locations[position], locations[position + len(quote) - 1]
    return first[0], last[0], joined[first[1] : last[1] + 1]


class CoursePipeline:
    def __init__(self, cfg: LibraryConfig, client: OmlxClient, model_factory=None):
        self.cfg = cfg
        self.client = client
        self.model_factory = model_factory
        self.root = cfg.data_dir / "courses"

    def course_dir(self, course_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", course_id):
            raise ValueError("课程 ID 无效")
        return self.root / course_id

    def load(self, course_id: str) -> dict:
        return read_json(self.course_dir(course_id) / "course.json")

    def list_courses(self) -> list[dict]:
        return [read_json(p) for p in sorted(self.root.glob("*/course.json"))]

    def import_course(self, folder: Path) -> dict:
        folder = folder.expanduser().resolve()
        if not folder.is_dir():
            raise ValueError("课程目录不存在")
        files = discover_videos(folder, self.cfg.scan_excludes)
        files.sort(
            key=lambda p: [
                (0, int(v)) if v.isdigit() else (1, v.casefold())
                for v in re.split(r"(\d+)", str(p.relative_to(folder)))
            ]
        )
        if not files:
            raise ValueError("目录中没有支持的视频（MP4/MKV/MOV）")
        course = {
            "id": uuid4().hex,
            "title": folder.name,
            "source": str(folder),
            "status": "imported",
            "lessons": [
                {
                    "id": f"{i:03}",
                    "title": p.stem,
                    "path": str(p),
                    "status": "pending",
                    "error": "",
                }
                for i, p in enumerate(files, 1)
            ],
        }
        write_json(self.course_dir(course["id"]) / "course.json", course)
        return course

    def run(
        self,
        course_id: str,
        progress: Callable[[str, float], None] = lambda stage, ratio: None,
        retry_only: bool = False,
        regenerate: bool = False,
    ) -> None:
        course = self.load(course_id)
        folder = self.course_dir(course_id)
        generation_id = uuid4().hex if regenerate else None
        course["status"] = "running"
        write_json(folder / "course.json", course)
        for index, lesson in enumerate(course["lessons"]):
            if retry_only and not regenerate and lesson["status"] == "done":
                progress("课程：保留已完成教程", (index + 1) / len(course["lessons"]))
                continue
            if regenerate:
                lesson["generation_id"] = generation_id
            lesson.update(status="running", error="")
            write_json(folder / "course.json", course)
            try:
                runtime = (
                    self.model_factory()
                    if self.model_factory
                    else nullcontext((self.cfg, self.client))
                )
                with runtime as (cfg, client):
                    self.cfg, self.client = cfg, client
                    self.process_lesson(
                        folder,
                        lesson,
                        lambda stage, fraction: progress(
                            stage, (index + fraction) / len(course["lessons"])
                        ),
                        generation_id=lesson.get("generation_id"),
                    )
                lesson["status"] = "done"
            except (
                Exception
            ) as exc:  # Task boundary: retain failures and continue other lessons.
                lesson.update(status="failed", error=str(exc)[:1000])
            write_json(folder / "course.json", course)
        course["status"] = (
            "failed"
            if any(l["status"] == "failed" for l in course["lessons"])
            else "done"
        )
        write_json(folder / "course.json", course)
        self.export(course_id)
        if course["status"] == "failed":
            raise ValueError("部分课程处理失败，请查看逐课错误并重试")
        progress("完成", 1.0)

    def process_lesson(
        self,
        folder: Path,
        lesson: dict,
        progress: Callable[[str, float], None],
        generation_id: str | None = None,
    ) -> None:
        video = Path(lesson["path"])
        progress("课程：读取媒体", 0)
        info = media.probe(video)
        if info.duration_s <= 0:
            raise ValueError("无法读取视频时长")
        subtitle = next(
            (p for ext in (".srt", ".vtt") if (p := video.with_suffix(ext)).exists()),
            None,
        )
        identity = {
            "version": VERSION,
            "sha": file_sha256(video),
            "subtitle": file_sha256(subtitle) if subtitle else None,
            "asr": self.cfg.omlx.asr_model,
            "vision": self.cfg.omlx.vision_model,
            "endpoint": self.cfg.omlx.base_url,
            "chunk": CHUNK_SECONDS,
        }
        for purpose in ("text", "vision"):
            if purpose + "_identity" in self.cfg.analysis:
                identity[purpose + "_identity"] = self.cfg.analysis[
                    purpose + "_identity"
                ]
        if generation_id is not None:
            identity["generation"] = generation_id
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[
            :20
        ]
        asr_identity = {
            k: v
            for k, v in identity.items()
            if k
            not in (
                "version",
                "vision",
                "generation",
                "text_identity",
                "vision_identity",
            )
        }
        asr_identity["transcription_version"] = 1
        asr_key = hashlib.sha256(
            json.dumps(asr_identity, sort_keys=True).encode()
        ).hexdigest()[:20]
        asr_cache = folder / "cache" / lesson["id"] / ("asr-" + asr_key)
        asr_cache.mkdir(parents=True, exist_ok=True)
        cache = folder / "cache" / lesson["id"] / key
        cache.mkdir(parents=True, exist_ok=True)
        segments: list[Segment] = []
        if subtitle:
            segments = read_subtitles(subtitle, info.duration_s)
            precision = "字幕时间；字幕准确性待人工抽查"
        else:
            if not info.audio_codec:
                raise ValueError("没有音轨或同名字幕，无法生成完整教程")
            count = int((info.duration_s - 0.001) // CHUNK_SECONDS) + 1
            for i in range(count):
                progress("课程：全量转写", 0.05 + 0.4 * i / count)
                start, end = i * CHUNK_SECONDS, min(
                    (i + 1) * CHUNK_SECONDS, info.duration_s
                )
                target = asr_cache / f"asr-{i:05}.json"
                if target.exists():
                    segment = Segment.model_validate(read_json(target))
                else:
                    wav = cache / "chunk.wav"
                    media.extract_audio(video, wav, start, end - start)
                    text = self.client.transcribe(
                        wav, self.cfg.omlx.asr_model, language="zh"
                    ).strip()
                    segment = Segment(start=start, end=end, text=text)
                    write_json(target, segment.model_dump())
                    wav.unlink(missing_ok=True)
                segments.append(segment)
            precision = f"音频块级近似定位（最长 {CHUNK_SECONDS} 秒），非句级时间戳"
        if not any(s.text.strip() for s in segments):
            raise ValueError("全文转写为空，未生成教程")
        write_json(
            cache / "transcript.json",
            {"precision": precision, "segments": [s.model_dump() for s in segments]},
        )
        # Keep context bounded by text length, including subtitle-heavy lessons.
        batches: list[list[int]] = []
        for idx, segment in enumerate(segments):
            if (
                not batches
                or sum(len(segments[j].text) for j in batches[-1]) + len(segment.text)
                > 5000
                or len(batches[-1]) >= 20
            ):
                batches.append([])
            batches[-1].append(idx)
        steps = []
        unverified = []
        batch_reviews = []
        for b, indexes in enumerate(batches):
            progress("课程：整理与视觉核对", 0.45 + 0.5 * b / len(batches))
            target = cache / f"notes-{b:05}.json"
            if target.exists():
                notes = Notes.model_validate(read_json(target))
            else:
                source = "\n".join(f"[{j}] {segments[j].text}" for j in indexes)
                messages = [
                    {
                        "role": "system",
                        "content": "你整理AI短剧课程。讲师讲解剧情结构、主角与反派设定、写作技巧、避坑指南都属于教学知识，必须提取，即使没有软件操作。提取有来源的知识点和操作方法，保留参数、技巧。detail和tips只能写原文明确讲到的内容，严禁自行补充原文未出现的数字、阶段、原因或指南。只提到标题而没有展开时标注待核对，不能自行补全。只有角色之间直接发生的对白、广告、寒暄才不属于教学，不要把它们改成行动指令；整段完全没有教学知识时才允许 steps 为空。不要因为课程讨论剧情或人物就排除教学。只输出JSON。",
                    },
                    {
                        "role": "user",
                        "content": "课程："
                        + lesson["title"]
                        + '\n输出 {"steps":[{"title":"步骤标题","detail":"详细操作、参数与预期结果","segment":来源编号,"evidence":"复制该来源片段中连续4到12个原文字符，不改错字、不拼接、不改数字写法","topic":"'
                        + "其他"
                        + '","tips":["技巧或待核对项"],"prompts":[{"text":"提示词内容","source":"口述","purpose":"用途"}]}]}。不得声称口述是画面原文。非操作的教学内容保留知识点；角色直接对白不提取，讲师对角色设定与剧情结构的指导必须提取。topic只允许：剧本、角色与人物一致性、分镜与镜头、静帧生成、视频生成、配音、剪辑、其他。提示词source只允许口述或整理模板，必须取单个值，没有提示词就返回空数组。来源：\n'
                        + source,
                    },
                ]
                verified_steps = {}
                pending_steps = {}
                for attempt in range(3):
                    previous_reply = cache / f"model-response-{b:05}.json"
                    reply = (
                        read_json(previous_reply)["reply"]
                        if attempt == 0 and previous_reply.exists()
                        else self.client.chat(self.cfg.omlx.vision_model, messages)
                    )
                    write_json(previous_reply, {"reply": reply})
                    try:
                        notes = parse_course_notes(reply)
                    except (ValidationError, OmlxError) as exc:
                        if attempt == 2:
                            notes = Notes(
                                steps=[],
                                issues=[
                                    "模型返回的内容无法解析，该时间段暂未整理。原始文字稿已保留，可切换模型后重新整理。"
                                ],
                            )
                            break
                        messages.append(
                            {
                                "role": "user",
                                "content": "上一结果不符合JSON结构："
                                + str(exc)[:1500]
                                + "。请重新输出完整JSON，字段必须符合要求，source只能取口述或整理模板之一。",
                            }
                        )
                        continue
                    if any(
                        s.segment not in indexes
                        or any(p.source == "画面原文" for p in s.prompts)
                        for s in notes.steps
                    ):
                        raise ValueError(
                            "模型返回了不存在的来源或未经视觉核对的画面原文"
                        )
                    invalid = []
                    for step in notes.steps:
                        located = locate_evidence(
                            segments, indexes, step.evidence, step.segment
                        )
                        if located is None:
                            invalid.append(step.evidence)
                            pending_steps[(step.title, step.evidence)] = step
                        else:
                            step.segment, step.end_segment, step.evidence = located
                            verified_steps[(step.title, step.evidence)] = step
                    if not invalid or attempt == 2:
                        verified_titles = {
                            step.title for step in verified_steps.values()
                        }
                        notes.steps = list(verified_steps.values())
                        notes.unverified = [
                            step
                            for step in pending_steps.values()
                            if step.title not in verified_titles
                        ]
                        break
                    messages.append(
                        {
                            "role": "user",
                            "content": "以下引用不是逐字原文："
                            + json.dumps(invalid, ensure_ascii=False)
                            + "。请重新输出完整结果，每个 evidence 只复制来源中连续4到12个字符，保留原文错别字、中文数字和标点，不概括不改写。",
                        }
                    )
                write_json(target, notes.model_dump())
            batch_reviews.extend(
                {
                    "title": f"第{b + 1}段内容",
                    "message": issue,
                    "start": segments[indexes[0]].start,
                    "end": segments[indexes[-1]].end,
                }
                for issue in notes.issues
            )
            unverified.extend(
                {
                    **step.model_dump(),
                    "start": segments[step.segment].start,
                    "end": segments[step.segment].end,
                }
                for step in notes.unverified
            )
            for n, step in enumerate(notes.steps):
                progress(
                    f"课程：核对截图 {n + 1}/{len(notes.steps)}（第{b + 1}/{len(batches)}段）",
                    0.45 + 0.5 * (b + n / len(notes.steps)) / len(batches),
                )
                segment = Segment(
                    start=segments[step.segment].start,
                    end=segments[
                        (
                            step.end_segment
                            if step.end_segment is not None
                            else step.segment
                        )
                    ].end,
                    text=step.evidence,
                )
                visual_path = cache / f"visual-{b:05}-{n:03}.json"
                images = [cache / f"frame-{b:05}-{n:03}-{k}.jpg" for k in range(3)]
                times = [
                    segment.start + (segment.end - segment.start) * ratio
                    for ratio in (0.15, 0.5, 0.85)
                ]
                if visual_path.exists() and all(p.exists() for p in images):
                    visual = Visual.model_validate(read_json(visual_path))
                else:
                    for image, timestamp in zip(images, times):
                        subprocess.run(
                            [
                                "ffmpeg",
                                "-y",
                                "-loglevel",
                                "error",
                                "-ss",
                                str(timestamp),
                                "-i",
                                str(video),
                                "-frames:v",
                                "1",
                                "-q:v",
                                "2",
                                str(image),
                            ],
                            check=True,
                            capture_output=True,
                            timeout=120,
                        )
                    reply = self.client.chat_vision(
                        self.cfg.omlx.vision_model,
                        "你核对课程原始截图。screen_prompts 只允许来自 AI 工具提示词输入框、明确标注的提示词文档中的完整指令。字幕、角色对白、水印、片名、账号、按钮名称绝对不是提示词。没有明确输入框或提示词文档时必须返回空数组。prompt_context 必须说明提示词所在工具与输入区域；不清晰或截断不得补全。只输出 JSON。",
                        f'操作：{step.title}\n{step.detail}\n三张候选图编号0/1/2，选最匹配且清晰的一张；无匹配用null。只提取所选截图中完整可读的AI输入提示词，其他候选截图文字不得混入。截断或模糊则不提取并说明。输出 {{"selected":0,"caption":"图注","screen_prompts":["画面提示词原文"],"uncertainty":"不确定或待核对内容","prompt_context":"可确认的AI提示词输入区域，无则空字符串"}}',
                        images,
                    )
                    visual = Visual.model_validate(extract_json(reply))
                    if visual.selected is not None and visual.selected >= len(images):
                        raise ValueError("视觉模型返回了无效截图编号")
                if visual.uncertainty.strip().lower() in ("无", "没有", "none"):
                    visual.uncertainty = ""
                context = visual.prompt_context.strip()
                if visual.screen_prompts and (
                    visual.selected is None
                    or not re.search(
                        r"输入框|提示词文档|prompt.*(?:input|document)", context, re.I
                    )
                    or re.search(r"无|未|没有|不是|非", context)
                ):
                    visual.screen_prompts = []
                    visual.uncertainty = (
                        visual.uncertainty
                        + "；缺少明确的提示词输入区域依据，未收录画面文字"
                    ).strip("；")
                write_json(visual_path, visual.model_dump())
                data = step.model_dump()
                data.update(
                    start=segment.start,
                    end=segment.end,
                    visual=visual.model_dump(),
                    image=None,
                )
                if visual.selected is not None:
                    data["image"] = str(images[visual.selected].relative_to(folder))
                    data["image_time"] = times[visual.selected]
                steps.append(data)
        lesson.update(cache=str(cache.relative_to(folder)), precision=precision)
        write_json(
            cache / "lesson.json",
            {
                "title": lesson["title"],
                "steps": steps,
                "precision": precision,
                "unverified": unverified,
                "batch_reviews": batch_reviews,
            },
        )
        progress("课程：生成教程", 1)

    def export(self, course_id: str) -> Path:
        """Deterministic Markdown: model output never supplies filenames or links."""
        course = self.load(course_id)
        folder = self.course_dir(course_id)
        out = Path(tempfile.mkdtemp(prefix=".export-", dir=folder))
        for name in (
            "lessons",
            "transcripts",
            "assets",
            "topics",
            "prompts",
            "sources",
        ):
            (out / name).mkdir(parents=True, exist_ok=True)
        index = [
            f"# {course['title']}",
            "",
            f"处理状态：{course['status']}",
            "",
            "时间定位精度见各课；AI 识别结果建议对照原视频核对。",
            "",
        ]
        topics: dict[str, list[str]] = {t: [] for t in TOPICS}
        prompts = [
            "# 提示词库",
            "",
            "画面原文为视觉识别结果；口述与整理模板分别标注。",
            "",
        ]
        for lesson in course["lessons"]:
            lid = lesson["id"]
            if lesson["status"] != "done":
                index.append(
                    f"- {lesson['title']}：{lesson['status']} {lesson['error']}"
                )
                continue
            index.append(f"- [{lesson['title']}](lessons/{lid}.md)")
            cache = folder / lesson["cache"]
            notes = read_json(cache / "lesson.json")
            transcript = read_json(cache / "transcript.json")
            lines = [
                f"# {lesson['title']}",
                "",
            ]
            if not notes["steps"]:
                lines.extend(
                    [
                        "本课未提取到可确认的教学步骤，可能为成片展示或非教学片段；请查看原始文字稿并对照视频。",
                        "",
                    ]
                )
            raw = [
                f"# {lesson['title']} · 原始文字稿",
                "",
                f"定位精度：{lesson['precision']}",
                "",
            ]
            for seg in transcript["segments"]:
                raw.extend(
                    [
                        f"[{seg['start']:.1f}–{seg['end']:.1f} 秒]",
                        "",
                        seg["text"] or "（该块未识别到文本）",
                        "",
                    ]
                )
            (out / "transcripts" / f"{lid}.md").write_text(
                "\n".join(raw), encoding="utf-8"
            )
            for number, step in enumerate(notes["steps"], 1):
                lines.extend(
                    [
                        f"## {number}. {step['title']}",
                        "",
                        step["detail"],
                        "",
                    ]
                )
                topics[step["topic"]].append(
                    f"- [{lesson['title']} · {step['title']}](../lessons/{lid}.md)（{step['start']:.1f}–{step['end']:.1f} 秒）\n\n  {step['detail']}"
                )
                lines.extend(
                    f"- {tip}" for tip in step["tips"] if not tip.startswith("待核对")
                )
                lines.append("")
                visual = step["visual"]
                if step["image"]:
                    name = f"{lid}-{number:03}-{file_sha256(folder / step['image'])[:12]}.jpg"
                    shutil.copyfile(folder / step["image"], out / "assets" / name)
                    lines.extend(
                        [
                            f"![步骤截图](../assets/{name})",
                            "",
                            visual["caption"],
                            "",
                        ]
                    )
                combined = step["prompts"] + [
                    {"text": text, "source": "画面原文", "purpose": step["title"]}
                    for text in visual["screen_prompts"]
                ]
                for prompt in combined:
                    # A fence longer than any fence inside a prompt preserves exact prompt text.
                    fence = "`" * max(
                        3,
                        max(
                            (len(m) + 1 for m in re.findall(r"`+", prompt["text"])),
                            default=3,
                        ),
                    )
                    block = [
                        f"### 提示词（{prompt['source']}）",
                        "",
                        prompt["purpose"],
                        "",
                        fence + "text",
                        prompt["text"],
                        fence,
                        "",
                    ]
                    lines.extend(block)
                    prompts.extend(
                        [
                            f"[来源：{lesson['title']}，{step['start']:.1f} 秒](../lessons/{lid}.md)",
                            "",
                        ]
                        + block
                    )
            sources = [
                f"# {lesson['title']} · 来源记录",
                "",
                f"原视频：{Path(lesson['path']).name}",
                "",
                f"定位精度：{lesson['precision']}",
                "",
                f"[返回教程](../lessons/{lid}.md) · [完整原始文字稿](../transcripts/{lid}.md)",
                "",
            ]
            for step in notes["steps"]:
                sources.extend(
                    [
                        f"## {step['title']}",
                        "",
                        f"时间：{step['start']:.1f}–{step['end']:.1f} 秒",
                        "",
                        step["evidence"],
                        "",
                    ]
                )
            (out / "sources" / f"{lid}.md").write_text(
                "\n".join(sources), encoding="utf-8"
            )
            lines.extend(
                [
                    "## 参考来源",
                    "",
                    f"[视频来源与原文依据](../sources/{lid}.md) · [完整文字稿](../transcripts/{lid}.md)",
                    "",
                ]
            )
            (out / "lessons" / f"{lid}.md").write_text(
                "\n".join(lines), encoding="utf-8"
            )
        index.extend(["", "## 专题", ""])
        for i, (topic, entries) in enumerate(topics.items(), 1):
            if entries:
                name = f"{i:02}.md"
                (out / "topics" / name).write_text(
                    "\n\n".join([f"# {topic}"] + entries), encoding="utf-8"
                )
                index.append(f"- [{topic}](topics/{name})")
        index.extend(["", "[提示词库](prompts/README.md)", ""])
        (out / "README.md").write_text("\n".join(index), encoding="utf-8")
        (out / "prompts" / "README.md").write_text("\n".join(prompts), encoding="utf-8")
        from library.course_documents import CourseDocuments

        documents = CourseDocuments(folder)
        documents.sync_generated(out)
        # Export the working Markdown, including human edits, rather than the AI draft.
        shutil.rmtree(out)
        out.mkdir()
        documents.export_to(out)
        destination = folder / "export"
        if destination.exists():
            shutil.rmtree(destination)
        out.replace(destination)
        return destination

    def archive(self, course_id: str) -> Path:
        folder = self.course_dir(course_id)
        if self.load(course_id)["status"] == "running":
            raise ValueError("课程仍在处理中，请完成后导出")
        out = self.export(course_id)
        return Path(shutil.make_archive(str(folder / "tutorials"), "zip", out))
