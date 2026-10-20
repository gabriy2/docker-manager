from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import auth
from .. import docker_service as ds
from ..config import settings
from ..db import get_db
from ..models import ActivityLog, as_utc
from ..schemas import ALL_ACTIONS, CONTAINER_ONLY_ACTIONS, LoginIn

router = APIRouter(prefix="/api", tags=["sistema"])

APP_VERSION = "1.0.0"


@router.get("/session")
def session_info(request: Request) -> dict:
    user = auth.current_user(request)
    return {
        "auth_enabled": settings.auth_enabled,
        "authenticated": user is not None,
        "user": user,
        "app_name": settings.app_name,
    }


@router.post("/auth/login")
def login(payload: LoginIn, request: Request, response: Response) -> dict:
    if not settings.auth_enabled:
        return {"authenticated": True, "user": "anonimo"}
    if not auth.verify_credentials(payload.username, payload.password):
        raise HTTPException(status_code=401, detail="credenziali non valide")
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    auth.issue_session(response, payload.username, secure=secure)
    return {"authenticated": True, "user": payload.username}


@router.post("/auth/logout")
def logout(response: Response) -> dict:
    auth.clear_session(response)
    return {"authenticated": False}


def _internal_base_url() -> str:
    """Indirizzo con cui raggiungere il pannello dall'interno della rete docker.

    È quello che serve a un backup tool affiancato al pannello: l'interfaccia
    da sola conosce solo l'URL con cui la stai navigando, che dietro a un
    reverse proxy è quello pubblico."""
    if settings.hook_base_url:
        return settings.hook_base_url
    name = ds.self_name()
    return f"http://{name}:{settings.port}" if name else ""


@router.get("/meta", dependencies=[Depends(auth.require_user)])
def meta() -> dict:
    return {
        "app_name": settings.app_name,
        "internal_base_url": _internal_base_url(),
        "hook_base_url_fixed": bool(settings.hook_base_url),
        "version": APP_VERSION,
        "timezone": settings.timezone_name,
        "actions": ALL_ACTIONS,
        "container_actions": CONTAINER_ONLY_ACTIONS,
        "compose_cli": ds.compose_cli_available(),
        "stats_enabled": settings.enable_stats,
        "stop_timeout": settings.stop_timeout,
        "auth_enabled": settings.auth_enabled,
    }


@router.get("/activity", dependencies=[Depends(auth.require_user)])
def activity(limit: int = Query(80, ge=1, le=500), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ActivityLog).order_by(ActivityLog.id.desc()).limit(limit)).all()
    return [
        {
            "id": r.id,
            "created_at": as_utc(r.created_at),
            "source": r.source,
            "action": r.action,
            "target": r.target,
            "status": r.status,
            "message": r.message,
        }
        for r in rows
    ]


@router.delete("/activity", dependencies=[Depends(auth.require_user)])
def clear_activity(db: Session = Depends(get_db)) -> dict:
    db.execute(delete(ActivityLog))
    db.commit()
    return {"cleared": True}


@router.get("/health")
def health() -> dict:
    status = ds.ping()
    return {"ok": status["connected"], "docker": status, "version": APP_VERSION}
