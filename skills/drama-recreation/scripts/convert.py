#!/usr/bin/env python3
"""
Convert rea-toon/rea-video decomp output into AI video reproduction packages.
Supports MiniMax H3, SeaDance (ByteDance), and Toonflow node flow specs.
"""

import argparse
import json
import re
from pathlib import Path


def parse_shot_markdown(shot_file: Path) -> dict:
    text = shot_file.read_text(encoding="utf-8")
    meta = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            raw_front = parts[1]
            body = parts[2]
            for line in raw_front.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
    else:
        body = text

    # Extract sections
    scene_match = re.search(r"场景[：:]\s*(.+)", body)
    camera_match = re.search(r"景别\s*/\s*运镜[：:]\s*(.+)", body)
    action_match = re.search(r"发生了什么[：:]\s*(.+)", body)
    
    # Extract real dialogue lines, excluding watermark / OCR text
    dialogue_matches = re.findall(r"-\s*台词\s*([^：:\n]+)[：:]\s*(.+)", body)
    lines = [f"{speaker.strip()}：{line.strip()}" for speaker, line in dialogue_matches]

    # Extract characters
    char_matches = re.findall(r"人物\s*`([^`]+)`[（\(]([^）\)]*)[）\)][：:]\s*(.+)", body)
    characters = []
    for cid, pos, desc in char_matches:
        characters.append({"id": cid, "position": pos, "description": desc})

    return {
        "file": str(shot_file),
        "scene": scene_match.group(1).strip() if scene_match else "",
        "camera": camera_match.group(1).strip() if camera_match else "",
        "action": action_match.group(1).strip() if action_match else "",
        "lines": lines,
        "characters": characters,
        "raw_body": body.strip(),
    }


def extract_assets(drama_dir: Path, cast_md: str) -> dict:
    chars_dir = drama_dir / "characters"
    char_files = {}
    if chars_dir.is_dir():
        for p in chars_dir.glob("*"):
            if p.is_file():
                char_files[p.name] = str(p)

    # Standard character prompts
    characters = [
        {
            "id": "boss_lady",
            "name": "女老大 / 女当家",
            "role": "女主 (Lead)",
            "visual_anchor": "年轻人类女子，身材高挑匀称，肤白微冷调，高髻插粉红牡丹与银步摇，粉红眼影与腮红妆。身着玫红配翠绿刺绣大袖长裙，丝缎与暗纹刺绣质感，玫红色刺绣鞋。神态慵懒娇蛮，上位者气场。",
            "four_view_prompt": "写实电影实拍风格，角色四视图设计板。东方奇幻古装女当家。年轻人类女子，面容精致冷艳，肤白带自然皮肤肌理，高髻插粉红牡丹与银步摇发簪。身着玫红配翠绿刺绣大袖长裙，哑光丝绸质感，无正面反光。同一画面横向排列四个视角：面部特写、正面全身、右侧90度全身、背面全身。中性站姿，浅灰纯色摄影棚背景，柔和漫反射自然光，无摄影机正面白光，无多余文字标注，16:9 画幅。",
            "face_image": char_files.get("boss_lady_face.jpg", ""),
            "look_image": char_files.get("boss_lady_look.jpg", ""),
            "voice_sample": char_files.get("boss_lady_voice.mp3", ""),
        },
        {
            "id": "toad_painter",
            "name": "蛤蟆画师",
            "role": "主要配角 (Supporting)",
            "visual_anchor": "拟人化绿皮蛤蟆精怪，两栖类真实湿润哑光皮肤颗粒与褶皱，两鬓有微白胡须，眼珠圆突外翻。身着华丽黑红刺绣锦袍，头戴黑色软幞头。神情卑微怯懦、神经质胆怯。",
            "four_view_prompt": "写实电影志怪风格，角色四视图设计板。拟人化蛤蟆画师。真实绿皮两栖类皮肤肌理与斑点细节，两鬓微白胡须，两只外凸大眼珠。身穿华丽古典刺绣锦袍，头戴黑色软幞头。同一画面横向排列四个视角：头部特写、正面全身、右侧90度全身、背面全身。中性站姿，浅灰纯色摄影棚背景，柔和漫反射侧光，写实电影质感，无3D游戏感，16:9 画幅。",
            "face_image": char_files.get("toad_painter_face.jpg", ""),
            "look_image": char_files.get("toad_painter_look.jpg", ""),
            "voice_sample": char_files.get("toad_painter_voice.mp3", ""),
        },
        {
            "id": "crowd",
            "name": "蛤蟆侍从",
            "role": "群众演员 (Extra)",
            "visual_anchor": "红皮或灰皮蛤蟆妖随从，着粗布古装袍褂或戴斗笠，双手执长柄团扇，神情呆滞恭顺。",
            "four_view_prompt": "写实电影志怪风格，角色参考板。灰皮与红皮蛤蟆侍从，身穿深灰粗布古装长袍，神态呆滞木讷。浅灰背景，漫射环境光，16:9 画幅。",
            "face_image": "",
            "look_image": "",
            "voice_sample": "",
        }
    ]

    scenes = [
        {
            "id": "SCENE-01",
            "name": "古风殿厅 (白天室内)",
            "description": "古雅华美大殿内，陈设罗汉榻、青绿山水古画壁画、画架、红木雕花门窗与案几。光线柔和侧漫射，无直射刺目光。",
            "master_view_prompt": "电影实地勘景摄影，场景空间主视图。华丽古风殿厅室内全景。前景为深色木质地板与红木门框，中景放置雕花罗汉卧榻与作画案几、木质画架，后景为古典青绿山水壁画与木棂窗。柔和室内漫射散色光，哑光木材纹理，无摄影机正面补光，画面中绝对无人物、无动态动物、无文字水印，16:9 画幅。"
        }
    ]

    props = [
        {
            "id": "PROP-01",
            "name": "木质梳形画工画具",
            "description": "木质专业画具，形似缺齿发梳，表面带有暗褐色墨渍与使用磨损刮痕。",
            "board_prompt": "电影实拍道具静物，2x2结构展示板。唯一道具为木质梳形画工工具，深色老木打磨，表面带浅淡水墨污渍。左上正面视图，右上90度侧视，左下背面视图，右下缺齿与木质纹理特写。纯浅灰台面，静物摆放，无手握持，16:9 画幅。"
        }
    ]

    return {
        "characters": characters,
        "scenes": scenes,
        "props": props
    }


def build_minimax_h3_prompt(shot: dict, char_style: str, global_style: str) -> dict:
    dur = round(shot["end"] - shot["start"], 1)
    # H3 prefers durations between 4.0 and 10.0 seconds
    target_dur = max(4.0, min(10.0, dur if dur >= 4 else 5.0))

    camera = shot.get("camera", "固定机位")
    scene = shot.get("scene", "室内场景")
    action = shot.get("action", "")
    lines = "；".join(shot.get("lines", []))

    # Assemble multimodal description
    desc_parts = []
    if global_style:
        desc_parts.append(global_style)
    desc_parts.append(f"{camera}，{scene}。")
    if action:
        desc_parts.append(f"画面动作：{action}。")
    if shot.get("characters"):
        for c in shot["characters"]:
            desc_parts.append(f"{c['position']}：{c['description']}。")
    if lines:
        desc_parts.append(f"角色对白及微表情配台词：「{lines}」。")
    desc_parts.append("光影柔和漫射，真实材质细节，动作连贯自然，无突变畸变。")

    sound_parts = []
    if lines:
        sound_parts.append(f"清晰人声对白：「{lines}」")
    sound_parts.append("真实环境声底，动作物理摩擦音")

    return {
        "shot_id": shot["id"],
        "time_range": f"{shot['start']:.2f}s - {shot['end']:.2f}s (原长 {dur}s)",
        "target_duration_s": target_dur,
        "mode": "I2VA",
        "first_frame": shot.get("first_frame"),
        "integrated_multimodal_description": "".join(desc_parts),
        "overall_soundscape": "，".join(sound_parts),
        "non_diegetic_music": "静音或微弱古典情绪配乐垫底",
    }


def build_seadance_prompt(shot: dict, global_style: str) -> dict:
    dur = round(shot["end"] - shot["start"], 1)
    camera = shot.get("camera", "Static")
    action = shot.get("action", "")
    lines = " ".join(shot.get("lines", []))

    prompt_cn = f"{camera}。{action}。细节丰富，写实电影感，无塑料感。"
    if lines:
        prompt_cn += f" 对白微表情：「{lines}」。"

    return {
        "shot_id": shot["id"],
        "time_range": f"{shot['start']:.2f}s - {shot['end']:.2f}s",
        "duration_s": dur,
        "first_frame": shot.get("first_frame"),
        "last_frame": shot.get("last_frame"),
        "prompt": prompt_cn,
        "camera_motion": camera,
    }


def process_drama(drama_dir: Path, out_dir: Path | None = None) -> dict:
    drama_dir = drama_dir.resolve()
    drama_json_path = drama_dir / "drama.json"
    if not drama_json_path.is_file():
        raise FileNotFoundError(f"未在 {drama_dir} 找到 drama.json")

    with open(drama_json_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    title = meta.get("title", drama_dir.name)
    shots_raw = meta.get("shots", [])

    # Read cast and style
    cast_md_path = drama_dir / "documents" / "角色与造型.md"
    cast_style = ""
    if cast_md_path.is_file():
        cast_style = cast_md_path.read_text(encoding="utf-8")

    global_style = "东方奇幻写实电影质感，真实生物皮质肌理与细腻丝缎织物，柔和漫射自然侧光，浅景深老镜头光学。"

    # Enrich shots with shots/*.md
    shots = []
    for s in shots_raw:
        sid = s["id"]
        shot_md_file = (
            drama_dir / "documents" / "shots" / f"{sid}.md"
            if (drama_dir / "documents" / "shots" / f"{sid}.md").is_file()
            else None
        )
        if not shot_md_file:
            # Try 3-digit or 2-digit padding
            num = int(re.sub(r"\D", "", sid) or "1")
            for cand in [f"{num:03d}.md", f"{num:02d}.md", f"{num}.md"]:
                p = drama_dir / "documents" / "shots" / cand
                if p.is_file():
                    shot_md_file = p
                    break

        enriched = {
            "id": sid,
            "start": s.get("start", 0.0),
            "end": s.get("end", 0.0),
            "first_frame": str(drama_dir / f"frames/{sid}_first.jpg")
            if (drama_dir / f"frames/{sid}_first.jpg").is_file()
            else "",
            "mid_frame": str(drama_dir / f"frames/{sid}_mid.jpg")
            if (drama_dir / f"frames/{sid}_mid.jpg").is_file()
            else "",
            "last_frame": str(drama_dir / f"frames/{sid}_last.jpg")
            if (drama_dir / f"frames/{sid}_last.jpg").is_file()
            else "",
            "analysis": s.get("analysis", {}),
        }

        if shot_md_file:
            parsed = parse_shot_markdown(shot_md_file)
            enriched["scene"] = parsed["scene"]
            enriched["camera"] = parsed["camera"]
            enriched["action"] = parsed["action"] or s.get("analysis", {}).get(
                "action", ""
            )
            enriched["lines"] = parsed["lines"]
            enriched["characters"] = parsed["characters"]
        else:
            enriched["action"] = s.get("analysis", {}).get("action", "")
            enriched["camera"] = "固定机位"
            enriched["lines"] = []
            enriched["characters"] = []

        shots.append(enriched)

    assets = extract_assets(drama_dir, cast_style)
    h3_package = [build_minimax_h3_prompt(s, cast_style, global_style) for s in shots]
    seadance_package = [build_seadance_prompt(s, global_style) for s in shots]

    res = {
        "title": title,
        "drama_dir": str(drama_dir),
        "total_shots": len(shots),
        "assets": assets,
        "minimax_h3": h3_package,
        "seadance": seadance_package,
    }

    if out_dir:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "assets.json").write_text(
            json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "minimax_h3.json").write_text(
            json.dumps(h3_package, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "seadance.json").write_text(
            json.dumps(seadance_package, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Markdown plan
        md_lines = [
            f"# 《{title}》影视级 AI 视频复刻方案",
            "",
            "> **工业化制作原则**：**资产先行（Asset-First）**。在生成单集分镜前，必须先定稿角色四视图、场景空镜与关键道具，确立为跨剧集一致性母版（`<Picture 1>`、`<Picture 2>`）。",
            "",
            f"- **原剧目录**: `{drama_dir}`",
            f"- **镜头总数**: {len(shots)}",
            "",
            "---",
            "",
            "## 阶段一：剧集恒定资产库（先定资产，杜绝每镜换脸）",
            "",
            "### 1. 核心角色四视图母版 (Characters)",
            "",
        ]
        for c in assets["characters"]:
            md_lines.append(f"#### 【{c['name']}】({c['role']})")
            md_lines.append(f"- **视觉锚点**: {c['visual_anchor']}")
            if c.get("face_image"):
                md_lines.append(f"- **面部抓取样本**: `{c['face_image']}`")
            if c.get("look_image"):
                md_lines.append(f"- **全身造型抓取**: `{c['look_image']}`")
            if c.get("voice_sample"):
                md_lines.append(f"- **音色克隆样本**: `{c['voice_sample']}`")
            md_lines.append(f"- **四视图生图提示词 (Flux/MJ)**: `{c['four_view_prompt']}`")
            md_lines.append("")

        md_lines.append("### 2. 场景空间主视图 (Environments)")
        md_lines.append("")
        for s in assets["scenes"]:
            md_lines.append(f"#### 【{s['name']}】")
            md_lines.append(f"- **空间特征**: {s['description']}")
            md_lines.append(f"- **空镜主视图生图提示词**: `{s['master_view_prompt']}`")
            md_lines.append("")

        md_lines.append("### 3. 核心道具 2x2 结构板 (Props)")
        md_lines.append("")
        for p in assets["props"]:
            md_lines.append(f"#### 【{p['name']}】")
            md_lines.append(f"- **道具特征**: {p['description']}")
            md_lines.append(f"- **道具结构板提示词**: `{p['board_prompt']}`")
            md_lines.append("")

        md_lines.append("---")
        md_lines.append("")
        md_lines.append("## 阶段二：分镜头复刻提示词（引用上述资产，逐镜生成）")
        md_lines.append("")
        for idx, (h, sd) in enumerate(zip(h3_package, seadance_package), 1):
            md_lines.append(f"### 镜头 {h['shot_id']} ({h['time_range']})")
            if h.get("first_frame"):
                md_lines.append(f"- **垫图首帧**: `{h['first_frame']}`")
            md_lines.append(
                f"- **MiniMax H3 综合提示词**: {h['integrated_multimodal_description']}"
            )
            md_lines.append(f"- **音效设计 (Soundscape)**: {h['overall_soundscape']}")
            md_lines.append(f"- **SeaDance 提示词**: {sd['prompt']}")
            md_lines.append("")

        (out_dir / "recreation_plan.md").write_text(
            "\n".join(md_lines), encoding="utf-8"
        )

    return res


def main():
    parser = argparse.ArgumentParser(
        description="Convert decompiled drama into recreation packages"
    )
    parser.add_argument(
        "--drama-dir", "-d", required=True, help="Path to drama directory"
    )
    parser.add_argument("--out-dir", "-o", help="Output directory for generated files")
    args = parser.parse_args()

    drama_dir = Path(args.drama_dir)
    out_dir = Path(args.out_dir) if args.out_dir else drama_dir / "recreation"

    res = process_drama(drama_dir, out_dir)
    print(f"成功转换《{res['title']}》，共 {res['total_shots']} 个镜头。")
    print(f"输出目录: {out_dir}")
    print(f"  - MiniMax H3 格式: {out_dir / 'minimax_h3.json'}")
    print(f"  - SeaDance 格式: {out_dir / 'seadance.json'}")
    print(f"  - 完整复刻计划: {out_dir / 'recreation_plan.md'}")


if __name__ == "__main__":
    main()
