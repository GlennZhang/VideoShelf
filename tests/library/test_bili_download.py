"""B 站下载：链接解析、下载管线与设置测试。"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from fastapi.testclient import TestClient
from library.bili_client import BiliError, parse_url, resolve
from library.config import LibraryConfig


def view_reply(pages=1, season=False):
    data = {
        "bvid": "BV1TestVideo",
        "title": "示例视频标题",
        "pages": [{"page": i + 1, "part": f"第{i + 1}节"} for i in range(pages)],
    }
    if season:
        episodes = [
            {"bvid": f"BVEpisode{i:02d}", "title": f"合集第{i + 1}集"}
            for i in range(3)
        ]
        data["ugc_season"] = {"title": "示例合集", "sections": [{"episodes": episodes}]}
    return data


def fake_get(payloads):
    calls = []

    def get(url, params=None):
        calls.append((url, dict(params or {})))
        return payloads(url, params)

    return get, calls


def test_parse_url_accepts_bv_in_common_shapes():
    assert parse_url("https://www.bilibili.com/video/BV1AbCdEfGh2?p=3") == {
        "bvid": "BV1AbCdEfGh2"
    }
    assert parse_url("m.bilibili.com/video/BV1AbCdEfGh2") == {"bvid": "BV1AbCdEfGh2"}
    assert parse_url("BV1AbCdEfGh2") == {"bvid": "BV1AbCdEfGh2"}


def test_parse_url_accepts_season_pages_and_rejects_others():
    assert parse_url(
        "https://space.bilibili.com/202224425/lists/192498?type=season"
    ) == {"season_id": "192498", "mid": "202224425"}
    assert parse_url(
        "https://space.bilibili.com/202224425/channel/collectiondetail?sid=192498"
    ) == {"season_id": "192498", "mid": "202224425"}
    with pytest.raises(BiliError):
        parse_url("https://example.com/video/BV1AbCdEfGh2")
    with pytest.raises(BiliError):
        parse_url("https://www.bilibili.com/bangumi/media/md123")


def test_resolve_returns_season_from_member_video():
    get, calls = fake_get(lambda url, params: view_reply(season=True))
    result = resolve("https://www.bilibili.com/video/BV1TestVideo", get=get)
    assert result["kind"] == "season"
    assert result["title"] == "示例合集"
    assert [item["bvid"] for item in result["items"]] == [
        "BVEpisode00",
        "BVEpisode01",
        "BVEpisode02",
    ]
    assert all(item["part"] is None for item in result["items"])


def test_resolve_returns_multi_part_list():
    get, _ = fake_get(lambda url, params: view_reply(pages=3))
    result = resolve("BV1TestVideo", get=get)
    assert result["kind"] == "multi_part"
    assert [item["part"] for item in result["items"]] == [1, 2, 3]
    assert result["items"][0]["title"] == "第1节"


def test_resolve_returns_single_video():
    get, _ = fake_get(lambda url, params: view_reply(pages=1))
    result = resolve("BV1TestVideo", get=get)
    assert result["kind"] == "single"
    assert len(result["items"]) == 1 and result["items"][0]["part"] is None


def test_resolve_season_page_paginates_all_archives():
    def payloads(url, params):
        assert params["season_id"] == "192498" and params["mid"] == "202224425"
        page = params.get("page_num", 1)
        if page == 1:
            archives = [{"bvid": f"BV{i}", "title": f"第{i}集"} for i in range(1, 31)]
            return {
                "meta": {"name": "分页合集"},
                "archives": archives,
                "page": {"page_num": 1, "page_size": 30, "total": 32},
            }
        return {
            "meta": {"name": "分页合集"},
            "archives": [{"bvid": "BV32", "title": "第32集"}],
            "page": {"page_num": 2, "page_size": 30, "total": 32},
        }

    get, calls = fake_get(payloads)
    result = resolve(
        "https://space.bilibili.com/202224425/lists/192498?type=season", get=get
    )
    assert result["kind"] == "season" and result["title"] == "分页合集"
    assert len(result["items"]) == 32
    assert result["items"][-1]["bvid"] == "BV32"


def test_download_settings_roundtrip(tmp_path):
    from library.download_settings import DownloadSettings

    settings = DownloadSettings(
        cookie_source="browser",
        browser="chrome",
        sessdata="abc123",
        quality="1080P",
        download_dir="/tmp/bili",
    )
    settings.save(tmp_path)
    loaded = DownloadSettings.load(tmp_path)
    assert loaded == settings
    # 默认：从浏览器读取 + Chrome；旧设置文件缺新字段时兼容回落
    assert DownloadSettings.load(tmp_path / "missing").cookie_source == "browser"
    (tmp_path / "download-settings.json").write_text(
        '{"sessdata":"old","quality":"自动（登录后最高）","download_dir":""}',
        encoding="utf-8",
    )
    legacy = DownloadSettings.load(tmp_path)
    assert legacy.sessdata == "old" and legacy.cookie_source == "manual"


# ---- 下载管线（mock yt-dlp） ------------------------------------------------


class FakeYoutubeDL:
    calls: list = []
    fail_urls: set = set()
    cookie_contents: str = ""

    def __init__(self, opts):
        self.opts = opts
        FakeYoutubeDL.calls.append(opts)
        self.hook = opts["progress_hooks"][0]
        if opts.get("cookiefile"):
            FakeYoutubeDL.cookie_contents = Path(
                opts["cookiefile"]
            ).read_text(encoding="utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def download(self, urls):
        url = urls[0]
        if url in FakeYoutubeDL.fail_urls:
            raise RuntimeError("下载失败")
        self.hook(
            {"status": "downloading", "downloaded_bytes": 4, "total_bytes": 8}
        )
        out = self.opts["outtmpl"].replace("%(ext)s", "mp4")
        Path(out).write_bytes(b"video")
        self.hook({"status": "finished", "filename": out})


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from library.download_pipeline import DownloadPipeline

    FakeYoutubeDL.calls = []
    FakeYoutubeDL.fail_urls = set()
    monkeypatch.setattr(
        "library.download_pipeline.yt_dlp",
        SimpleNamespace(YoutubeDL=FakeYoutubeDL),
    )
    cfg = LibraryConfig(data_dir=tmp_path / "data")
    return DownloadPipeline(cfg), cfg


def sample_items():
    return [
        {"bvid": "BV1A", "part": None, "title": "第一集：入门", "index": 1},
        {"bvid": "BV1B", "part": 2, "title": "第二集：进阶/高级", "index": 2},
    ]


def test_create_list_and_read(downloads):
    pipe, cfg = downloads
    record = pipe.create(sample_items(), str(cfg.data_dir / "videos"), "示例合集")
    assert record["status"] == "queued"
    assert all(item["status"] == "pending" for item in record["items"])
    assert pipe.read(record["id"])["title"] == "示例合集"
    assert len(pipe.list_items()) == 1


def test_run_downloads_each_item_with_urls_and_names(downloads):
    pipe, cfg = downloads
    record = pipe.create(sample_items(), str(cfg.data_dir / "videos"), "示例合集")
    pipe.run(record["id"])
    done = pipe.read(record["id"])
    assert done["status"] == "done" and done["progress"] == 1
    opts = FakeYoutubeDL.calls
    assert opts[0]["noplaylist"] is True and "cookiefile" not in opts[0]
    assert opts[0]["outtmpl"].endswith("01 - 第一集_入门.%(ext)s")
    assert opts[1]["outtmpl"].endswith("02 - 第二集_进阶_高级.%(ext)s")
    items = done["items"]
    assert items[0]["file"].endswith("01 - 第一集_入门.mp4")
    assert items[1]["file"].endswith("02 - 第二集_进阶_高级.mp4")


def test_run_failure_keeps_done_items_and_allows_retry(downloads):
    pipe, cfg = downloads
    record = pipe.create(sample_items(), str(cfg.data_dir / "videos"), "示例合集")
    fail_url = "https://www.bilibili.com/video/BV1B?p=2"
    FakeYoutubeDL.fail_urls = {fail_url}
    with pytest.raises(RuntimeError):
        pipe.run(record["id"])
    failed = pipe.read(record["id"])
    assert failed["status"] == "failed"
    assert failed["items"][0]["status"] == "done"
    assert failed["items"][1]["status"] == "failed"
    FakeYoutubeDL.fail_urls = set()
    FakeYoutubeDL.calls = []
    pipe.run(record["id"])
    assert pipe.read(record["id"])["status"] == "done"
    assert len(FakeYoutubeDL.calls) == 1  # 只重下失败的条目


def test_run_writes_and_removes_cookie_file(downloads):
    pipe, cfg = downloads
    from library.download_settings import DownloadSettings

    DownloadSettings(cookie_source="manual", sessdata="secret123").save(cfg.data_dir)
    record = pipe.create(sample_items()[:1], str(cfg.data_dir / "videos"), "示例")
    pipe.run(record["id"])
    cookie_files = list(pipe.folder(record["id"]).glob("cookies*.txt"))
    assert not cookie_files  # 结束后清理
    assert "secret123" in FakeYoutubeDL.cookie_contents


def test_cookie_source_browser_and_none(downloads):
    pipe, cfg = downloads
    from library.download_settings import DownloadSettings

    DownloadSettings(cookie_source="browser", browser="edge").save(cfg.data_dir)
    record = pipe.create(sample_items()[:1], str(cfg.data_dir / "videos"), "示例")
    pipe.run(record["id"])
    assert FakeYoutubeDL.calls[-1].get("cookiesfrombrowser") == ("edge",)

    DownloadSettings(cookie_source="none").save(cfg.data_dir)
    record = pipe.create(sample_items()[:1], str(cfg.data_dir / "videos"), "示例2")
    pipe.run(record["id"])
    opts = FakeYoutubeDL.calls[-1]
    assert "cookiesfrombrowser" not in opts and "cookiefile" not in opts


def test_worker_handles_download_task(downloads, monkeypatch):
    pipe, cfg = downloads
    from library.worker import Worker

    record = pipe.create(sample_items()[:1], str(cfg.data_dir / "videos"), "示例")
    lib = Mock()
    Worker(lib, cfg, Mock())._handle(
        {"id": 3, "type": "download", "payload": {"download_id": record["id"]}}
    )
    assert pipe.read(record["id"])["status"] == "done"
    lib.update_task.assert_called_with(3, status="done", progress=1.0)


# ---- API --------------------------------------------------------------------


def download_api(downloads, monkeypatch):
    from library.app import create_app, get_state

    pipe, cfg = downloads
    monkeypatch.setattr("library.worker.Worker.start", lambda self: None)
    monkeypatch.setattr("library.worker.Worker.stop", lambda self, **kwargs: None)
    http = TestClient(create_app(cfg))
    return http, get_state(), pipe


def test_api_settings_roundtrip(downloads, monkeypatch):
    http, _, _ = download_api(downloads, monkeypatch)
    assert http.get("/api/download-settings").json()["sessdata"] == ""
    saved = http.put(
        "/api/download-settings",
        json={"sessdata": "tok", "quality": "1080P", "download_dir": "/tmp/v"},
    )
    assert saved.status_code == 200
    assert http.get("/api/download-settings").json()["sessdata"] == "tok"


def test_api_resolve_rejects_foreign_domain(downloads, monkeypatch):
    http, _, _ = download_api(downloads, monkeypatch)
    response = http.post(
        "/api/downloads/resolve", json={"url": "https://example.com/x"}
    )
    assert response.status_code == 400


def test_api_create_list_retry_delete(downloads, monkeypatch):
    http, state, pipe = download_api(downloads, monkeypatch)
    created = http.post(
        "/api/downloads",
        json={
            "items": sample_items()[:1],
            "target_dir": str(pipe.cfg.data_dir / "videos"),
            "title": "示例合集",
        },
    )
    assert created.status_code == 200
    did = created.json()["id"]
    assert any(d["id"] == did for d in http.get("/api/downloads").json())
    state.worker._handle(state.lib.next_task())
    detail = http.get(f"/api/downloads/{did}").json()
    assert detail["status"] == "done" and detail["items"][0]["file"].endswith(
        ".mp4"
    )
    retry = http.post(f"/api/downloads/{did}/retry")
    assert retry.status_code == 200
    assert http.delete(f"/api/downloads/{did}").status_code == 200
    assert http.get(f"/api/downloads/{did}").status_code == 404
    # 删除任务不删已下载的视频文件
    assert Path(detail["items"][0]["file"]).is_file()


# ---- 扫码登录 ----------------------------------------------------------------


def qr_transport(responses, cookies=None):
    """HTTP transport mock: (method, url, params) -> (data, cookie_dict)."""
    calls = []

    def transport(method, url, params=None):
        calls.append((method, url, dict(params or {})))
        data, cookie = responses(url, params)
        return data, (cookie if cookie is not None else (cookies or {}))

    return transport, calls


def test_qr_login_start_and_poll_states():
    from library.bili_client import QrLogin

    def responses(url, params):
        if url.endswith("generate"):
            return {"url": "https://passport.bilibili.com/h5-app/passport/login/scan?qrcode_key=KEY", "qrcode_key": "KEY"}, {}
        code = params.get("qrcode_key") and {"KEY": "86101", "SCANNED": "86090", "OK": "0"}.get(params["qrcode_key"], "86038")
        return {"code": int(code), "message": ""}, {}

    transport, calls = qr_transport(responses)
    qr = QrLogin(transport=transport)
    started = qr.start()
    assert started["qrcode_key"] == "KEY" and started["svg"].startswith("data:image/svg+xml")
    assert qr.poll("KEY")["status"] == "waiting"
    assert qr.poll("SCANNED")["status"] == "scanned"
    assert qr.poll("GONE")["status"] == "expired"


def test_qr_login_success_extracts_sessdata_from_cookies_then_url():
    from urllib.parse import urlencode

    from library.bili_client import QrLogin

    def responses(url, params):
        if url.endswith("generate"):
            return {"url": "qr", "qrcode_key": "K"}, {}
        # 返回 None 让 transport 回落到 fixture 提供的 cookies
        return {"code": 0, "url": "https://passport.bilibili.com/crossDomain?" + urlencode({"SESSDATA": "from-url", "DedeUserID": "1"})}, None

    transport, _ = qr_transport(responses, cookies={"SESSDATA": "from-cookie", "DedeUserID": "7"})
    qr = QrLogin(transport=transport)
    qr.start()
    result = qr.poll("K")
    assert result["status"] == "success" and result["sessdata"] == "from-cookie"

    # Set-Cookie 缺失时回落到 data.url 的 query 参数
    transport2, _ = qr_transport(responses, cookies={})
    qr2 = QrLogin(transport=transport2)
    qr2.start()
    assert qr2.poll("K")["sessdata"] == "from-url"


def test_api_qr_login_flow_saves_sessdata(downloads, monkeypatch):
    from library import bili_client

    http, state, pipe = download_api(downloads, monkeypatch)
    states = iter(["waiting", "scanned", "success"])

    class FakeQr:
        def __init__(self, transport=None):
            pass

        def start(self):
            return {"qrcode_key": "KEY", "url": "u", "svg": "data:image/svg+xml;base64,QQ=="}

        def poll(self, key):
            status = next(states)
            return (
                {"status": "success", "sessdata": "qr-sessdata"}
                if status == "success"
                else {"status": status, "message": ""}
            )

    monkeypatch.setattr(bili_client, "QrLogin", FakeQr)
    started = http.post("/api/downloads/qr-login/start").json()
    assert started["qrcode_key"] == "KEY" and started["svg"].startswith("data:")
    assert http.get("/api/downloads/qr-login/poll?key=KEY").json()["status"] == "waiting"
    assert http.get("/api/downloads/qr-login/poll?key=KEY").json()["status"] == "scanned"
    final = http.get("/api/downloads/qr-login/poll?key=KEY").json()
    assert final == {"status": "success"}
    settings = http.get("/api/download-settings").json()
    assert settings["cookie_source"] == "manual" and settings["sessdata"] == "qr-sessdata"
