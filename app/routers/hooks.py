"""API dei webhook di backup.

Due router distinti:

* `/api/hooks/...` — gestione dall'interfaccia, protetta dalla sessione;
* `/hooks/{token}/pre|post` — endpoint pubblici chiamati dal backup tool,
  autenticati dal solo token nell'URL. Restano fuori da `/api` perché
  finiscono scritti a mano nella configurazione di ZeroByte.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import actions as act
from .. import hooks as hk
from ..auth import require_user
from ..db import get_db
from ..models import BackupHook, as_utc
from ..schemas import HookIn

log = logging.getLogger("docker-manager.hooks")

router = APIRouter(prefix="/api/hooks", tags=["webhook"], dependencies=[Depends(require_user)])
public_router = APIRouter(prefix="/hooks", tags=["webhook"])


def serialize(hook: BackupHook, db: Session | None = None) -> dict:
    targets = list(hook.targets or [])
    return {
        "id": hook.id,
        "name": hook.name,
        "enabled": hook.enabled,
        "target_kind": hook.target_kind,
        "target_ref": hook.target_ref,
        "targets": targets,
        "target_label": act.describe_target(hook.target_kind, hook.target_ref, targets, db),
        "mode": hook.mode,
        "resume_after_minutes": hook.resume_after_minutes,
        "token": hook.token,
        "pre_url": f"/hooks/{hook.token}/pre",
        "post_url": f"/hooks/{hook.token}/post",
        "suspended": list(hook.suspended or []),
        "suspended_at": as_utc(hook.suspended_at),
        "last_phase": hook.last_phase,
        "last_run_at": as_utc(hook.last_run_at),
        "last_status": hook.last_status,
        "last_message": hook.last_message,
        "run_count": hook.run_count,
    }


def _all(db: Session) -> list[dict]:
    rows = db.scalars(select(BackupHook).order_by(BackupHook.enabled.desc(), BackupHook.name)).all()
    return [serialize(h, db) for h in rows]


def _get(db: Session, hook_id: int) -> BackupHook:
    hook = db.get(BackupHook, hook_id)
    if hook is None:
        raise HTTPException(status_code=404, detail="hook non trovato")
    return hook


def _apply_payload(hook: BackupHook, payload: HookIn) -> None:
    if payload.target_kind == "containers" and not payload.targets:
        raise HTTPException(status_code=400, detail="seleziona almeno un container")
    if payload.target_kind in {"category", "stack"} and not payload.target_ref:
        raise HTTPException(status_code=400, detail="seleziona la categoria o lo stack di destinazione")
    hook.name = payload.name.strip()
    hook.enabled = payload.enabled
    hook.target_kind = payload.target_kind
    hook.target_ref = payload.target_ref
    hook.targets = list(payload.targets)
    hook.mode = payload.mode
    hook.resume_after_minutes = payload.resume_after_minutes


# --------------------------------------------------------------------------- #
# Gestione (interfaccia)                                                       #
# --------------------------------------------------------------------------- #

@router.get("")
def list_hooks(db: Session = Depends(get_db)) -> list[dict]:
    return _all(db)


@router.post("", status_code=201)
def create_hook(payload: HookIn, db: Session = Depends(get_db)) -> dict:
    hook = BackupHook(token=hk.generate_token())
    _apply_payload(hook, payload)
    db.add(hook)
    db.commit()
    act.record_activity(db, source="ui", action="hook.create", target=hook.name, status="ok", message="")
    return {"id": hook.id, "hooks": _all(db)}


@router.patch("/{hook_id}")
def update_hook(hook_id: int, payload: HookIn, db: Session = Depends(get_db)) -> dict:
    hook = _get(db, hook_id)
    _apply_payload(hook, payload)
    db.commit()
    return {"id": hook.id, "hooks": _all(db)}


@router.delete("/{hook_id}")
def delete_hook(hook_id: int, db: Session = Depends(get_db)) -> dict:
    hook = _get(db, hook_id)
    if hook.suspended:
        raise HTTPException(
            status_code=409,
            detail=(
                f"ci sono {len(hook.suspended)} container ancora sospesi: "
                "riprendili prima di eliminare l'hook"
            ),
        )
    name = hook.name
    db.delete(hook)
    db.commit()
    act.record_activity(db, source="ui", action="hook.delete", target=name, status="ok", message="")
    return {"hooks": _all(db)}


@router.post("/{hook_id}/toggle")
def toggle_hook(hook_id: int, db: Session = Depends(get_db)) -> dict:
    hook = _get(db, hook_id)
    hook.enabled = not hook.enabled
    db.commit()
    return {"hooks": _all(db)}


@router.post("/{hook_id}/rotate")
def rotate_token(hook_id: int, db: Session = Depends(get_db)) -> dict:
    """Nuovo token: i vecchi URL smettono di funzionare all'istante."""
    hook = _get(db, hook_id)
    hook.token = hk.generate_token()
    db.commit()
    act.record_activity(db, source="ui", action="hook.rotate", target=hook.name, status="ok", message="")
    return {"hooks": _all(db)}


@router.post("/{hook_id}/run/{phase}")
def run_phase(phase: str, hook_id: int, db: Session = Depends(get_db)) -> dict:
    """Esecuzione manuale dall'interfaccia, per provare l'hook o per
    riprendere a mano i container se il post non è mai arrivato."""
    if phase not in {"pre", "post"}:
        raise HTTPException(status_code=400, detail="fase non valida (ammesse: pre, post)")
    hook = _get(db, hook_id)
    result = hk.run_pre(db, hook) if phase == "pre" else hk.run_post(db, hook, note="eseguito a mano")
    return {"result": result, "hooks": _all(db)}


# --------------------------------------------------------------------------- #
# Endpoint pubblici chiamati dal backup tool                                   #
# --------------------------------------------------------------------------- #

async def _body_status(request: Request) -> str:
    """ZeroByte manda nel post un JSON con `status` (success|warning|error|
    cancelled). Lo usiamo solo per l'audit: i container vanno ripresi comunque.
    Il corpo è facoltativo e può essere personalizzato, quindi ogni errore di
    lettura viene ignorato."""
    try:
        payload = await request.json()
    except Exception:
        return ""
    return str(payload.get("status", "")) if isinstance(payload, dict) else ""


def _resolve(db: Session, token: str) -> BackupHook:
    hook = hk.find_by_token(db, token)
    if hook is None:
        raise HTTPException(status_code=404, detail="token non valido")
    if not hook.enabled:
        raise HTTPException(status_code=409, detail="hook disattivato")
    return hook


@public_router.post("/{token}/pre")
async def webhook_pre(token: str, db: Session = Depends(get_db)) -> dict:
    """Sospende i container prima del backup.

    Risponde 500 se qualcosa non si è fermato: una risposta non 2xx fa
    annullare il backup a ZeroByte, che è esattamente ciò che vogliamo
    piuttosto che copiare dati incoerenti."""
    hook = _resolve(db, token)
    result = hk.run_pre(db, hook)
    if result["status"] != "ok":
        raise HTTPException(
            status_code=500,
            detail=f"sospensione non riuscita: {act.summarize(result)}",
        )
    return {
        "phase": "pre",
        "hook": hook.name,
        "mode": hook.mode,
        "suspended": result.get("suspended", []),
        "message": act.summarize(result),
    }


@public_router.post("/{token}/post")
async def webhook_post(token: str, request: Request, db: Session = Depends(get_db)) -> dict:
    """Riavvia i container dopo il backup, a prescindere dall'esito."""
    hook = _resolve(db, token)
    backup_status = await _body_status(request)
    note = f"backup: {backup_status}" if backup_status else ""
    result = hk.run_post(db, hook, note=note)
    if result["status"] != "ok":
        raise HTTPException(
            status_code=500,
            detail=f"ripresa non riuscita: {act.summarize(result)}",
        )
    return {
        "phase": "post",
        "hook": hook.name,
        "backup_status": backup_status,
        "resumed": result.get("resumed", []),
        "message": act.summarize(result),
    }
