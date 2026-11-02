"""The FastAPI application: routes under /api, the built web UI at /, background workers in the same process."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import Depends, FastAPI, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .. import __version__
from ..config import Settings, get_settings
from ..db.engine import init_db, make_engine, make_session_factory
from .deps import require_auth
from .embedded import EmbeddedWorkers
from .errors import ApiError, install_error_handlers
from .routes import admin, documents, records, search
from .security import RateLimiter, SecurityMiddleware

log = logging.getLogger("docintel.api")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False, description="Only needed when the server was started with DOCINTEL_API_KEY.")


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        t0 = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - t0) * 1000
        response.headers["X-Request-ID"] = rid
        if response.status_code >= 500 or ms > 2000:
            log.warning("%s %s -> %s in %.0f ms [%s]", request.method, request.url.path, response.status_code, ms, rid)
        else:
            log.debug("%s %s -> %s in %.0f ms [%s]", request.method, request.url.path, response.status_code, ms, rid)
        return response


def create_app(settings: Settings | None = None, *, start_workers: bool | None = None) -> FastAPI:
    """Build the app. `start_workers=False` is for tests that drive a worker by hand."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    engine = make_engine(settings.db_url)
    init_db(engine)
    factory = make_session_factory(engine)
    run_workers = (settings.workers > 0) if start_workers is None else start_workers

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        workers = None
        if run_workers:
            workers = EmbeddedWorkers(settings, factory)
            await anyio.to_thread.run_sync(workers.start)
            app.state.workers = workers
        log.info("DocIntel %s ready (database: %s, workers: %d)", __version__, "sqlite" if settings.is_sqlite else "other", settings.workers if run_workers else 0)
        try:
            yield
        finally:
            if workers is not None:
                await anyio.to_thread.run_sync(workers.stop)
            engine.dispose()

    app = FastAPI(
        title="DocIntel API",
        version=__version__,
        summary="Upload invoices, receipts and spreadsheets; get validated structured data back; search it; correct it.",
        description=(
            "Upload PDF, JPG, PNG, TIFF, XLSX, XLS or CSV files to `POST /api/documents`. They are processed in the background "
            "(text layer or OCR, extraction, validation). Poll `GET /api/documents/{id}` for progress, read results from "
            "`GET /api/records/{id}`, correct them with `PATCH /api/records/{id}`, search with `GET /api/search`."
        ),
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings, app.state.engine, app.state.factory = settings, engine, factory
    app.state.workers = None
    app.state.limiter = RateLimiter(settings.rate_limit_per_minute)
    install_error_handlers(app)

    app.add_middleware(SecurityMiddleware, settings=settings, limiter=app.state.limiter)
    if settings.cors_origin_list:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["X-API-Key", "Content-Type", "Authorization"], max_age=600)
    app.add_middleware(RequestLogMiddleware)

    app.include_router(admin.public, prefix="/api")
    protected = [Depends(require_auth), Security(_api_key_scheme)]
    for r in (documents.router, records.router, search.router, admin.router):
        app.include_router(r, prefix="/api", dependencies=protected)

    _mount_ui(app)
    return app


def _mount_ui(app: FastAPI) -> None:
    root = STATIC_DIR.resolve()
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def ui(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise ApiError(404, "NOT_FOUND", "No such endpoint. See /api/docs.")
        if full_path:
            candidate = (root / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
        index = root / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"message": "The web interface has not been built. The API is available at /api/docs.", "docs": "/api/docs"}, status_code=200)
