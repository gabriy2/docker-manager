from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import docker_service as ds
from .config import settings
from .db import init_db
from .routers import categories, containers, hooks, schedules, system
from .scheduler import manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("docker-manager")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    log.info("database: %s", settings.db_path)
    log.info("timezone: %s", settings.timezone_name)
    log.info("autenticazione: %s", "attiva" if settings.auth_enabled else "disattivata")
    health = ds.ping()
    if health["connected"]:
        log.info("Docker %s su %s", health["docker_version"], health["host"])
    else:
        log.warning("Docker non raggiungibile: %s", health.get("error"))
    ds.stats_collector.start()
    manager.start()
    try:
        yield
    finally:
        manager.shutdown()
        ds.stats_collector.stop()
        ds.reset_client()


app = FastAPI(
    title=settings.app_name,
    version=system.APP_VERSION,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.include_router(system.router)
app.include_router(containers.router)
app.include_router(categories.router)
app.include_router(schedules.router)
app.include_router(hooks.router)
app.include_router(hooks.public_router)


@app.exception_handler(ds.DockerUnavailable)
async def docker_unavailable_handler(_request: Request, exc: ds.DockerUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": f"Docker non raggiungibile: {exc}"})


@app.exception_handler(ds.ActionError)
async def action_error_handler(_request: Request, exc: ds.ActionError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/favicon.svg", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.svg")
