"""Bilibili download jobs: per-item state, yt-dlp execution, cookie handling."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
from typing import Callable
from uuid import uuid4

import yt_dlp

from library.config import LibraryConfig
from library.course_documents import write_text
from library.download_settings import DownloadSettings

QUALITY_HEIGHTS = {"720P": 720, "1080P": 1080, "自动": None}


def dump(path: Path, value: object) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def safe_name(title: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|：？\n\r\t]', "_", str(title)).strip(" .")
    return cleaned[:80] or "video"


def item_url(item: dict) -> str:
    url = f"https://www.bilibili.com/video/{item['bvid']}"
    return f"{url}?p={item['part']}" if item.get("part") else url


class DownloadPipeline:
    """Download jobs recorded under data/downloads/<id>/download.json.

    Video files land in the user-chosen target directory; deleting a job
    never touches downloaded files.
    """

    def __init__(self, cfg: LibraryConfig):
        self.cfg = cfg
        self.root = cfg.data_dir / "downloads"

    def folder(self, download_id: str) -> Path:
        if len(download_id) != 32 or any(
            c not in "0123456789abcdef" for c in download_id
        ):
            raise FileNotFoundError("下载任务不存在")
        return self.root / download_id

    def read(self, download_id: str) -> dict:
        return load(self.folder(download_id) / "download.json")

    def list_items(self) -> list[dict]:
        return sorted(
            [load(p) for p in self.root.glob("*/download.json")],
            key=lambda item: item["created"],
            reverse=True,
        )

    def create(self, items: list[dict], target_dir: str, title: str = "") -> dict:
        if not items:
            raise ValueError("请至少勾选一个条目")
        target = Path(target_dir).expanduser()
        value = {
            "id": uuid4().hex,
            "title": title,
            "target_dir": str(target),
            "created": datetime.now(timezone.utc).isoformat(),
            "status": "queued",
            "stage": "等待处理",
            "progress": 0,
            "error": "",
            "items": [
                {
                    "bvid": item["bvid"],
                    "part": item.get("part"),
                    "title": item["title"],
                    "index": item.get("index") or position,
                    "status": "pending",
                    "file": "",
                }
                for position, item in enumerate(items, 1)
            ],
        }
        dump(self.folder(value["id"]) / "download.json", value)
        return value

    def delete(self, download_id: str) -> None:
        shutil.rmtree(self.folder(download_id))

    def retry(self, download_id: str) -> None:
        item = self.read(download_id)
        item.update(status="queued", stage="等待处理", progress=0, error="")
        item["items"] = [
            dict(entry, status="pending")
            if entry["status"] != "done"
            else entry
            for entry in item["items"]
        ]
        dump(self.folder(download_id) / "download.json", item)

    # -- execution ------------------------------------------------------------

    def run(
        self,
        download_id: str,
        progress: Callable[[str, float], None] = lambda *_: None,
    ) -> None:
        record = self.read(download_id)
        folder = self.folder(download_id)
        record.update(status="running", error="")
        settings = DownloadSettings.load(self.cfg.data_dir)
        use_manual_cookie = (
            settings.cookie_source == "manual" and bool(settings.sessdata)
        )
        cookie_path = (
            self._write_cookie(folder, settings) if use_manual_cookie else None
        )

        def report(stage: str, fraction: float) -> None:
            record.update(stage=stage, progress=fraction)
            dump(folder / "download.json", record)
            progress(stage, fraction)

        try:
            pending = [
                entry
                for entry in record["items"]
                if entry["status"] != "done"
            ]
            total = len(record["items"]) or 1
            done_before = total - len(pending)
            for offset, entry in enumerate(pending):
                base = (done_before + offset) / total
                span = 1 / total
                try:
                    self._download_one(
                        record, entry, settings, cookie_path,
                        lambda fraction: report(
                            f"下载 {entry['title'][:24]}",
                            min(0.999, base + span * fraction),
                        ),
                    )
                except Exception:
                    entry["status"] = "failed"
                    dump(folder / "download.json", record)
                    raise
            record.update(status="done", error="")
            report("完成", 1)
        except Exception as exc:
            record.update(status="failed", error=str(exc)[:1000])
            dump(folder / "download.json", record)
            raise
        finally:
            if cookie_path is not None:
                cookie_path.unlink(missing_ok=True)

    def _write_cookie(self, folder: Path, settings: DownloadSettings) -> Path:
        """Netscape cookie jar with SESSDATA for .bilibili.com."""
        path = folder / f"cookies-{uuid4().hex[:8]}.txt"
        path.write_text(
            "# Netscape HTTP Cookie File\n"
            ".bilibili.com\tTRUE\t/\tTRUE\t2145916800\tSESSDATA\t"
            f"{settings.sessdata}\n",
            encoding="utf-8",
        )
        return path

    def _download_one(
        self, record, entry, settings, cookie_path, report
    ) -> None:
        target = Path(record["target_dir"])
        target.mkdir(parents=True, exist_ok=True)
        stem = f"{entry['index']:02d} - {safe_name(entry['title'])}"
        opts = {
            "outtmpl": str(target / f"{stem}.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "noprogress": True,
            "progress_hooks": [
                lambda state: report(
                    min(
                        0.9,
                        (state.get("downloaded_bytes") or 0)
                        / max(1, state.get("total_bytes") or 1),
                    )
                )
                if state.get("status") == "downloading"
                else None
            ],
        }
        if cookie_path is not None:
            opts["cookiefile"] = str(cookie_path)
        elif settings.cookie_source == "browser":
            # 借用浏览器已有登录态（yt-dlp 读取浏览器 cookie 库；
            # macOS 首次使用会弹一次 Keychain 授权）。
            opts["cookiesfrombrowser"] = (settings.browser,)
        height = QUALITY_HEIGHTS.get(settings.quality)
        if height:
            opts["format"] = f"best*[height<={height}]/best"
        yt_dlp.YoutubeDL(opts).download([item_url(entry)])
        entry["file"] = str(self._find_file(target, stem))
        entry["status"] = "done"
        dump(self.folder(record["id"]) / "download.json", record)

    @staticmethod
    def _find_file(target: Path, stem: str) -> Path:
        candidates = sorted(target.glob(f"{stem}.*"))
        for path in candidates:
            if path.suffix.lower() == ".mp4":
                return path
        return candidates[0]
