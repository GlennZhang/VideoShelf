"""Resumable short-drama shot analysis and illustrated reconstruction notes."""

from contextlib import nullcontext
import hashlib
import json
import math
from pathlib import Path
import shutil
from typing import Callable
from uuid import uuid4

from json_repair import loads as repair_json
from pydantic import BaseModel, Field, ValidationError

from library import media
from library.agy_client import AgyClient, DramaSettings
from library.config import LibraryConfig, SUPPORTED_EXTENSIONS
from library.course_documents import CourseDocuments, write_text
from library.course_pipeline import Segment, read_subtitles
from library.drama_media import extract_shots
from library.hashing import file_sha256
from library.omlx_client import OmlxClient, OmlxError, extract_json

VERSION = 1


class ShotAnalysis(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    observed_image_count: int = Field(ge=1, le=12)
    scene: str = Field(min_length=1, max_length=2000)
    characters: list[str] = Field(max_length=30)
    composition: str = Field(min_length=1, max_length=2000)
    lighting: str = Field(min_length=1, max_length=2000)
    action: str = Field(min_length=1, max_length=3000)
    camera_motion: str = Field(min_length=1, max_length=2000)
    image_prompt: str = Field(min_length=1, max_length=5000)
    video_prompt: str = Field(min_length=1, max_length=5000)
    editing: str = Field(default="", max_length=2000)
    uncertainties: list[str] = Field(default_factory=list, max_length=20)


class EntityCard(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=1, max_length=3000)
    prompt: str = Field(min_length=1, max_length=5000)
    shot_ids: list[str] = Field(min_length=1)


class DramaSummary(BaseModel):
    synopsis: str = Field(min_length=1, max_length=4000)
    visual_style: str = Field(min_length=1, max_length=3000)
    characters: list[EntityCard] = Field(max_length=100)
    scenes: list[EntityCard] = Field(max_length=100)
    rhythm: str = Field(min_length=1, max_length=3000)
    workflow: list[str] = Field(min_length=1, max_length=30)


def dump(path: Path, value: object) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class DramaPipeline:
    def __init__(self, cfg: LibraryConfig, client: OmlxClient, model_factory=None):
        self.cfg = cfg
        self.client = client
        self.model_factory = model_factory
        self.root = cfg.data_dir / "dramas"

    def folder(self, drama_id: str) -> Path:
        if len(drama_id) != 32 or any(c not in "0123456789abcdef" for c in drama_id):
            raise FileNotFoundError("短剧不存在")
        return self.root / drama_id

    def read(self, drama_id: str) -> dict:
        return load(self.folder(drama_id) / "drama.json")

    def list_items(self) -> list[dict]:
        return sorted(
            [load(p) for p in self.root.glob("*/drama.json")],
            key=lambda item: item["created"],
            reverse=True,
        )

    def create(
        self,
        video: Path,
        group_title: str = "",
        group_id: str = "",
    ) -> dict:
        from datetime import datetime, timezone

        video = video.expanduser().resolve()
        if not video.is_file() or video.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError("请选择本机 MP4、MKV 或 MOV 视频文件")
        try:
            with video.open("rb") as file:
                file.read(1)
        except PermissionError as exc:
            raise ValueError(
                "系统拒绝读取视频，请将文件复制到项目的 artifacts/drama-input 目录，或为启动服务的终端授予文件访问权限"
            ) from exc
        value = {
            "id": uuid4().hex,
            "title": video.stem,
            "group_title": group_title,
            "group_id": group_id,
            "path": str(video),
            "source_sha256": file_sha256(video),
            "created": datetime.now(timezone.utc).isoformat(),
            "status": "queued",
            "stage": "等待处理",
            "progress": 0,
            "error": "",
            "shots": [],
            "documents_ready": False,
            "has_report": False,
        }
        dump(self.folder(value["id"]) / "drama.json", value)
        return value

    def run(
        self,
        drama_id: str,
        progress: Callable[[str, float], None] = lambda *_: None,
        regenerate: bool = False,
    ) -> None:
        item = self.read(drama_id)
        folder = self.folder(drama_id)
        # Each queued video gets a settings snapshot; changing UI settings affects the next job.
        settings = DramaSettings.load(self.cfg.data_dir)
        runtime = (
            self.model_factory()
            if self.model_factory
            else nullcontext((self.cfg, self.client))
        )
        if regenerate:
            item["generation"] = uuid4().hex
            item["shots"] = []
        item.update(status="running", error="")

        def report(stage: str, fraction: float) -> None:
            item.update(stage=stage, progress=fraction)
            dump(folder / "drama.json", item)
            progress(stage, fraction)

        if settings.backend == "rea_video":
            try:
                self.run_rea_video(item, folder, Path(item["path"]), settings, report)
                return
            except Exception as exc:
                item.update(status="failed", error=str(exc)[:1000])
                dump(folder / "drama.json", item)
                raise
        elif settings.backend == "rea_toon":
            try:
                self.run_rea_toon(item, folder, Path(item["path"]), settings, report)
                return
            except Exception as exc:
                item.update(status="failed", error=str(exc)[:1000])
                dump(folder / "drama.json", item)
                raise

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
                frames = cache / "frames"
                report("切镜与抽帧", 0.03)
                shot_file = cache / "shots.json"
                if shot_file.exists():
                    shots = load(shot_file)
                    valid = all(
                        (frames / f["file"]).is_file()
                        for s in shots
                        for f in s["frames"]
                    )
                else:
                    valid = False
                if not valid:
                    shots = extract_shots(video, frames, info.duration_s)
                    dump(shot_file, shots)
                item["shots"] = [
                    dict(
                        s,
                        frames=[
                            dict(f, file=str((frames / f["file"]).relative_to(folder)))
                            for f in s["frames"]
                        ],
                    )
                    for s in shots
                ]
                transcript = self.transcribe(video, cache, info, cfg, client, report)
                dump(folder / "transcript.json", transcript)
                identities = {
                    k: cfg.analysis.get(
                        k + "_identity",
                        {"model": cfg.omlx.vision_model, "url": cfg.omlx.base_url},
                    )
                    for k in ("text", "vision")
                }
                identity = {
                    "version": VERSION,
                    "backend": settings.backend,
                    "models": (
                        identities
                        if settings.backend == "compatible"
                        else settings.model
                    ),
                    "transcript": transcript,
                    "generation": item.get("generation"),
                }
                key = hashlib.sha256(
                    json.dumps(identity, sort_keys=True).encode()
                ).hexdigest()[:20]
                analysis_dir = cache / ("analysis-" + key)
                analysis_dir.mkdir(parents=True, exist_ok=True)
                item["analysis_cache"] = str(analysis_dir.relative_to(folder))
                item["backend"] = settings.backend
                analyses = []
                for i, shot in enumerate(shots):
                    report(
                        f"分析镜头 {i + 1}/{len(shots)}", 0.25 + 0.6 * i / len(shots)
                    )
                    segments = [
                        s
                        for s in transcript["segments"]
                        if s["start"] < shot["end"] and s["end"] > shot["start"]
                    ]
                    prompt = (
                        "按时间顺序分析同一个镜头的多帧。中文输出。只把图片中能确认的内容写入观察字段。"
                        "人物没有已知姓名时按外观称呼，不凭台词判断说话人。区分人物动作、镜头运动与剪辑；"
                        "静帧无法确认的运镜要明确写无法确认。不要猜作者使用的模型、种子或软件。"
                        "image_prompt 是可复制的静帧复现建议，video_prompt 是基于参考图的视频复现建议，"
                        "应包含主体动作顺序、环境变化、运镜、时长和一致性约束，不得声称是作者原始提示词。"
                        "不依据图片编造音效或音乐。台词是音频块/字幕的相邻上下文，不保证全属于本镜头。"
                        f"\n镜头：{json.dumps(shot, ensure_ascii=False)}"
                        f"\n台词与精度：{json.dumps({'precision': transcript['precision'], 'segments': segments}, ensure_ascii=False)}"
                        "\n严格输出以下 JSON schema 对应的对象：\n"
                        + json.dumps(
                            ShotAnalysis.model_json_schema(), ensure_ascii=False
                        )
                    )
                    images = [frames / f["file"] for f in shot["frames"]]
                    result = self.analyze(
                        analysis_dir / f"shot-{shot['id']}.json",
                        prompt,
                        images,
                        ShotAnalysis,
                        settings,
                        cfg,
                        client,
                    )
                    if result.observed_image_count != len(images):
                        raise ValueError(
                            f"镜头 {shot['id']} 模型未确认看完全部图片，请重试或更换视觉模型"
                        )
                    analyses.append(result.model_dump())
                    item["shots"][i]["analysis"] = result.model_dump()
                    dump(folder / "drama.json", item)
                report("汇总角色、场景与复现流程", 0.88)
                observations = [
                    dict(
                        id=s["id"],
                        start=s["start"],
                        end=s["end"],
                        take=s["take"],
                        subdivision=s["subdivision"],
                        part=s["part"],
                        **{
                            k: a[k]
                            for k in (
                                "title",
                                "scene",
                                "characters",
                                "composition",
                                "lighting",
                                "action",
                                "camera_motion",
                            )
                        },
                    )
                    for s, a in zip(shots, analyses)
                ]
                prompt = (
                    "将短剧逐镜观察汇总为中文学习文档。结合图片核对外观，把同一角色和同一场景跨镜头归并。"
                    "角色姓名未知就用外观名称，不凭剧情发明角色。每个卡片 shot_ids 必须来自输入镜头 ID。"
                    "角色 prompt 用于制作一致性参考图，场景 prompt 用于制作场景参考图。"
                    "workflow 给出可执行的复现顺序。rhythm 分析镜头时长、切换和叙事节奏，不猜未提供的音乐。"
                    "take 是原始切镜编号；同一个 take 的多个片段属于同一连续镜头，"
                    "subdivision=true 是程序为分析长镜头而拆分，绝不能说成剪辑切点，也不要建议在此切镜。"
                    f"本片检测到 {max(s['take'] for s in shots)} 个原始镜头、{len(shots)} 个分析片段。"
                    "只能推测复现方法，不得宣称恢复原始提示词或识别出确定的生成模型。"
                    f"\n逐镜观察：{json.dumps(observations, ensure_ascii=False)}"
                    "\n人物归并不确定时分开描述。"
                    "\n严格输出以下 JSON schema 对应的对象：\n"
                    + json.dumps(DramaSummary.model_json_schema(), ensure_ascii=False)
                )
                selected = list(
                    dict.fromkeys(
                        round(i * (len(shots) - 1) / min(11, max(1, len(shots) - 1)))
                        for i in range(min(12, len(shots)))
                    )
                )
                prompt += "\n参考图顺序对应镜头 ID：" + json.dumps(
                    [shots[i]["id"] for i in selected]
                )
                summary = self.analyze(
                    analysis_dir / "summary-v2.json",
                    prompt,
                    [
                        frames
                        / shots[i]["frames"][len(shots[i]["frames"]) // 2]["file"]
                        for i in selected
                    ],
                    DramaSummary,
                    settings,
                    cfg,
                    client,
                    allowed_shot_ids={s["id"] for s in shots},
                )
                valid_ids = {s["id"] for s in shots}
                if any(
                    set(card.shot_ids) - valid_ids
                    for card in summary.characters + summary.scenes
                ):
                    raise ValueError("角色/场景汇总引用了不存在的镜头，请重新分析")
                dump(folder / "summary.json", summary.model_dump())
                report("生成 Markdown 教程", 0.96)
                self.export(item, analyses, summary, transcript)
                item.update(status="done", error="", documents_ready=True)
                report("完成", 1)
        except Exception as exc:
            item.update(status="failed", error=str(exc)[:1000])
            dump(folder / "drama.json", item)
            raise

    def transcribe(self, video, cache, info, cfg, client, report) -> dict:
        subtitle = next(
            (
                p
                for suffix in (".srt", ".vtt")
                if (p := video.with_suffix(suffix)).exists()
            ),
            None,
        )
        if subtitle:
            segments = read_subtitles(subtitle, info.duration_s)
            if not segments:
                raise ValueError(
                    "字幕文件为空或格式无法识别，请修正字幕或移走同名字幕后重试"
                )
            return {
                "precision": "字幕提供的时间戳，台词准确性需对照视频",
                "segments": [s.model_dump() for s in segments],
            }
        if not info.audio_codec:
            return {"precision": "视频没有音轨，仅进行画面分析", "segments": []}
        identity = {
            "model": cfg.omlx.asr_model,
            "endpoint": cfg.omlx.base_url,
            "chunk": 15,
            "version": 1,
        }
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[
            :20
        ]
        asr_dir = cache / ("asr-" + key)
        segments = []
        count = math.ceil(info.duration_s / 15)
        for i in range(count):
            report(f"音频转写 {i + 1}/{count}", 0.15 + 0.1 * i / count)
            target = asr_dir / f"{i:05}.json"
            if target.exists():
                segment = Segment.model_validate(load(target))
            else:
                wav = asr_dir / f"{i:05}.wav"
                wav.parent.mkdir(parents=True, exist_ok=True)
                start, end = i * 15, min((i + 1) * 15, info.duration_s)
                media.extract_audio(video, wav, start, end - start)
                text = client.transcribe(wav, cfg.omlx.asr_model, language="zh").strip()
                segment = Segment(start=start, end=end, text=text)
                dump(target, segment.model_dump())
                wav.unlink(missing_ok=True)
            segments.append(segment.model_dump())
        return {
            "precision": "音频块级近似时间（最长 15 秒），非句级或说话人时间戳；未进行音乐/音效识别",
            "segments": segments,
        }

    def analyze(
        self,
        target,
        prompt,
        images,
        schema,
        settings,
        cfg,
        client,
        allowed_shot_ids=None,
    ):
        if target.exists():
            return schema.model_validate(load(target))
        error = ""
        for attempt in range(3):
            request = prompt + (
                "\n上次输出未通过校验，请修正：" + error if error else ""
            )
            if settings.backend == "agy":
                reply = AgyClient(settings).analyze(request, images, target.parent)
            else:
                reply = client.chat_vision(
                    cfg.omlx.vision_model,
                    "你是短剧分镜分析助手，只输出 JSON。",
                    request,
                    images,
                )
            write_text(
                target.with_name(f"{target.stem}-raw-{uuid4().hex[:8]}.txt"), reply
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
                parsed = schema.model_validate(value)
                if isinstance(parsed, DramaSummary) and any(
                    set(card.shot_ids) - allowed_shot_ids
                    for card in parsed.characters + parsed.scenes
                ):
                    raise ValueError("shot_ids 只能包含输入中存在的镜头 ID")
                if isinstance(
                    parsed, ShotAnalysis
                ) and parsed.observed_image_count != len(images):
                    raise ValueError(
                        f"observed_image_count 必须为实际查看的 {len(images)} 张图片"
                    )
            except (OmlxError, ValidationError, ValueError) as exc:
                error = str(exc)[:1600]
                continue
            dump(target, parsed.model_dump())
            return parsed
        raise ValueError(
            "模型连续三次未返回有效拆解结构，已保留回复和已完成镜头；可更换模型后重试。"
            + error[:300]
        )

    def export(
        self, item: dict, analyses: list[dict], summary: DramaSummary, transcript: dict
    ) -> None:
        folder = self.folder(item["id"])
        staged = folder / "staged"
        if staged.exists():
            shutil.rmtree(staged)
        (staged / "assets").mkdir(parents=True)
        (staged / "shots").mkdir()
        note = "> 提示词是依据成片整理的复现建议，并非作者原始提示词；生成模型、种子与实际工作流无法由成片确定。\n\n"
        index = (
            f"# {item['title']}\n\n{note}{summary.synopsis}\n\n## 视觉风格\n\n{summary.visual_style}\n\n## 剪辑节奏\n\n{summary.rhythm}\n\n## 复现顺序\n\n"
            + "\n".join(f"{i}. {step}" for i, step in enumerate(summary.workflow, 1))
        )
        index += f"\n\n检测到 {max(s['take'] for s in item['shots'])} 个原始镜头，分为 {len(item['shots'])} 个分析片段。长镜头分段不代表剪辑切点。\n"
        index += "\n\n## 文档\n\n- [角色设定卡](characters.md)\n- [场景设定卡](scenes.md)\n- [原始文字稿](transcript.md)\n\n## 分镜目录\n\n"
        shot_by_id = {shot["id"]: shot for shot in item["shots"]}
        for kind, cards, title, prefix in (
            ("characters", summary.characters, "角色设定卡", "C"),
            ("scenes", summary.scenes, "场景设定卡", "S"),
        ):
            text = f"# {title}\n\n{note}"
            for i, card in enumerate(cards, 1):
                text += (
                    f"## {prefix}{i:03} · {card.name}\n\n{card.description}\n\n### 参考图复现提示词\n\n{card.prompt}\n\n出现镜头："
                    + "、".join(f"[{sid}](shots/{sid}.md)" for sid in card.shot_ids)
                    + "\n\n"
                )
                reference = shot_by_id[card.shot_ids[0]]["frames"]
                frame = reference[len(reference) // 2]
                source = folder / frame["file"]
                name = file_sha256(source)[:24] + ".jpg"
                shutil.copyfile(source, staged / "assets" / name)
                text += f"![镜头 {card.shot_ids[0]} · {frame['time']:.2f} 秒](assets/{name})\n\n"
            write_text(
                staged / f"{kind}.md",
                text + ("未识别到可确认的对象。\n" if not cards else ""),
            )
        for shot, analysis in zip(item["shots"], analyses):
            sid = shot["id"]
            index += f"- [{sid} · {analysis['title']}](shots/{sid}.md)（{shot['start']:.2f}–{shot['end']:.2f} 秒）\n"
            text = f"# {sid} · {analysis['title']}\n\n时间：{shot['start']:.2f}–{shot['end']:.2f} 秒\n\n"
            if shot["subdivision"]:
                text += f"> 原镜头 {shot['take']} 的第 {shot['part']} 个分析分段，分段边界不代表剪辑切镜。\n\n"
            for frame in shot["frames"]:
                source = folder / frame["file"]
                name = file_sha256(source)[:24] + ".jpg"
                shutil.copyfile(source, staged / "assets" / name)
                text += f"![{frame['time']:.2f} 秒](../assets/{name})\n\n"
            related = []
            for kind, cards, prefix in (
                ("characters", summary.characters, "C"),
                ("scenes", summary.scenes, "S"),
            ):
                related += [
                    f"[{prefix}{i:03} · {card.name}](../{kind}.md)"
                    for i, card in enumerate(cards, 1)
                    if sid in card.shot_ids
                ]
            if related:
                text += (
                    "相关设定卡："
                    + "、".join(related)
                    + "\n\n复现时以设定卡固定角色与场景细节。\n\n"
                )
            text += "## 画面观察\n\n"
            for title, field in (
                ("场景", "scene"),
                ("构图与景别", "composition"),
                ("光线与色彩", "lighting"),
                ("人物动作", "action"),
                ("镜头运动", "camera_motion"),
                ("剪辑", "editing"),
            ):
                if analysis[field]:
                    text += f"### {title}\n\n{analysis[field]}\n\n"
            text += (
                "### 出镜角色\n\n"
                + (
                    "\n".join("- " + c for c in analysis["characters"])
                    or "没有可确认的角色。"
                )
                + "\n\n"
            )
            nearby = [
                s
                for s in transcript["segments"]
                if s["start"] < shot["end"] and s["end"] > shot["start"] and s["text"]
            ]
            if nearby:
                text += (
                    "## 相邻台词\n\n"
                    + transcript["precision"]
                    + "\n\n"
                    + "\n\n".join(
                        f"{s['start']:.2f}–{s['end']:.2f} 秒：{s['text']}"
                        for s in nearby
                    )
                    + "\n\n"
                )
            text += (
                "## 复现建议\n\n"
                + note
                + "### 静帧提示词\n\n"
                + analysis["image_prompt"]
                + "\n\n### 视频生成提示词\n\n"
                + analysis["video_prompt"]
                + "\n"
            )
            write_text(staged / "shots" / f"{sid}.md", text)
        write_text(staged / "README.md", index)
        write_text(
            staged / "transcript.md",
            "# 原始文字稿\n\n"
            + transcript["precision"]
            + "\n\n"
            + "\n\n".join(
                f"## {s['start']:.2f}–{s['end']:.2f} 秒\n\n{s['text']}"
                for s in transcript["segments"]
            ),
        )
        CourseDocuments(folder).sync_generated(staged)
        shutil.rmtree(staged)

    def archive(self, drama_id: str) -> Path:
        folder = self.folder(drama_id)
        destination = folder / "export"
        if destination.exists():
            shutil.rmtree(destination)
        CourseDocuments(folder).export_to(destination)
        return Path(
            shutil.make_archive(str(folder / "drama-breakdown"), "zip", destination)
        )

    def run_rea_video(
        self,
        item: dict,
        folder: Path,
        video: Path,
        settings: DramaSettings,
        report: Callable[[str, float], None],
    ) -> None:
        import os
        import subprocess

        report("读取视频", 0.01)
        info = media.probe(video)
        if math.isfinite(info.duration_s) and info.duration_s > 0:
            item["duration"] = info.duration_s
            dump(folder / "drama.json", item)

        cmd_path = settings.rea_executable or shutil.which("rea-video") or "rea-video"
        if not Path(cmd_path).exists() and not shutil.which(cmd_path):
            raise ValueError(f"未找到 rea-video 可执行文件: {cmd_path}")

        env = os.environ.copy()
        vlm_url = settings.rea_vlm_base_url or "http://127.0.0.1:3425/v1"
        if settings.rea_vlm_base_url:
            env["REA_VLM_BASE_URL"] = settings.rea_vlm_base_url
        if settings.rea_vlm_api_key:
            env["REA_VLM_API_KEY"] = settings.rea_vlm_api_key
        if settings.rea_vlm_model:
            env["REA_VLM_MODEL"] = settings.rea_vlm_model
        if settings.https_proxy and not ("127.0.0.1" in vlm_url or "localhost" in vlm_url):
            env["HTTPS_PROXY"] = settings.https_proxy
        else:
            env.pop("HTTPS_PROXY", None)
            env.pop("HTTP_PROXY", None)
            env.pop("ALL_PROXY", None)
            env.pop("https_proxy", None)
            env.pop("http_proxy", None)
            env.pop("all_proxy", None)
        env["no_proxy"] = "127.0.0.1,localhost,127.0.0.1:3425,localhost:3425,*"
        env["NO_PROXY"] = "127.0.0.1,localhost,127.0.0.1:3425,localhost:3425,*"

        cmd = [
            str(cmd_path),
            "decompose",
            str(video.resolve()),
            "-o",
            str(folder.resolve()),
            "--vlm",
            "--lang",
            "zh",
        ]

        report("启动 rea-video 逆向分析", 0.03)
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            bufsize=1,
        )

        total_shots = 0
        current_shot = 0
        if process.stdout:
            for line in iter(process.stdout.readline, ""):
                line_str = line.strip()
                if not line_str:
                    continue
                if "detected" in line_str and "shots" in line_str:
                    try:
                        total_shots = int(line_str.split("detected")[1].split("shots")[0].strip())
                        report(f"检测到 {total_shots} 个镜头，开始分析", 0.1)
                    except Exception:
                        pass
                elif "separating stems" in line_str:
                    report("分离人声与伴奏音轨", 0.2)
                elif "packaging layer" in line_str:
                    report("提取剪辑包装与字幕层", 0.25)
                elif "shot_" in line_str and "INFO" in line_str:
                    current_shot += 1
                    frac = 0.3 + 0.6 * (current_shot / max(1, total_shots or 10))
                    report(f"分析镜头 {current_shot}/{total_shots or '?'}", min(0.92, frac))
                elif "done in" in line_str:
                    report("汇总报告与资产", 0.95)

        process.wait()
        if process.returncode != 0:
            raise RuntimeError(f"rea-video 执行失败，退出码 {process.returncode}")

        report_html = folder / "report.html"
        breakdown_json = folder / "breakdown.json"
        if not report_html.exists():
            raise RuntimeError("rea-video 执行完毕但未生成 report.html")

        item["has_report"] = True
        item["documents_ready"] = True
        if breakdown_json.exists():
            try:
                data = load(breakdown_json)
                item["verdict"] = data.get("summary", {}).get("verdict", "")
                if data.get("source", {}).get("duration"):
                    item["duration"] = data["source"]["duration"]
                item["shots"] = [
                    {
                        "id": s.get("id", f"shot_{i+1:03d}"),
                        "start": s.get("start", 0),
                        "end": s.get("end", 0),
                        "analysis": s,
                    }
                    for i, s in enumerate(data.get("shots", []))
                ]
            except Exception:
                pass
        item.update(status="done", error="")
        dump(folder / "drama.json", item)
        report("完成", 1.0)

    def _populate_documents_from_rea_toon(self, folder: Path) -> None:
        docs = folder / "documents"
        docs.mkdir(parents=True, exist_ok=True)
        if (folder / "VIDEO.md").exists():
            shutil.copyfile(folder / "VIDEO.md", docs / "README.md")
        if (folder / "pages" / "script.md").exists():
            shutil.copyfile(folder / "pages" / "script.md", docs / "剧本.md")
        if (folder / "video" / "x-report.md").exists():
            shutil.copyfile(folder / "video" / "x-report.md", docs / "爆款拆解分析.md")
        if (folder / "video" / "cast.md").exists():
            shutil.copyfile(folder / "video" / "cast.md", docs / "角色与造型.md")
        if (folder / "video" / "content.md").exists():
            shutil.copyfile(folder / "video" / "content.md", docs / "内容与结构.md")

        shots_dir = docs / "shots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        if (folder / "video" / "shots").is_dir():
            for f in (folder / "video" / "shots").glob("*.md"):
                shutil.copyfile(f, shots_dir / f.name)
                num = f.stem.lstrip("0") or "0"
                try:
                    three_digit = f"{int(num):03d}.md"
                    shutil.copyfile(f, shots_dir / three_digit)
                except ValueError:
                    pass

    def run_rea_toon(
        self,
        item: dict,
        folder: Path,
        video: Path,
        settings: DramaSettings,
        report: Callable[[str, float], None],
    ) -> None:
        import os
        import subprocess

        report("读取视频", 0.01)
        info = media.probe(video)
        if math.isfinite(info.duration_s) and info.duration_s > 0:
            item["duration"] = info.duration_s
            dump(folder / "drama.json", item)

        cmd_path = settings.rea_toon_executable or shutil.which("rea-toon") or "rea-toon"
        if not Path(cmd_path).exists() and not shutil.which(cmd_path):
            raise ValueError(f"未找到 rea-toon 可执行文件: {cmd_path}")

        env = os.environ.copy()
        vlm_url = settings.rea_vlm_base_url or "http://127.0.0.1:3425/v1"
        vlm_key = settings.rea_vlm_api_key or "magpie"
        vlm_model = settings.rea_vlm_model or "agy-cli/gemini-3.8-flash"
        env["REA_TOON_VLM_BASE_URL"] = vlm_url
        env["REA_TOON_VLM_API_KEY"] = vlm_key
        env["REA_TOON_VLM_MODEL"] = vlm_model
        env["OPENAI_BASE_URL"] = vlm_url
        env["OPENAI_API_KEY"] = vlm_key
        if settings.https_proxy and not ("127.0.0.1" in vlm_url or "localhost" in vlm_url):
            env["HTTPS_PROXY"] = settings.https_proxy
        else:
            env.pop("HTTPS_PROXY", None)
            env.pop("HTTP_PROXY", None)
            env.pop("ALL_PROXY", None)
            env.pop("https_proxy", None)
            env.pop("http_proxy", None)
            env.pop("all_proxy", None)
        env["no_proxy"] = "127.0.0.1,localhost,127.0.0.1:3425,localhost:3425,*"
        env["NO_PROXY"] = "127.0.0.1,localhost,127.0.0.1:3425,localhost:3425,*"

        title = item.get("title") or video.stem
        cmd = [
            str(cmd_path),
            "--backend",
            "api",
            "analyze",
            str(folder.resolve()),
            "--source",
            str(video.resolve()),
            "--title",
            str(title),
            "--lang",
            "zh",
        ]

        report("启动 rea-toon 短剧逆向分析", 0.03)
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            bufsize=1,
        )

        output_lines: list[str] = []
        if process.stdout:
            for line in iter(process.stdout.readline, ""):
                line_str = line.strip()
                if not line_str:
                    continue
                output_lines.append(line_str)
                if len(output_lines) > 50:
                    output_lines.pop(0)
                if "切镜" in line_str or "shots" in line_str:
                    report("切镜与感知抽帧", 0.15)
                elif "字幕" in line_str or "subtitles" in line_str:
                    report("识别与校对字幕", 0.3)
                elif "角色" in line_str or "cast" in line_str:
                    report("建立角色档案与说话人", 0.5)
                elif "深度拆解" in line_str or "breakdown" in line_str:
                    report("深度戏剧节拍与看点拆解", 0.7)
                elif "报告" in line_str or "report" in line_str:
                    report("生成爆款分析报告", 0.85)
                elif "剧本" in line_str or "script" in line_str:
                    report("生成标准剧本与分镜表", 0.92)

        process.wait()
        if process.returncode != 0:
            err_msg = " | ".join(output_lines[-5:]) if output_lines else f"退出码 {process.returncode}"
            raise RuntimeError(f"rea-toon 执行失败: {err_msg}")

        candidates = [
            folder / "index.html",
            folder / "report.html",
            folder / "pages" / "report.html",
            folder / "pages" / "breakdown.html",
        ]
        found_report = next((p for p in candidates if p.exists()), None)
        if not found_report:
            raise RuntimeError("rea-toon 执行完毕但未生成 index.html 或 report.html")
        if not (folder / "index.html").exists():
            shutil.copyfile(found_report, folder / "index.html")
        if not (folder / "report.html").exists():
            shutil.copyfile(found_report, folder / "report.html")

        self._populate_documents_from_rea_toon(folder)

        item["has_report"] = True
        item["documents_ready"] = True
        item.update(status="done", error="")
        dump(folder / "drama.json", item)
        report("完成", 1.0)

    def _populate_documents_from_rea_toon(self, folder: Path) -> None:
        docs = folder / "documents"
        docs.mkdir(parents=True, exist_ok=True)
        if (folder / "VIDEO.md").exists():
            shutil.copyfile(folder / "VIDEO.md", docs / "README.md")
        if (folder / "pages" / "script.md").exists():
            shutil.copyfile(folder / "pages" / "script.md", docs / "剧本.md")
        if (folder / "video" / "x-report.md").exists():
            shutil.copyfile(folder / "video" / "x-report.md", docs / "爆款拆解分析.md")
        if (folder / "video" / "cast.md").exists():
            shutil.copyfile(folder / "video" / "cast.md", docs / "角色与造型.md")
        if (folder / "video" / "content.md").exists():
            shutil.copyfile(folder / "video" / "content.md", docs / "内容与结构.md")

        shots_dir = docs / "shots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        if (folder / "video" / "shots").is_dir():
            for f in (folder / "video" / "shots").glob("*.md"):
                shutil.copyfile(f, shots_dir / f.name)
                num = f.stem.lstrip("0") or "0"
                try:
                    three_digit = f"{int(num):03d}.md"
                    shutil.copyfile(f, shots_dir / three_digit)
                except ValueError:
                    pass
