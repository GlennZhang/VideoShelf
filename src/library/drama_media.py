"""Chronological shot sampling for short-drama reconstruction."""

import math
import re
from pathlib import Path

from library import media


def extract_shots(video: Path, folder: Path, duration: float) -> list[dict]:
    """Detect cuts, then label subdivisions of long takes explicitly."""
    result = media._run(
        [
            media.FFMPEG_BIN,
            "-hide_banner",
            "-i",
            str(video),
            "-vf",
            "scale=320:-2,select='gt(scene,0.30)',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        timeout=1800,
    )
    cuts = [0.0]
    for value in re.findall(r"pts_time:([0-9.]+)", result.stderr):
        time = float(value)
        if time - cuts[-1] > 0.08 and duration - time > 0.08:
            cuts.append(time)
    cuts.append(duration)
    import cv2

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise media.MediaError("无法打开视频进行抽帧")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if frame_count < 1:
        cap.release()
        raise media.MediaError("视频没有可读取的画面")
    shots = []
    folder.mkdir(parents=True, exist_ok=True)
    try:
        for take, (start, end) in enumerate(zip(cuts, cuts[1:]), 1):
            pieces = max(1, math.ceil((end - start) / 8))
            for part in range(pieces):
                a = start + (end - start) * part / pieces
                b = start + (end - start) * (part + 1) / pieces
                sid = f"{len(shots) + 1:03}"
                count = max(3, min(12, math.ceil(b - a) + 1))
                inset = min(0.08, (b - a) / 4)
                frames = []
                for i in range(count):
                    time = a + inset + (b - a - 2 * inset) * i / (count - 1)
                    # Seek a real frame index so the final sample cannot land past EOF.
                    index = min(frame_count - 1, int(time * fps))
                    cap.set(cv2.CAP_PROP_POS_FRAMES, index)
                    ok, image = cap.read()
                    if not ok:
                        raise media.MediaError(f"镜头 {sid} 在 {time:.3f} 秒抽帧失败")
                    height, width = image.shape[:2]
                    if width > 960:
                        image = cv2.resize(image, (960, round(height * 960 / width)))
                    target = folder / f"{sid}-{i:02}.jpg"
                    if not cv2.imwrite(str(target), image):
                        raise media.MediaError(f"镜头 {sid} 截图写入失败")
                    frames.append({"time": index / fps, "file": target.name})
                shots.append(
                    {
                        "id": sid,
                        "take": take,
                        "start": a,
                        "end": b,
                        "subdivision": pieces > 1,
                        "part": part + 1,
                        "frames": frames,
                    }
                )
    finally:
        cap.release()
    return shots
