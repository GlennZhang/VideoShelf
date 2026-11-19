"""Bilibili download API: resolve, create, list, retry, delete, settings."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from library.bili_client import BiliError, resolve
from library.download_pipeline import DownloadPipeline
from library.download_settings import DownloadSettings

router = APIRouter(prefix="/api")


def pipeline() -> DownloadPipeline:
    from library.app import get_state

    return DownloadPipeline(get_state().cfg)


def record(download_id: str):
    downloads = pipeline()
    try:
        return downloads, downloads.read(download_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "下载任务不存在") from exc


def tasks(download_id: str) -> list[dict]:
    from library.app import get_state

    return [
        t
        for t in get_state().lib.list_tasks(limit=100000)
        if t["type"] == "download"
        and t["payload"].get("download_id") == download_id
    ]


class ResolveRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


class CreateRequest(BaseModel):
    items: list[dict] = Field(min_length=1, max_length=1000)
    target_dir: str = Field(min_length=1, max_length=2000)
    title: str = Field(default="", max_length=500)


@router.get("/download-settings")
def read_settings():
    return DownloadSettings.load(pipeline().cfg.data_dir).model_dump()


@router.put("/download-settings")
def save_settings(body: DownloadSettings):
    body.save(pipeline().cfg.data_dir)
    return body.model_dump()


@router.post("/downloads/resolve")
def resolve_link(body: ResolveRequest):
    try:
        return resolve(body.url)
    except BiliError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/downloads")
def list_downloads():
    return pipeline().list_items()


@router.post("/downloads")
def create_download(body: CreateRequest):
    from library.app import get_state

    try:
        item = pipeline().create(body.items, body.target_dir, body.title)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    task_id = get_state().lib.enqueue_task(
        "download", {"download_id": item["id"]}
    )
    return {**item, "task_id": task_id}


@router.get("/downloads/{download_id}")
def read_download(download_id: str):
    _, item = record(download_id)
    return {**item, "tasks": tasks(download_id)}


@router.post("/downloads/{download_id}/retry")
def retry_download(download_id: str):
    from library.app import get_state

    state = get_state()
    with state.lib._lock:
        downloads, item = record(download_id)
        if any(
            t["status"] in ("queued", "running", "retry")
            for t in tasks(download_id)
        ):
            raise HTTPException(409, "下载任务已有排队或运行中的任务")
        downloads.retry(download_id)
        return {
            "task_id": state.lib.enqueue_task(
                "download", {"download_id": download_id}
            )
        }


@router.delete("/downloads/{download_id}")
def delete_download(download_id: str):
    from library.app import get_state

    lib = get_state().lib
    with lib._lock:
        downloads, item = record(download_id)
        if item["status"] == "running" or any(
            t["status"] == "running" for t in tasks(download_id)
        ):
            raise HTTPException(409, "下载正在处理中，完成后才能删除")
        downloads.delete(download_id)
        lib._conn.execute(
            "DELETE FROM tasks WHERE type='download' AND json_extract(payload_json, '$.download_id')=?",
            (download_id,),
        )
        lib._conn.commit()
    return {"deleted": True}


@router.post("/downloads/qr-login/start")
def qr_login_start():
    from library.bili_client import BiliError, QrLogin

    try:
        started = QrLogin().start()
    except BiliError as exc:
        raise HTTPException(502, str(exc)) from exc
    return started


@router.get("/downloads/qr-login/poll")
def qr_login_poll(key: str):
    from library.bili_client import BiliError, QrLogin

    try:
        result = QrLogin().poll(key)
    except BiliError as exc:
        raise HTTPException(502, str(exc)) from exc
    if result["status"] == "success":
        settings = DownloadSettings.load(pipeline().cfg.data_dir)
        settings.sessdata = result["sessdata"]
        settings.cookie_source = "manual"
        settings.save(pipeline().cfg.data_dir)
        return {"status": "success"}
    return result
