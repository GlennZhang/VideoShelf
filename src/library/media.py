"""ffmpeg/ffprobe/OpenCV helpers for the analysis pipeline.

All functions are pure I/O helpers; pipeline.py owns stage sequencing and
caching. Original video files are only ever read, never modified (spec §1).
"""

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

FFPROBE_BIN = "ffprobe"
FFMPEG_BIN = "ffmpeg"


class MediaError(Exception):
    """Raised when ffmpeg/ffprobe fails on a media file."""


@dataclass(frozen=True)
class MediaInfo:
    duration_s: float
    width: int
    height: int
    video_codec: str
    audio_codec: Optional[str]
    container: str


def _run(cmd: List[str], timeout: float = 600.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=True
        )
    except FileNotFoundError as exc:
        raise MediaError(f"{cmd[0]} not installed") from exc
    except subprocess.CalledProcessError as exc:
        raise MediaError(f"{cmd[0]} failed: {(exc.stderr or '')[-500:]}") from exc


def probe(path: Path) -> MediaInfo:
    """Read stream metadata via ffprobe."""
    proc = _run(
        [
            FFPROBE_BIN,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    data = json.loads(proc.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        raise MediaError(f"no video stream in {path}")
    duration = float(v.get("duration") or fmt.get("duration") or 0.0)
    return MediaInfo(
        duration_s=duration,
        width=int(v.get("width") or 0),
        height=int(v.get("height") or 0),
        video_codec=v.get("codec_name") or "",
        audio_codec=a.get("codec_name") if a else None,
        container=(fmt.get("format_name") or "").split(",")[0],
    )


def extract_audio(
    path: Path, out_wav: Path, start_s: float = 0.0, duration_s: Optional[float] = None
) -> Path:
    """Extract 16 kHz mono WAV (optionally a chunk) for transcription."""
    cmd = [FFMPEG_BIN, "-y", "-loglevel", "error"]
    if start_s > 0:
        cmd += ["-ss", f"{start_s:.3f}"]
    cmd += ["-i", str(path)]
    if duration_s is not None:
        cmd += ["-t", f"{duration_s:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", str(out_wav)]
    _run(cmd)
    return out_wav


def detect_speech_ratio(wav_path: Path) -> float:
    """Estimate the fraction of non-silence via ffmpeg silencedetect.

    A rough energy heuristic (noise floor -35 dB, ≥2 s silence windows);
    the pipeline treats low ratios as "no usable speech" and falls back to
    visual-only analysis (design.md D5).
    """
    proc = _run(
        [
            FFMPEG_BIN,
            "-i",
            str(wav_path),
            "-af",
            "silencedetect=noise=-35dB:d=2",
            "-f",
            "null",
            "-",
        ],
        timeout=1800.0,
    )
    stderr = proc.stderr or ""
    total = 0.0
    for m in re.finditer(r"silence_duration: ([0-9.]+)", stderr):
        total += float(m.group(1))
    try:
        info = probe_audio_duration(wav_path)
    except MediaError:
        return 1.0
    if info <= 0:
        return 1.0
    ratio = max(0.0, min(1.0, 1.0 - total / info))
    logger.debug("speech ratio %.2f for %s", ratio, wav_path)
    return ratio


def probe_audio_duration(wav_path: Path) -> float:
    proc = _run(
        [
            FFPROBE_BIN,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(wav_path),
        ]
    )
    return float(json.loads(proc.stdout)["format"]["duration"])


@dataclass(frozen=True)
class SampledFrame:
    """A sampled keyframe stored as JPEG."""

    index: int
    timestamp_s: float
    path: Path


def sample_frames(
    video: Path,
    out_dir: Path,
    max_frames: int = 10,
    interval_s: float = 10.0,
    width: int = 640,
) -> List[SampledFrame]:
    """Sample up to ``max_frames`` frames evenly across the video.

    Uses OpenCV like the existing src/video_parser frame extraction, but
    writes JPEGs so the pipeline cache and screenshot dir can serve them.
    """
    import cv2  # local import: heavy dependency, keep module importable

    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise MediaError(f"cannot open video: {video}")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if frame_count <= 0 or fps <= 0:
            raise MediaError(f"cannot determine length: {video}")
        duration = frame_count / fps
        step = max(interval_s, duration / max_frames)
        targets = [
            min(t, duration - 0.1)
            for t in (i * step for i in range(max_frames))
            if t < duration
        ]
        frames: List[SampledFrame] = []
        for i, ts in enumerate(targets):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(ts * fps))
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            if w > width:
                scale = width / w
                frame = cv2.resize(
                    frame,
                    (width, int(h * scale)),
                    interpolation=cv2.INTER_AREA,
                )
            out_path = out_dir / f"frame_{i:02d}_t{ts:.1f}.jpg"
            cv2.imwrite(str(out_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            frames.append(SampledFrame(i, ts, out_path))
        return frames
    finally:
        cap.release()


def has_ffmpeg() -> bool:
    return all(shutil.which(b) for b in (FFPROBE_BIN, FFMPEG_BIN))
