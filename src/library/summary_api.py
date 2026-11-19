"""Single-video summary API: queue, detail, downloads, retry and delete."""

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from library.summary_pipeline import SummaryPipeline, dump

router = APIRouter(prefix="/api")


def pipeline() -> SummaryPipeline:
    from library.app import get_state

    state = get_state()
    return SummaryPipeline(state.cfg, state.client)


def record(summary_id: str):
    summaries = pipeline()
    try:
        return summaries, summaries.read(summary_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "总结不存在") from exc


def tasks(summary_id: str) -> list[dict]:
    from library.app import get_state

    return [
        t
        for t in get_state().lib.list_tasks(limit=100000)
        if t["type"] == "summary" and t["payload"].get("summary_id") == summary_id
    ]


class SummaryCreate(BaseModel):
    path: str = Field(min_length=1)
    include_frames: bool = True


@router.get("/summaries")
def list_summaries():
    return pipeline().list_items()


@router.post("/summaries")
def create_summary(body: SummaryCreate):
    from library.app import get_state

    try:
        item = pipeline().create(Path(body.path), body.include_frames)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    task_id = get_state().lib.enqueue_task("summary", {"summary_id": item["id"]})
    return {**item, "task_id": task_id}


@router.get("/summaries/{summary_id}")
def read_summary(summary_id: str):
    summaries, item = record(summary_id)
    folder = summaries.folder(summary_id)

    def text_of(name: str) -> str:
        path = folder / name
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    return {
        **item,
        "tasks": tasks(summary_id),
        "source_available": Path(item["path"]).is_file(),
        "notes": text_of("notes.md"),
        "transcript_polished": text_of("transcript-polished.md"),
        "frames": sorted(p.name for p in (folder / "frames").glob("*.jpg"))
        if (folder / "frames").is_dir()
        else [],
    }


@router.get("/summaries/{summary_id}/frames/{file_name}")
def frame(summary_id: str, file_name: str):
    summaries, _ = record(summary_id)
    root = (summaries.folder(summary_id) / "frames").resolve()
    path = (root / file_name).resolve()
    if (
        not path.is_relative_to(root)
        or path.suffix.lower() != ".jpg"
        or not path.is_file()
    ):
        raise HTTPException(404, "截图不存在")
    return FileResponse(path)


@router.get("/summaries/{summary_id}/download")
def download(summary_id: str, file: Literal["notes", "transcript"] = "notes"):
    summaries, item = record(summary_id)
    name = "notes.md" if file == "notes" else "transcript-polished.md"
    path = summaries.folder(summary_id) / name
    if not path.is_file():
        raise HTTPException(404, "文件尚未生成")
    return FileResponse(
        path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{item['title']}-{file}.md",
    )


@router.post("/summaries/{summary_id}/retry")
def retry_summary(summary_id: str):
    from library.app import get_state

    state = get_state()
    with state.lib._lock:
        summaries, item = record(summary_id)
        if any(
            t["status"] in ("queued", "running", "retry") for t in tasks(summary_id)
        ):
            raise HTTPException(409, "总结已有排队或运行中的任务")
        item.update(status="queued", stage="等待处理", progress=0, error="")
        dump(summaries.folder(summary_id) / "summary.json", item)
        return {
            "task_id": state.lib.enqueue_task("summary", {"summary_id": summary_id})
        }


@router.delete("/summaries/{summary_id}")
def delete_summary(summary_id: str):
    from library.app import get_state

    lib = get_state().lib
    with lib._lock:
        summaries, item = record(summary_id)
        if item["status"] == "running" or any(
            t["status"] == "running" for t in tasks(summary_id)
        ):
            raise HTTPException(409, "总结正在处理中，完成后才能删除")
        summaries.delete(summary_id)
        lib._conn.execute(
            "DELETE FROM tasks WHERE type='summary' AND json_extract(payload_json, '$.summary_id')=?",
            (summary_id,),
        )
        lib._conn.commit()
    return {"deleted": True}
