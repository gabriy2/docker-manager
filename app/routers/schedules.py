from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import actions as act
from ..auth import require_user
from ..config import settings
from ..db import get_db
from ..models import Schedule, as_utc, to_utc
from ..scheduler import ScheduleError, manager, preview_next_runs, tz_for, validate
from ..schemas import ScheduleIn

router = APIRouter(prefix="/api/schedules", tags=["pianificazioni"], dependencies=[Depends(require_user)])


def _normalize_run_at(run_at: datetime | None, timezone_name: str) -> datetime | None:
    """Una data senza fuso arriva dal browser come ora locale del pannello:
    la interpretiamo nel fuso della pianificazione e la salviamo in UTC."""
    if run_at is None:
        return None
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=tz_for(timezone_name))
    return to_utc(run_at)


def serialize(schedule: Schedule, db: Session | None = None) -> dict:
    targets = list(schedule.targets or [])
    return {
        "id": schedule.id,
        "name": schedule.name,
        "enabled": schedule.enabled,
        "action": schedule.action,
        "target_kind": schedule.target_kind,
        "target_ref": schedule.target_ref,
        "targets": targets,
        "target_label": act.describe_target(schedule.target_kind, schedule.target_ref, targets, db),
        "trigger_kind": schedule.trigger_kind,
        "cron_expr": schedule.cron_expr,
        "interval_minutes": schedule.interval_minutes,
        "run_at": as_utc(schedule.run_at),
        "timezone_name": schedule.timezone_name or settings.timezone_name,
        "next_run": manager.next_run(schedule.id),
        "next_runs": preview_next_runs(schedule, 3) if schedule.enabled else [],
        "last_run_at": as_utc(schedule.last_run_at),
        "last_status": schedule.last_status,
        "last_message": schedule.last_message,
        "run_count": schedule.run_count,
    }


def _all(db: Session) -> list[dict]:
    rows = db.scalars(select(Schedule).order_by(Schedule.enabled.desc(), Schedule.name)).all()
    return [serialize(s, db) for s in rows]


def _apply_payload(schedule: Schedule, payload: ScheduleIn) -> None:
    schedule.name = payload.name.strip()
    schedule.enabled = payload.enabled
    schedule.action = payload.action
    schedule.target_kind = payload.target_kind
    schedule.target_ref = payload.target_ref
    schedule.targets = list(payload.targets)
    schedule.trigger_kind = payload.trigger_kind
    schedule.cron_expr = payload.cron_expr.strip()
    schedule.interval_minutes = payload.interval_minutes
    schedule.timezone_name = payload.timezone_name or settings.timezone_name
    schedule.run_at = _normalize_run_at(payload.run_at, schedule.timezone_name)

    if payload.target_kind == "containers" and not payload.targets:
        raise HTTPException(status_code=400, detail="seleziona almeno un container")
    if payload.target_kind in {"category", "stack"} and not payload.target_ref:
        raise HTTPException(status_code=400, detail="seleziona la categoria o lo stack di destinazione")
    if payload.action in {"up", "down"} and payload.target_kind != "stack":
        raise HTTPException(status_code=400, detail="le azioni up/down valgono solo per gli stack")

    try:
        validate(schedule)
    except ScheduleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _get(db: Session, schedule_id: int) -> Schedule:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="pianificazione non trovata")
    return schedule


@router.get("")
def list_schedules(db: Session = Depends(get_db)) -> list[dict]:
    return _all(db)


@router.post("", status_code=201)
def create_schedule(payload: ScheduleIn, db: Session = Depends(get_db)) -> dict:
    schedule = Schedule()
    _apply_payload(schedule, payload)
    db.add(schedule)
    db.commit()
    manager.sync(schedule)
    act.record_activity(
        db, source="ui", action="schedule.create", target=schedule.name, status="ok", message=""
    )
    return {"id": schedule.id, "schedules": _all(db)}


@router.patch("/{schedule_id}")
def update_schedule(schedule_id: int, payload: ScheduleIn, db: Session = Depends(get_db)) -> dict:
    schedule = _get(db, schedule_id)
    _apply_payload(schedule, payload)
    db.commit()
    manager.sync(schedule)
    return {"id": schedule.id, "schedules": _all(db)}


@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)) -> dict:
    schedule = _get(db, schedule_id)
    name = schedule.name
    manager.remove(schedule.id)
    db.delete(schedule)
    db.commit()
    act.record_activity(db, source="ui", action="schedule.delete", target=name, status="ok", message="")
    return {"schedules": _all(db)}


@router.post("/{schedule_id}/toggle")
def toggle_schedule(schedule_id: int, db: Session = Depends(get_db)) -> dict:
    schedule = _get(db, schedule_id)
    schedule.enabled = not schedule.enabled
    db.commit()
    manager.sync(schedule)
    return {"schedules": _all(db)}


@router.post("/{schedule_id}/run")
def run_schedule(schedule_id: int, db: Session = Depends(get_db)) -> dict:
    schedule = _get(db, schedule_id)
    manager.run_now(schedule.id)
    db.expire_all()
    return {"schedules": _all(db)}


@router.post("/preview")
def preview(payload: ScheduleIn) -> dict:
    schedule = Schedule(
        name=payload.name or "preview",
        enabled=True,
        action=payload.action,
        target_kind=payload.target_kind,
        target_ref=payload.target_ref,
        targets=list(payload.targets),
        trigger_kind=payload.trigger_kind,
        cron_expr=payload.cron_expr.strip(),
        interval_minutes=payload.interval_minutes,
        timezone_name=payload.timezone_name or settings.timezone_name,
    )
    schedule.run_at = _normalize_run_at(payload.run_at, schedule.timezone_name)
    try:
        validate(schedule)
    except ScheduleError as exc:
        return {"valid": False, "error": str(exc), "next_runs": []}
    return {"valid": True, "error": None, "next_runs": preview_next_runs(schedule, 5)}
