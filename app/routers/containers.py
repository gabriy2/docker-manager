from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import actions as act
from .. import docker_service as ds
from ..auth import require_user
from ..db import get_db
from ..models import Category
from ..schemas import ActionIn

router = APIRouter(prefix="/api", tags=["container"], dependencies=[Depends(require_user)])


def category_map(db: Session) -> dict[str, list[dict]]:
    """nome container -> categorie a cui appartiene."""
    mapping: dict[str, list[dict]] = {}
    for category in db.scalars(select(Category).order_by(Category.position, Category.name)):
        brief = {"id": category.id, "name": category.name, "color": category.color, "icon": category.icon}
        for name in category.container_names:
            mapping.setdefault(name, []).append(brief)
    return mapping


def enriched_containers(db: Session) -> list[dict]:
    mapping = category_map(db)
    containers = ds.list_containers()
    for container in containers:
        container["categories"] = mapping.get(container["name"], [])
    return containers


def categories_payload(db: Session, containers: list[dict]) -> list[dict]:
    by_name = {c["name"]: c for c in containers}
    out = []
    for category in db.scalars(select(Category).order_by(Category.position, Category.name)):
        names = category.container_names
        present = [n for n in names if n in by_name]
        out.append(
            {
                "id": category.id,
                "name": category.name,
                "color": category.color,
                "icon": category.icon,
                "description": category.description,
                "position": category.position,
                "containers": names,
                "missing": [n for n in names if n not in by_name],
                "total": len(present),
                "running": sum(1 for n in present if by_name[n]["state"] == "running"),
            }
        )
    return out


def stacks_payload(containers: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for container in containers:
        key = container["stack"] or "(senza stack)"
        bucket = grouped.setdefault(
            key,
            {
                "name": key,
                "has_compose": bool(container["stack"]),
                "containers": [],
                "running": 0,
                "total": 0,
            },
        )
        bucket["containers"].append(container["name"])
        bucket["total"] += 1
        if container["state"] == "running":
            bucket["running"] += 1
    return sorted(grouped.values(), key=lambda s: (not s["has_compose"], s["name"].lower()))


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    """Payload unico usato dall'interfaccia a ogni refresh."""
    health = ds.ping()
    if not health["connected"]:
        return {
            "docker": health,
            "containers": [],
            "categories": [],
            "stacks": [],
            "counts": {"total": 0, "running": 0, "stopped": 0, "paused": 0, "unhealthy": 0},
        }
    containers = enriched_containers(db)
    counts = {
        "total": len(containers),
        "running": sum(1 for c in containers if c["state"] == "running"),
        "stopped": sum(1 for c in containers if c["state"] in {"exited", "created", "dead"}),
        "paused": sum(1 for c in containers if c["state"] == "paused"),
        "unhealthy": sum(1 for c in containers if c["health"] == "unhealthy"),
    }
    return {
        "docker": health,
        "containers": containers,
        "categories": categories_payload(db, containers),
        "stacks": stacks_payload(containers),
        "counts": counts,
    }


@router.get("/containers")
def list_containers(db: Session = Depends(get_db)) -> list[dict]:
    return enriched_containers(db)


@router.get("/containers/{ref}")
def container_detail(ref: str, db: Session = Depends(get_db)) -> dict:
    try:
        data = ds.inspect(ref)
    except (ds.ActionError, ds.DockerUnavailable) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    data["categories"] = category_map(db).get(data["name"], [])
    return data


@router.get("/containers/{ref}/logs")
def container_logs(ref: str, tail: int = Query(300, ge=10, le=5000)) -> dict:
    try:
        return {"logs": ds.logs(ref, tail=tail)}
    except (ds.ActionError, ds.DockerUnavailable) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/actions")
def run_action(payload: ActionIn, db: Session = Depends(get_db)) -> dict:
    if payload.action in {"up", "down"} and payload.target_kind != "stack":
        raise HTTPException(status_code=400, detail="le azioni up/down valgono solo per gli stack")
    result = act.execute(
        db,
        action=payload.action,
        target_kind=payload.target_kind,
        target_ref=payload.target_ref,
        targets=payload.targets,
        source="ui",
    )
    return result


@router.get("/stacks")
def list_stacks(db: Session = Depends(get_db)) -> list[dict]:
    return stacks_payload(enriched_containers(db))
