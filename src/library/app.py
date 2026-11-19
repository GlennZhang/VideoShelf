"""FastAPI application factory and lifecycle management."""

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.staticfiles import StaticFiles

from library.api import router
from library.drama_api import router as drama_router
from library.summary_api import router as summary_router
from library.download_api import router as download_router
from library.config import LibraryConfig
from library.db import Library
from library.omlx_client import OmlxClient
from library.pipeline import AnalysisPipeline
from library.worker import Worker
from library.model_settings import ModelSettings

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class AppState:
    """Shared state: one library, one worker, one oMLX client."""

    def __init__(self, cfg: LibraryConfig):
        self.cfg = cfg
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.lib = Library(cfg.db_path)
        self.client = OmlxClient(
            cfg.omlx.base_url,
            cfg.omlx.effective_api_key,
            timeout_s=cfg.omlx.timeout_s,
            max_retries=cfg.omlx.max_retries,
        )
        self.pipeline = AnalysisPipeline(self.lib, cfg, self.client)
        self.model_settings = ModelSettings(cfg)
        self.worker = Worker(
            self.lib,
            cfg,
            self.pipeline,
            model_factory=lambda: self.model_settings.runtime(self.client),
        )

    def start(self) -> None:
        self.worker.start()
        logger.info("library ready: %s (fts=%s)", self.cfg.db_path, self.lib.fts_mode)

    def shutdown(self) -> None:
        self.worker.stop()
        self.lib.close()
        self.client.close()


_STATE: Optional[AppState] = None


def get_state() -> AppState:
    if _STATE is None:
        raise RuntimeError("app state not initialized")
    return _STATE


def create_app(cfg: LibraryConfig) -> FastAPI:
    global _STATE
    _STATE = AppState(cfg)

    app = FastAPI(title="VideoShelf · 本地视频资料库", docs_url=None, redoc_url=None)
    app.include_router(router)
    app.include_router(drama_router)
    app.include_router(summary_router)
    app.include_router(download_router)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        if request.url.path.startswith("/api/model-settings"):
            return JSONResponse(
                status_code=422,
                content={
                    "detail": [
                        {key: error[key] for key in ("loc", "msg", "type")}
                        for error in exc.errors()
                    ]
                },
            )
        return await request_validation_exception_handler(request, exc)

    @app.on_event("startup")
    async def _startup() -> None:
        _STATE.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        _STATE.shutdown()

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/")
        async def index():
            return FileResponse(STATIC_DIR / "index.html")

    return app
