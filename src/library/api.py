"""REST API for the library web app (all routes, one router)."""

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from library import media
from library.db import Library

router = APIRouter(prefix="/api")

#: browser-safe combos remuxed with -c copy; anything else transcodes
REMUX_CODECS = {"h264"}
REMUX_AUDIO = {"aac", "mp3", "alac"}


class FolderIn(BaseModel):
    path: str


class VideoPatch(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    category: Optional[str] = None
    topic: Optional[str] = None
    form: Optional[str] = None
    tags: Optional[List[str]] = None
    notes: Optional[str] = None
    favorite: Optional[bool] = None


class CollectionIn(BaseModel):
    name: str


class CollectionItemIn(BaseModel):
    content_id: int


class MergeIn(BaseModel):
    keep_id: int
    merge_id: int


class PersonPatch(BaseModel):
    canonical_name: Optional[str] = None
    aliases: Optional[List[str]] = None
    confirmed_status: Optional[str] = None


class BatchIn(BaseModel):
    content_ids: List[int]
    action: str  # artist | category | tag | collection | favorite
    value: Any


class SearchIn(BaseModel):
    q: Optional[str] = None


def _lib() -> Library:
    from library.app import get_state

    return get_state().lib


def _video_or_404(lib: Library, content_id: int) -> Dict[str, Any]:
    video = lib.get_video(content_id)
    if video is None:
        raise HTTPException(404, f"content {content_id} not found")
    return video


def _decorate(lib: Library, video: Dict[str, Any]) -> Dict[str, Any]:
    """Attach people/screenshots and parse tag JSON for the UI."""
    video = dict(video)
    for key in ("ai_tags", "user_tags"):
        if isinstance(video.get(key), str):
            try:
                video[key] = json.loads(video[key])
            except json.JSONDecodeError:
                video[key] = []
    video["tags"] = video.get("user_tags") or video.get("ai_tags") or []
    video["people"] = lib.video_people(video["content_id"])
    video["screenshots"] = lib.get_screenshots(video["content_id"])
    video["in_collections"] = [
        c["id"]
        for c in lib.list_collections()
        if video["content_id"]
        in [
            v["content_id"] for v in lib.list_videos(collection_id=c["id"], limit=10000)
        ]
    ]
    return video


# -- folders ---------------------------------------------------------------


@router.post("/folders")
def add_folder(body: FolderIn):
    lib = _lib()
    path = Path(body.path).expanduser()
    if not path.is_dir():
        raise HTTPException(400, f"not a directory: {body.path}")
    folder_id = lib.add_folder(str(path))
    task_id = lib.enqueue_task(
        "scan", {"folder_id": folder_id, "path": str(path.resolve())}
    )
    return {"folder_id": folder_id, "scan_task_id": task_id}


@router.get("/folders")
def list_folders():
    return _lib().list_folders()


@router.delete("/folders/{folder_id}")
def remove_folder(folder_id: int):
    _lib().remove_folder(folder_id)
    return {"ok": True}


@router.post("/folders/{folder_id}/rescan")
def rescan_folder(folder_id: int):
    lib = _lib()
    folders = {f["id"]: f for f in lib.list_folders()}
    if folder_id not in folders:
        raise HTTPException(404, "folder not found")
    task_id = lib.enqueue_task(
        "scan", {"folder_id": folder_id, "path": folders[folder_id]["path"]}
    )
    return {"scan_task_id": task_id}


# -- tasks -----------------------------------------------------------------


@router.get("/tasks")
def list_tasks(limit: int = Query(200, le=1000)):
    return _lib().list_tasks(limit=limit)


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: int):
    lib = _lib()
    tasks = {t["id"]: t for t in lib.list_tasks(limit=1000)}
    if task_id not in tasks:
        raise HTTPException(404, "task not found")
    lib.update_task(task_id, status="queued", error=None, progress=0.0)
    return {"ok": True}


# -- videos ------------------------------------------------------------------


@router.get("/videos")
def list_videos(
    category: Optional[str] = None,
    artist_id: Optional[int] = None,
    form: Optional[str] = None,
    folder_id: Optional[int] = None,
    favorite: Optional[bool] = None,
    collection_id: Optional[int] = None,
    pending: bool = False,
    unknown_artist: bool = False,
    q: Optional[str] = None,
    limit: int = Query(60, le=500),
    offset: int = 0,
):
    lib = _lib()
    if q:
        videos = lib.search_videos(q, limit=500)

        # refine with structured filters client-side via list filter re-check
        def match(v: Dict[str, Any]) -> bool:
            full = _decorate(lib, v)
            if unknown_artist and any(p["is_attribution"] for p in full["people"]):
                return False
            if category and full.get("category") != category:
                return False
            if form and full.get("form") != form:
                return False
            if favorite is not None and bool(full.get("user_favorite")) != favorite:
                return False
            if pending and not (
                full.get("analysis_status") != "done"
                or any(p["confirmed_status"] == "pending" for p in full["people"])
            ):
                return False
            if artist_id is not None and not any(
                p["id"] == artist_id and p["is_attribution"] for p in full["people"]
            ):
                return False
            return True

        videos = [v for v in videos if match(v)]
        total = len(videos)
        videos = videos[offset:offset + limit]
    else:
        filters = dict(
            category=category,
            artist_id=artist_id,
            form=form,
            folder_id=folder_id,
            favorite=favorite,
            collection_id=collection_id,
            pending=pending,
            unknown_artist=unknown_artist,
        )
        videos = lib.list_videos(**filters, limit=limit, offset=offset)
        total = lib.count_videos(**filters)
    return {
        "total": total,
        "items": [_decorate(lib, v) for v in videos],
    }


@router.get("/videos/{content_id}")
def get_video(content_id: int):
    lib = _lib()
    return _decorate(lib, _video_or_404(lib, content_id))


@router.patch("/videos/{content_id}")
def patch_video(content_id: int, body: VideoPatch):
    lib = _lib()
    _video_or_404(lib, content_id)
    fields: Dict[str, Any] = {}
    if body.favorite is not None:
        fields["user_favorite"] = body.favorite
    for name in ("title", "summary", "category", "topic", "form"):
        value = getattr(body, name)
        if value is not None:
            fields[f"user_{name}"] = value
    if body.tags is not None:
        fields["user_tags"] = json.dumps(body.tags, ensure_ascii=False)
    if body.notes is not None:
        fields["user_notes"] = body.notes
    lib.update_user_fields(content_id, fields)
    return _decorate(lib, _video_or_404(lib, content_id))


@router.get("/videos/{content_id}/transcripts")
def get_transcripts(content_id: int):
    lib = _lib()
    _video_or_404(lib, content_id)
    return lib.get_transcripts(content_id)


@router.post("/videos/{content_id}/reanalyze")
def reanalyze(content_id: int):
    lib = _lib()
    _video_or_404(lib, content_id)
    task_id = lib.enqueue_task("analyze", {"content_id": content_id})
    lib.set_analysis_status(content_id, "pending")
    return {"task_id": task_id}


@router.post("/videos/{content_id}/reveal")
def reveal_file(content_id: int):
    lib = _lib()
    video = _video_or_404(lib, content_id)
    available = [f for f in video["files"] if f["status"] == "available"]
    if not available:
        raise HTTPException(409, "no accessible file")
    subprocess.Popen(["open", "-R", available[0]["path"]])
    return {"ok": True}


class ArtistIn(BaseModel):
    name: str


@router.post("/videos/{content_id}/artist")
def set_artist(content_id: int, body: ArtistIn):
    """Replace this video's painter (old painter link is removed)."""
    lib = _lib()
    _video_or_404(lib, content_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name must not be empty")
    person_id = lib.upsert_person("painter", name, confirmed_status="confirmed")
    lib.set_video_artist(content_id, person_id)
    return _decorate(lib, _video_or_404(lib, content_id))


@router.post("/videos/{content_id}/people/{person_id}/confirm/{role}")
def confirm_person(content_id: int, person_id: int, role: str):
    lib = _lib()
    _video_or_404(lib, content_id)
    lib.confirm_video_person(content_id, person_id, role)
    lib.update_person(person_id, confirmed_status="confirmed")
    return {"ok": True}


# -- screenshots --------------------------------------------------------------


@router.get("/screenshots/{shot_id}")
def get_screenshot(shot_id: int):
    lib = _lib()
    row = lib._conn.execute(
        "SELECT * FROM screenshots WHERE id=?", (shot_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "screenshot not found")
    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(404, "screenshot file missing")
    return FileResponse(path, media_type="image/jpeg")


# -- people ---------------------------------------------------------------------


@router.get("/people")
def list_people(kind: Optional[str] = None):
    return _lib().list_people(kind=kind)


@router.patch("/people/{person_id}")
def patch_person(person_id: int, body: PersonPatch):
    lib = _lib()
    person = lib.get_person(person_id)
    if person is None:
        raise HTTPException(404, "person not found")
    # renaming to an existing name of the same kind = merge: the person
    # disappears and all its video links move to the existing one
    if body.canonical_name and body.canonical_name != person["canonical_name"]:
        existing = next(
            (
                p
                for p in lib.list_people(kind=person["kind"])
                if p["canonical_name"] == body.canonical_name and p["id"] != person_id
            ),
            None,
        )
        if existing:
            lib.merge_people(existing["id"], person_id)
            return lib.get_person(existing["id"])
    lib.update_person(
        person_id,
        canonical_name=body.canonical_name,
        aliases=body.aliases,
        confirmed_status=body.confirmed_status,
    )
    return lib.get_person(person_id)


@router.post("/people/merge")
def merge_people(body: MergeIn):
    lib = _lib()
    if body.keep_id == body.merge_id:
        raise HTTPException(400, "cannot merge into itself")
    try:
        lib.merge_people(body.keep_id, body.merge_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


# -- collections ----------------------------------------------------------------


@router.get("/collections")
def list_collections():
    return _lib().list_collections()


@router.post("/collections")
def create_collection(body: CollectionIn):
    return {"id": _lib().create_collection(body.name)}


@router.delete("/collections/{collection_id}")
def delete_collection(collection_id: int):
    _lib().delete_collection(collection_id)
    return {"ok": True}


@router.post("/collections/{collection_id}/items")
def add_collection_item(collection_id: int, body: CollectionItemIn):
    lib = _lib()
    lib.collection_add(collection_id, body.content_id)
    return {"ok": True}


@router.delete("/collections/{collection_id}/items/{content_id}")
def remove_collection_item(collection_id: int, content_id: int):
    lib = _lib()
    lib.collection_remove(collection_id, content_id)
    return {"ok": True}


# -- batch ----------------------------------------------------------------------


@router.post("/batch")
def batch(body: BatchIn):
    lib = _lib()
    if not body.content_ids:
        raise HTTPException(400, "no content ids")
    for cid in body.content_ids:
        _video_or_404(lib, cid)
        if body.action == "artist":
            person_id = lib.upsert_person(
                "painter", str(body.value), confirmed_status="confirmed"
            )
            lib.set_video_artist(cid, person_id)
        elif body.action == "category":
            lib.update_user_fields(cid, {"user_category": str(body.value)})
        elif body.action == "tag":
            video = lib.get_video(cid)
            tags = list(video.get("user_tags") or video.get("ai_tags") or [])
            tag = str(body.value)
            if tag not in tags:
                tags.append(tag)
            lib.update_user_fields(
                cid, {"user_tags": json.dumps(tags, ensure_ascii=False)}
            )
        elif body.action == "collection":
            lib.collection_add(int(body.value), cid)
        elif body.action == "favorite":
            lib.update_user_fields(cid, {"user_favorite": bool(body.value)})
        else:
            raise HTTPException(400, f"unknown action: {body.action}")
    return {"ok": True, "updated": len(body.content_ids)}


# -- stats (sidebar counts) -------------------------------------------------------


@router.get("/stats")
def stats():
    lib = _lib()
    videos = lib.list_videos(limit=100000)
    categories: Dict[str, int] = {}
    for v in videos:
        cat = v.get("category") or "未分类"
        categories[cat] = categories.get(cat, 0) + 1
    return {
        "total": lib.count_videos(),
        "categories": categories,
        "painters": sum(p["video_count"] > 0 for p in lib.list_people(kind="painter")),
        "accounts": len(lib.list_people(kind="source_account")),
        "collections": len(lib.list_collections()),
        "pending": lib.count_videos(pending=True),
        "favorites": lib.count_videos(favorite=True),
        "unknown_artists": lib.count_videos(unknown_artist=True),
        "fts_mode": lib.fts_mode,
    }


# -- streaming ---------------------------------------------------------------------


def _file_row(lib: Library, file_id: int):
    row = lib._conn.execute(
        "SELECT f.*, c.video_codec, c.audio_codec, c.container"
        " FROM files f JOIN contents c ON c.id=f.content_id"
        " WHERE f.id=?",
        (file_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "file not found")
    if row["status"] != "available" and not Path(row["path"]).exists():
        raise HTTPException(409, "file not accessible")
    return row


@router.get("/files/{file_id}/stream")
def stream_file(file_id: int, start: float = 0.0):
    """Stream a video for browser playback.

    MP4/H.264+AAC is served with Range support; other containers/codecs are
    remuxed or transcoded to fragmented MP4 on the fly (design.md D6). The
    original file is only read.
    """
    lib = _lib()
    row = _file_row(lib, file_id)
    path = Path(row["path"])
    src_args = []
    if start > 0:
        src_args += ["-ss", f"{start:.3f}"]

    direct = (
        (row["container"] or "").startswith("mp4")
        and (row["video_codec"] or "") in REMUX_CODECS
        and (row["audio_codec"] or "aac") in REMUX_AUDIO | {None}
    )
    if direct:
        return FileResponse(path, media_type="video/mp4")

    copy_ok = (row["video_codec"] or "") in REMUX_CODECS and (
        row["audio_codec"] or ""
    ) in REMUX_AUDIO
    vcodec = (
        ["-c:v", "copy"]
        if copy_ok
        else [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
        ]
    )
    acodec = ["-c:a", "copy"] if copy_ok else ["-c:a", "aac", "-b:a", "128k"]
    cmd = [
        media.FFMPEG_BIN,
        "-loglevel",
        "error",
        *src_args,
        "-i",
        str(path),
        *vcodec,
        *acodec,
        "-movflags",
        "frag_keyframe+empty_moov",
        "-f",
        "mp4",
        "pipe:1",
    ]

    def generate():
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            while chunk := proc.stdout.read(64 * 1024):
                yield chunk
        finally:
            proc.stdout.close()
            err = proc.stderr.read().decode(errors="replace")[-500:]
            proc.stderr.close()
            if proc.wait() != 0:
                raise RuntimeError(f"ffmpeg failed: {err}")

    return StreamingResponse(
        generate(),
        media_type="video/mp4",
        headers={"Cache-Control": "no-store"},
    )
