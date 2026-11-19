"""Download settings persisted under the data dir (data/download-settings.json)."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

SETTINGS_NAME = "download-settings.json"

CookieSource = Literal["none", "browser", "manual"]
BrowserName = Literal["chrome", "edge", "firefox", "brave", "chromium", "safari"]


class DownloadSettings(BaseModel):
    # none: 未登录清晰度；browser: 借用浏览器登录态（推荐，永不过期）；
    # manual: 手动粘贴 SESSDATA（旧设置文件缺字段时回落到此值以保留 sessdata）。
    cookie_source: CookieSource = "browser"
    browser: BrowserName = "chrome"
    sessdata: str = Field(default="", max_length=2000)
    quality: str = Field(default="自动（登录后最高）", max_length=50)
    download_dir: str = Field(default="", max_length=2000)

    @classmethod
    def path(cls, data_dir: Path) -> Path:
        return Path(data_dir) / SETTINGS_NAME

    @classmethod
    def load(cls, data_dir: Path) -> "DownloadSettings":
        path = cls.path(data_dir)
        if not path.is_file():
            return cls()
        raw = path.read_text(encoding="utf-8")
        settings = cls.model_validate_json(raw)
        # 旧文件没有 cookie_source：若存了 sessdata 则视为 manual，否则 browser。
        if "cookie_source" not in raw:
            settings = settings.model_copy(
                update={"cookie_source": "manual" if settings.sessdata else "browser"}
            )
        return settings

    def save(self, data_dir: Path) -> None:
        path = self.path(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
