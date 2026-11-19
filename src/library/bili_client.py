"""Bilibili link parsing and collection listing via public web APIs.

Two collection shapes exist on Bilibili and both are supported:
- UGC season (合集): many BV ids grouped by the uploader.
- Multi-part video (多P): a single BV id with many parts.

The list endpoints are public (no login); downloading is delegated to yt-dlp.
"""

import re
from typing import Callable
from urllib.parse import parse_qs, urlparse

import httpx

VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
SEASON_URL = (
    "https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"
)
PAGE_SIZE = 30
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_BV = re.compile(r"(BV[0-9A-Za-z]{10})")


class BiliError(ValueError):
    """Raised for unsupported links or API failures."""


def parse_url(url: str) -> dict:
    """Return {'bvid': ...} or {'season_id': ..., 'mid': ...} from a link."""
    text = (url or "").strip()
    if _BV.fullmatch(text):
        return {"bvid": text}
    if "://" not in text:
        text = "https://" + text
    host = urlparse(text).netloc.lower()
    if not any(
        host == domain or host.endswith("." + domain)
        for domain in ("bilibili.com", "b23.tv")
    ):
        raise BiliError("仅支持 bilibili.com 或 b23.tv 链接")
    parsed = urlparse(text)
    query = parse_qs(parsed.query)
    segments = [s for s in parsed.path.split("/") if s]
    # New season page: space.bilibili.com/{mid}/lists/{sid}?type=season
    if len(segments) >= 3 and segments[1] == "lists" and segments[0].isdigit():
        return {"season_id": segments[2], "mid": segments[0]}
    # Old season page: .../channel/collectiondetail?sid=... with mid in path
    if "sid" in query and segments and segments[0].isdigit():
        return {"season_id": query["sid"][0], "mid": segments[0]}
    match = _BV.search(text)
    if match:
        return {"bvid": match.group(1)}
    raise BiliError("无法从链接中识别视频或合集，请粘贴视频或合集页地址")


def http_get(url: str, params: dict | None = None) -> dict:
    """Default transport: one GET returning parsed JSON."""
    try:
        response = httpx.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise BiliError(f"B 站接口请求失败：{exc}") from exc
    if payload.get("code") != 0:
        raise BiliError(
            f"B 站接口返回错误：{payload.get('code')} {payload.get('message')}"
        )
    return payload["data"]


def resolve(
    url: str, get: Callable[[str, dict | None], dict] | None = None
) -> dict:
    """List collection items for a video or season page link.

    Returns {kind: 'season'|'multi_part'|'single', title, items} where each
    item is {bvid, part (int|None), title, index}.
    """
    get = get or http_get
    ref = parse_url(url)
    if "bvid" in ref:
        data = get(VIEW_URL, {"bvid": ref["bvid"]})
        season = data.get("ugc_season")
        if season:
            episodes = [
                episode
                for section in season.get("sections", [])
                for episode in section.get("episodes", [])
            ]
            if episodes:
                return {
                    "kind": "season",
                    "title": season.get("title") or data.get("title") or "",
                    "items": [
                        {
                            "bvid": episode["bvid"],
                            "part": None,
                            "title": episode.get("title") or episode["bvid"],
                            "index": position,
                        }
                        for position, episode in enumerate(episodes, 1)
                    ],
                }
        pages = data.get("pages") or []
        if len(pages) > 1:
            return {
                "kind": "multi_part",
                "title": data.get("title") or ref["bvid"],
                "items": [
                    {
                        "bvid": ref["bvid"],
                        "part": page.get("page"),
                        "title": page.get("part") or f"P{page.get('page')}",
                        "index": page.get("page"),
                    }
                    for page in pages
                ],
            }
        return {
            "kind": "single",
            "title": data.get("title") or ref["bvid"],
            "items": [
                {
                    "bvid": ref["bvid"],
                    "part": None,
                    "title": data.get("title") or ref["bvid"],
                    "index": 1,
                }
            ],
        }

    archives: list[dict] = []
    meta_name = ""
    page_num = 1
    while True:
        data = get(
            SEASON_URL,
            {
                "season_id": ref["season_id"],
                "mid": ref["mid"],
                "page_num": page_num,
                "page_size": PAGE_SIZE,
            },
        )
        meta_name = meta_name or (data.get("meta") or {}).get("name") or ""
        archives.extend(data.get("archives") or [])
        total = (data.get("page") or {}).get("total") or len(archives)
        if len(archives) >= total or page_num > 100:
            break
        page_num += 1
    if not archives:
        raise BiliError("合集为空或接口未返回条目")
    return {
        "kind": "season",
        "title": meta_name,
        "items": [
            {
                "bvid": archive["bvid"],
                "part": None,
                "title": archive.get("title") or archive["bvid"],
                "index": position,
            }
            for position, archive in enumerate(archives, 1)
        ],
    }


QR_GENERATE_URL = (
    "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
)
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
QR_CODE_STATES = {86101: "waiting", 86090: "scanned", 86038: "expired"}


def _http_json_with_cookies(method: str, url: str, params: dict | None = None):
    """Default transport returning (data, cookie_dict)."""
    try:
        response = httpx.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise BiliError(f"B 站接口请求失败：{exc}") from exc
    if payload.get("code") != 0:
        raise BiliError(
            f"B 站接口返回错误：{payload.get('code')} {payload.get('message')}"
        )
    return payload["data"], dict(response.cookies)


class QrLogin:
    """Bilibili QR-code login: generate a code, poll until scanned.

    On success the SESSDATA cookie is extracted (from Set-Cookie first,
    falling back to the cross-domain url query) and never logged.
    """

    def __init__(self, transport=None):
        self._transport = transport or _http_json_with_cookies

    def start(self) -> dict:
        data, _ = self._transport("GET", QR_GENERATE_URL)
        qrcode_key = data.get("qrcode_key") or ""
        url = data.get("url") or ""
        if not qrcode_key or not url:
            raise BiliError("B 站未返回二维码信息")
        return {"qrcode_key": qrcode_key, "url": url, "svg": _qr_svg(url)}

    def poll(self, qrcode_key: str) -> dict:
        data, cookies = self._transport("GET", QR_POLL_URL, {"qrcode_key": qrcode_key})
        code = int(data.get("code") or 0)
        if code == 0:
            sessdata = cookies.get("SESSDATA") or ""
            if not sessdata:
                query = parse_qs(str(data.get("url") or "").split("?", 1)[-1])
                sessdata = (query.get("SESSDATA") or [""])[0]
            if not sessdata:
                return {"status": "error", "message": "登录成功但未取到 SESSDATA"}
            return {"status": "success", "sessdata": sessdata}
        return {
            "status": QR_CODE_STATES.get(code, "error"),
            "message": str(data.get("message") or code),
        }


def _qr_svg(content: str) -> str:
    """Render QR content as an inline data-uri SVG (no PIL dependency)."""
    import base64
    from io import BytesIO

    import qrcode
    import qrcode.image.svg

    buffer = BytesIO()
    image = qrcode.make(content, image_factory=qrcode.image.svg.SvgPathImage)
    image.save(buffer)
    return (
        "data:image/svg+xml;base64,"
        + base64.b64encode(buffer.getvalue()).decode()
    )
