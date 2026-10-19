"""Risoluzione dei target (container / categoria / stack / tutti) ed esecuzione
delle azioni, condivisa tra API web e scheduler."""

from __future__ import annotations

import contextlib
import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import docker_service as ds
from .config import settings
from .models import ActivityLog, Category

log = logging.getLogger("docker-manager.actions")

TARGET_KINDS = {"containers", "category", "stack", "all"}


class TargetError(RuntimeError):
    pass


def describe_target(
    target_kind: str,
    target_ref: str,
    targets: list[str],
    db: Session | None = None,
) -> str:
    if target_kind == "all":
        return "tutti i container"
    if target_kind == "category":
        name = target_ref
        if db is not None:
            with contextlib.suppress(TargetError):
                name = _find_category(db, target_ref).name
        return f"categoria {name}"
    if target_kind == "stack":
        return f"stack {target_ref}"
    if len(targets) == 1:
        return targets[0]
    return f"{len(targets)} container"


def resolve_targets(db: Session, target_kind: str, target_ref: str, targets: list[str]) -> list[str]:
    """Ritorna i nomi dei container su cui agire, filtrati su quelli esistenti."""
    if target_kind not in TARGET_KINDS:
        raise TargetError(f"tipo di target '{target_kind}' non valido")

    existing = {c["name"]: c for c in ds.list_containers()}

    if target_kind == "all":
        names = list(existing)
    elif target_kind == "containers":
        names = [n for n in (targets or []) if n in existing]
    elif target_kind == "category":
        category = _find_category(db, target_ref)
        names = [n for n in category.container_names if n in existing]
    else:  # stack
        names = [n for n, c in existing.items() if (c["stack"] or "(senza stack)") == target_ref]

    # non ci spegniamo mai da soli durante un'azione di massa
    if target_kind != "containers":
        names = [n for n in names if not existing[n]["is_self"]]
    return names


def _find_category(db: Session, ref: str) -> Category:
    category = None
    if str(ref).isdigit():
        category = db.get(Category, int(ref))
    if category is None:
        category = db.scalar(select(Category).where(Category.name == str(ref)))
    if category is None:
        raise TargetError(f"categoria '{ref}' non trovata")
    return category


def execute(
    db: Session,
    *,
    action: str,
    target_kind: str = "containers",
    target_ref: str = "",
    targets: list[str] | None = None,
    source: str = "ui",
    label: str | None = None,
) -> dict:
    """Esegue l'azione e registra l'esito nell'audit log."""
    targets = list(targets or [])
    description = label or describe_target(target_kind, target_ref, targets, db)

    try:
        if target_kind == "stack" and action in {"up", "down"}:
            result = ds.stack_action(target_ref, action)
            result["targets"] = [target_ref]
        else:
            names = resolve_targets(db, target_kind, target_ref, targets)
            if not names:
                result = {"status": "error", "results": [], "message": "nessun container corrispondente"}
            else:
                result = ds.bulk_action(names, action)
                result["targets"] = names
    except (ds.ActionError, ds.DockerUnavailable, TargetError) as exc:
        result = {"status": "error", "results": [], "message": str(exc), "targets": targets}

    record_activity(
        db,
        source=source,
        action=action,
        target=description,
        status=result["status"],
        message=summarize(result),
    )
    return result


def summarize(result: dict) -> str:
    if result.get("message"):
        return str(result["message"])
    rows = result.get("results") or []
    ok = sum(1 for r in rows if r.get("ok"))
    errors = [f"{r['target']}: {r['message']}" for r in rows if not r.get("ok")]
    parts = [f"{ok}/{len(rows)} ok"]
    if errors:
        parts.append("; ".join(errors[:5]))
    return " — ".join(parts)


def record_activity(
    db: Session,
    *,
    source: str,
    action: str,
    target: str,
    status: str,
    message: str = "",
) -> None:
    db.add(
        ActivityLog(source=source, action=action, target=target, status=status, message=message[:2000])
    )
    db.commit()
    _trim_log(db)


def _trim_log(db: Session) -> None:
    cutoff = db.scalars(
        select(ActivityLog.id).order_by(ActivityLog.id.desc()).offset(settings.log_retention).limit(1)
    ).first()
    if cutoff:
        db.execute(delete(ActivityLog).where(ActivityLog.id <= cutoff))
        db.commit()
