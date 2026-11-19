"""Dedicated short-drama API, sharing queue and persistent document storage."""

from pathlib import Path
import shutil

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from library.agy_client import DramaSettings
from library.api import DocumentSave, DraftAdopt
from library.course_documents import CourseDocuments, DocumentConflict
from library.drama_pipeline import DramaPipeline, load, dump
from library.hashing import file_sha256
from library.config import SUPPORTED_EXTENSIONS

router = APIRouter(prefix="/api")


def pipeline() -> DramaPipeline:
    from library.app import get_state

    state = get_state()
    return DramaPipeline(state.cfg, state.client)


def record(drama_id: str):
    dramas = pipeline()
    try:
        return dramas, dramas.read(drama_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "短剧不存在") from exc


def tasks(drama_id: str) -> list[dict]:
    from library.app import get_state

    return [
        t
        for t in get_state().lib.list_tasks(limit=100000)
        if t["type"] == "drama" and t["payload"].get("drama_id") == drama_id
    ]


def store(drama_id: str) -> CourseDocuments:
    dramas, _ = record(drama_id)
    return CourseDocuments(dramas.folder(drama_id))


class DramaImport(BaseModel):
    path: str = Field(min_length=1)
    group_title: str = Field(default="")
    group_id: str = Field(default="")


class DramaBatchItem(BaseModel):
    path: str = Field(min_length=1)
    title: str = Field(default="")


class DramaBatchImport(BaseModel):
    group_title: str = Field(default="")
    group_id: str = Field(default="")
    items: list[DramaBatchItem] = Field(default_factory=list)


@router.get("/drama-settings")
def read_settings():
    return DramaSettings.load(pipeline().cfg.data_dir).model_dump()


@router.put("/drama-settings")
def save_settings(body: DramaSettings):
    body.save(pipeline().cfg.data_dir)
    return body.model_dump()


@router.get("/dramas")
def list_dramas():
    return pipeline().list_items()


@router.post("/dramas")
def import_drama(body: DramaImport):
    from library.app import get_state
    from library.config import SUPPORTED_EXTENSIONS

    source = Path(body.path).expanduser()
    group_title = body.group_title
    group_id = body.group_id
    if source.is_dir() and not group_title:
        group_title = source.name

    files = (
        sorted(
            p
            for p in source.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        if source.is_dir()
        else [source]
    )
    if not files:
        raise HTTPException(400, "文件夹中没有 MP4、MKV 或 MOV 视频")
    imported, errors = [], []
    for video in files:
        try:
            item = pipeline().create(
                video,
                group_title=group_title,
                group_id=group_id,
            )
        except ValueError as exc:
            errors.append({"path": str(video), "message": str(exc)})
            continue
        task_id = get_state().lib.enqueue_task("drama", {"drama_id": item["id"]})
        imported.append({**item, "task_id": task_id})
    if not imported:
        raise HTTPException(400, errors[0]["message"])
    return {
        **imported[0],
        "imported": [
            {k: item[k] for k in ("id", "title", "task_id")} for item in imported
        ],
        "errors": errors,
    }


@router.post("/dramas/batch")
def import_dramas_batch(body: DramaBatchImport):
    from library.app import get_state

    if not body.items:
        raise HTTPException(400, "批量列表不能为空")

    imported, errors = [], []
    for entry in body.items:
        video = Path(entry.path).expanduser()
        try:
            item = pipeline().create(
                video,
                group_title=body.group_title,
                group_id=body.group_id,
            )
        except ValueError as exc:
            errors.append({"path": str(video), "message": str(exc)})
            continue
        task_id = get_state().lib.enqueue_task("drama", {"drama_id": item["id"]})
        imported.append({**item, "task_id": task_id})

    if not imported:
        raise HTTPException(400, errors[0]["message"] if errors else "无有效视频")

    return {
        "imported": [
            {k: item[k] for k in ("id", "title", "task_id")} for item in imported
        ],
        "errors": errors,
    }


@router.get("/dramas/{drama_id}")
def read_drama(drama_id: str):
    _, item = record(drama_id)
    return {
        **item,
        "tasks": tasks(drama_id),
        "source_available": Path(item["path"]).is_file(),
    }


@router.put("/dramas/{drama_id}/source")
def relink_source(drama_id: str, body: DramaImport):
    from library.app import get_state

    with get_state().lib._lock:
        dramas, item = record(drama_id)
        if item["status"] == "running" or any(
            t["status"] in ("queued", "running", "retry") for t in tasks(drama_id)
        ):
            raise HTTPException(409, "短剧正在排队或处理中，请任务结束后重新关联")
        target = Path(body.path).expanduser().resolve()
        if not target.is_file() or target.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise HTTPException(400, "请选择可读取的 MP4、MKV 或 MOV 视频文件")
        # Older completed records store the first 20 SHA-256 characters in their cache path.
        expected = item.get("source_sha256")
        if not expected and item.get("analysis_cache"):
            expected = Path(item["analysis_cache"]).parts[1]
        try:
            if not expected:
                expected = file_sha256(item["path"])
            actual = file_sha256(target)
        except OSError as exc:
            raise HTTPException(
                400,
                "无法读取视频以核对内容，请检查文件访问权限；原路径和拆解资料已保留",
            ) from exc
        if not actual.startswith(expected):
            raise HTTPException(
                409, "文件内容与原视频不一致，未更改关联；请选回原视频的移动或改名副本"
            )
        item.update(path=str(target), source_sha256=actual)
        dump(dramas.folder(drama_id) / "drama.json", item)
    return {**item, "source_available": True, "tasks": tasks(drama_id)}


@router.post("/dramas/{drama_id}/retry")
def retry_drama(drama_id: str, regenerate: bool = False):
    from library.app import get_state

    state = get_state()
    # Same queue lock used by next_task and deletion.
    with state.lib._lock:
        dramas, item = record(drama_id)
        if any(t["status"] in ("queued", "running", "retry") for t in tasks(drama_id)):
            raise HTTPException(409, "短剧已有排队或运行中的任务")
        item.update(status="queued", stage="等待处理", progress=0, error="")
        dump(dramas.folder(drama_id) / "drama.json", item)
        return {
            "task_id": state.lib.enqueue_task(
                "drama", {"drama_id": drama_id, "regenerate": regenerate}
            )
        }


@router.delete("/dramas/{drama_id}")
def delete_drama(drama_id: str):
    from library.app import get_state

    lib = get_state().lib
    with lib._lock:
        dramas, item = record(drama_id)
        if item["status"] == "running" or any(
            t["status"] == "running" for t in tasks(drama_id)
        ):
            raise HTTPException(409, "短剧正在处理中，完成后才能删除")
        shutil.rmtree(dramas.folder(drama_id))
        lib._conn.execute(
            "DELETE FROM tasks WHERE type='drama' AND json_extract(payload_json, '$.drama_id')=?",
            (drama_id,),
        )
        lib._conn.commit()
    return {"deleted": True}


@router.get("/dramas/{drama_id}/source")
@router.get("/dramas/{drama_id}/source.mp4")
def source_video(drama_id: str):
    dramas, item = record(drama_id)
    if Path(item["path"]).is_file():
        return FileResponse(item["path"])
    local_src = dramas.folder(drama_id) / "source.mp4"
    if local_src.is_file():
        return FileResponse(local_src)
    raise HTTPException(404, "原视频不可用")


@router.get("/dramas/{drama_id}/report")
def drama_report(drama_id: str):
    from fastapi.responses import HTMLResponse

    dramas, _ = record(drama_id)
    folder = dramas.folder(drama_id)
    candidates = [
        folder / "pages" / "report.html",
        folder / "index.html",
        folder / "report.html",
        folder / "pages" / "breakdown.html",
    ]
    report_file = next((p for p in candidates if p.is_file()), None)
    if not report_file:
        raise HTTPException(404, "拆解报告尚未生成")
    content = report_file.read_text(encoding="utf-8")
    if "../source.mp4" in content or "../frames/" in content:
        base_tag = f'<base href="/api/dramas/{drama_id}/file/pages/">'
    else:
        base_tag = f'<base href="/api/dramas/{drama_id}/file/">'
    if "<head>" in content:
        content = content.replace("<head>", f"<head>{base_tag}", 1)
    else:
        content = base_tag + content
    return HTMLResponse(content)


@router.get("/dramas/{drama_id}/file/{file_path:path}")
def drama_file(drama_id: str, file_path: str):
    dramas, item = record(drama_id)
    folder = dramas.folder(drama_id).resolve()
    target = (folder / file_path).resolve()
    if not target.is_file():
        if file_path in ("source.mp4", "source") and item.get("path") and Path(item["path"]).is_file():
            return FileResponse(item["path"])
        raise HTTPException(404, "文件不存在")
    if not target.is_relative_to(folder):
        raise HTTPException(404, "文件不存在")
    return FileResponse(target)


@router.get("/dramas/{drama_id}/frames/{asset_path:path}")
def frame(drama_id: str, asset_path: str):
    dramas, _ = record(drama_id)
    folder = dramas.folder(drama_id).resolve()
    candidates = [
        (folder / "cache" / asset_path).resolve(),
        (folder / "frames" / asset_path).resolve(),
        (folder / asset_path).resolve(),
    ]
    for path in candidates:
        if (
            path.is_file()
            and path.is_relative_to(folder)
            and path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        ):
            return FileResponse(path)
    raise HTTPException(404, "截图不存在")


@router.get("/dramas/{drama_id}/shots/{shot_id}/diagnostics")
def diagnostics(drama_id: str, shot_id: str):
    dramas, item = record(drama_id)
    shot = next((s for s in item["shots"] if s["id"] == shot_id), None)
    if shot is None:
        raise HTTPException(404, "镜头不存在")
    transcript_path = dramas.folder(drama_id) / "transcript.json"
    transcript = (
        load(transcript_path)
        if transcript_path.exists()
        else {"precision": "尚未转写", "segments": []}
    )
    return {
        "precision": transcript["precision"],
        "sources": [
            {
                "title": f"镜头 {shot_id}",
                "start": shot["start"],
                "end": shot["end"],
                "evidence": "按时间顺序对照多帧；提示词为复现建议。",
            }
        ],
        "reviews": [
            {"title": "分析依据的限制", "message": text, "start": shot["start"]}
            for text in shot.get("analysis", {}).get("uncertainties", [])
        ],
        "raw": shot,
    }


@router.get("/dramas/{drama_id}/documents")
def documents(drama_id: str):
    return store(drama_id).list()


@router.get("/dramas/{drama_id}/document")
def read_document(drama_id: str, path: str, draft: bool = False):
    try:
        return store(drama_id).read(path, generated=draft)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/dramas/{drama_id}/document")
def save_document(drama_id: str, path: str, body: DocumentSave):
    try:
        return store(drama_id).save(path, body.markdown, body.revision)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except DocumentConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/dramas/{drama_id}/document/adopt")
def adopt_document(drama_id: str, path: str, body: DraftAdopt):
    try:
        return store(drama_id).adopt(path, body.revision, body.draft_revision)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except DocumentConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/dramas/{drama_id}/document-assets/{asset_path:path}")
def document_asset(drama_id: str, asset_path: str):
    root = (store(drama_id).root / "assets").resolve()
    path = (root / asset_path).resolve()
    if (
        not path.is_relative_to(root)
        or path.suffix.lower() not in (".jpg", ".png", ".webp")
        or not path.is_file()
    ):
        raise HTTPException(404, "图片不存在")
    return FileResponse(path)


@router.get("/dramas/{drama_id}/download")
def download(drama_id: str):
    dramas, item = record(drama_id)
    if any(t["status"] in ("queued", "running", "retry") for t in tasks(drama_id)):
        raise HTTPException(409, "短剧处理中，请完成后导出")
    if not (dramas.folder(drama_id) / "documents/README.md").is_file():
        raise HTTPException(404, "拆解文档尚未生成")
    return FileResponse(
        dramas.archive(drama_id),
        media_type="application/zip",
        filename="drama-breakdown.zip",
    )
