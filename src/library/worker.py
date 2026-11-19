"""Background worker: consumes the task queue sequentially (design.md D1).

One thread, one task at a time — analysis is intentionally sequential per
spec §2. Scan tasks run before analyze tasks (see Library.next_task).
"""

import logging
import threading
from typing import Optional

from library.config import LibraryConfig
from library.db import Library
from library.pipeline import AnalysisPipeline
from library.scanner import scan_folder

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 1.0


class Worker:
    """Sequential task-queue worker thread."""

    def __init__(
        self,
        lib: Library,
        cfg: LibraryConfig,
        pipeline: AnalysisPipeline,
        model_factory=None,
    ):
        self.lib = lib
        self.cfg = cfg
        self.pipeline = pipeline
        self.model_factory = model_factory
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.lib.requeue_stale_running()
        self._thread = threading.Thread(
            target=self._run, name="library-worker", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        logger.info("worker started")
        while not self._stop.is_set():
            task = self.lib.next_task()
            if task is None:
                self._stop.wait(POLL_INTERVAL_S)
                continue
            try:
                self._handle(task)
            except Exception as exc:  # noqa: BLE001 — worker must survive
                logger.exception("task %s failed", task["id"])
                self.lib.update_task(task["id"], status="failed", error=str(exc)[:1000])
                if task["type"] == "analyze":
                    self.lib.set_analysis_status(
                        task["payload"]["content_id"], "failed", str(exc)
                    )
        logger.info("worker stopped")

    def _handle(self, task: dict) -> None:
        if task["type"] == "scan":
            folder = task["payload"]
            result = scan_folder(
                self.lib,
                folder["folder_id"],
                folder["path"],
                self.cfg,
                enqueue_analysis=True,
            )
            if result.errors:
                self.lib.update_task(
                    task["id"],
                    status="failed",
                    error="; ".join(result.errors)[:1000],
                )
            else:
                self.lib.update_task(task["id"], status="done", progress=1.0)
        elif task["type"] == "analyze":
            content_id = task["payload"]["content_id"]
            if self.model_factory is None:
                self.pipeline.analyze_content(content_id, task_id=task["id"])
            else:
                with self.model_factory() as (cfg, client):
                    AnalysisPipeline(self.lib, cfg, client).analyze_content(
                        content_id, task_id=task["id"]
                    )
            self.lib.update_task(task["id"], status="done")
        elif task["type"] == "course":
            from library.course_pipeline import CoursePipeline

            course = CoursePipeline(
                self.cfg, self.pipeline.client, model_factory=self.model_factory
            )
            course.run(
                task["payload"]["course_id"],
                lambda stage, fraction: self.lib.update_task(
                    task["id"], stage=stage, progress=fraction
                ),
                retry_only=not task["payload"].get("regenerate", False),
                regenerate=task["payload"].get("regenerate", False),
            )
            self.lib.update_task(task["id"], status="done", progress=1.0)
        elif task["type"] == "drama":
            from library.drama_pipeline import DramaPipeline

            DramaPipeline(self.cfg, self.pipeline.client, self.model_factory).run(
                task["payload"]["drama_id"],
                lambda stage, fraction: self.lib.update_task(
                    task["id"], stage=stage, progress=fraction
                ),
                regenerate=task["payload"].get("regenerate", False),
            )
            self.lib.update_task(task["id"], status="done", progress=1.0)
        elif task["type"] == "summary":
            from library.summary_pipeline import SummaryPipeline

            SummaryPipeline(
                self.cfg, self.pipeline.client, self.model_factory
            ).run(
                task["payload"]["summary_id"],
                lambda stage, fraction: self.lib.update_task(
                    task["id"], stage=stage, progress=fraction
                ),
            )
            self.lib.update_task(task["id"], status="done", progress=1.0)
        elif task["type"] == "download":
            from library.download_pipeline import DownloadPipeline

            DownloadPipeline(self.cfg).run(
                task["payload"]["download_id"],
                lambda stage, fraction: self.lib.update_task(
                    task["id"], stage=stage, progress=fraction
                ),
            )
            self.lib.update_task(task["id"], status="done", progress=1.0)
        else:
            raise ValueError(f"unknown task type: {task['type']}")
