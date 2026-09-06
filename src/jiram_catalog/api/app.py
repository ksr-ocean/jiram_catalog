"""The application factory: routers, the job pool, and the built front end.

One process serves both halves of GUI v2 -- the JSON/Arrow/PNG API under
``/api`` and the compiled single-page front end at ``/`` -- so that there
is one port to tunnel and no cross-origin story to get wrong.  The state
every route needs (the mirror root and the job manager) hangs off
``app.state``, which is what makes :func:`create_app` a factory a test
can call with a synthetic mirror instead of a module that reads the
environment at import time.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from ..config import mirror_root, paper_data_root
from ..webapp import dist_dir
from . import catalog, data, selections, stacks, strips
from .jobs import JobManager

LOGGER = logging.getLogger(__name__)

TITLE = "JIRAM catalog"

#: What the static handler says when nobody has run ``npm run build``.
NO_BUNDLE = (
    "The front-end bundle is missing.\n"
    "Build it with:  cd frontend && npm install && npm run build\n"
    "The API itself is up: try /api/health or /api/config.\n"
)

meta_router = APIRouter(prefix="/api", tags=["meta"])


def version() -> str:
    """The installed package version, or ``unknown`` from a bare checkout."""
    from importlib.metadata import PackageNotFoundError, version as installed

    try:
        return installed("jiram-catalog")
    except PackageNotFoundError:  # pragma: no cover - only outside an install
        return "unknown"


@meta_router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@meta_router.get("/config")
def config(request: Request) -> dict[str, Any]:
    """What this server is pointed at, and how much of it there is.

    The counts are the four numbers a user needs before deciding the
    server is pointed at the right mirror; each is cheap because the
    tables behind them are the loaders' cached ones.
    """
    mirror = request.app.state.mirror
    counts = {
        "frames_on_planet": _count(lambda: len(catalog.catalog_frame(mirror))),
        "strips": _count(lambda: len(data.strips_table(mirror))),
        "stacks": _count(lambda: len(stacks.stack_index(mirror))),
        "selections": _count(lambda: len(selections.load_all(mirror))),
    }
    return {
        "mirror": str(mirror),
        "paper_data": str(paper_data_root()),
        "version": version(),
        "counts": counts,
        "has_trackability": bool(_safe(lambda: data.has_trackability(mirror), False)),
    }


@meta_router.get("/jobs")
def list_jobs(request: Request) -> list[dict[str, Any]]:
    return request.app.state.jobs.list()


@meta_router.get("/jobs/{identifier}")
def get_job(request: Request, identifier: str) -> dict[str, Any]:
    try:
        return request.app.state.jobs.get(identifier)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown job: {identifier}") from exc


@meta_router.delete("/jobs/{identifier}")
def cancel_job(request: Request, identifier: str) -> dict[str, Any]:
    try:
        return request.app.state.jobs.cancel(identifier)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown job: {identifier}") from exc


def _safe(call: Any, default: Any) -> Any:
    """Run ``call``; log and fall back rather than 500 the config page.

    ``/api/config`` is what a user opens when something is wrong, so a
    missing strip index has to show as ``strips: 0`` rather than as a
    stack trace where the diagnosis should be.
    """
    try:
        return call()
    except Exception as exc:  # noqa: BLE001 - a diagnostic page must not raise
        LOGGER.warning("config: %s", exc)
        return default


def _count(call: Any) -> int:
    return int(_safe(call, 0))


# ---------------------------------------------------------------------------
# the factory
# ---------------------------------------------------------------------------
def create_app(mirror: str | Path | None = None) -> FastAPI:
    """The application, bound to one mirror root for its lifetime."""
    root = mirror_root(mirror)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        LOGGER.info("serving mirror %s", root)
        yield
        app.state.jobs.shutdown()

    app = FastAPI(title=TITLE, version=version(), lifespan=lifespan)
    app.state.mirror = root
    app.state.jobs = JobManager(data.gui_cache_dir(root) / "jobs")
    stacks.register_jobs(app.state.jobs, root)

    app.include_router(meta_router)
    app.include_router(catalog.router)
    app.include_router(selections.router)
    app.include_router(stacks.router)
    app.include_router(strips.router)

    _add_static(app)
    return app


def _add_static(app: FastAPI) -> None:
    """Serve the built bundle, with ``index.html`` as the client-route fallback.

    Registered last, so every ``/api`` route above wins; an unmatched
    ``/api`` path is answered as 404 rather than silently handed the
    single-page shell, which would otherwise turn a typo in a fetch URL
    into an HTML body the front end cannot parse.
    """

    @app.get("/{resource:path}", include_in_schema=False)
    def spa(resource: str) -> Any:
        if resource.startswith("api/") or resource == "api":
            raise HTTPException(status_code=404, detail=f"no such endpoint: /{resource}")
        dist = dist_dir()
        index = dist / "index.html"
        if resource:
            candidate = (dist / resource).resolve()
            try:
                inside = candidate.is_relative_to(dist.resolve())
            except (OSError, ValueError):
                inside = False
            if inside and candidate.is_file():
                return FileResponse(candidate)
        if index.is_file():
            return FileResponse(index, media_type="text/html")
        return PlainTextResponse(NO_BUNDLE, status_code=503)
