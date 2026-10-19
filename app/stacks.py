"""Registro degli stack compose.

`docker compose down` rimuove i container e con essi le label
`com.docker.compose.project.*` da cui si ricava dove sta il file compose.
Senza memoria, uno stack spento dal pannello non sarebbe più ricreabile e
spariterebbe dall'interfaccia: qui teniamo traccia dei progetti visti.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ComposeProject, utcnow


def remember(db: Session, containers: list[dict]) -> None:
    """Aggiorna il registro con i progetti compose presenti tra i container."""
    seen: dict[str, tuple[str, list[str]]] = {}
    for container in containers:
        project = container.get("stack")
        if not project:
            continue
        workdir = container.get("compose_workdir") or ""
        files = list(container.get("compose_files") or [])
        known = seen.get(project)
        # preferiamo il container che porta un working_dir valorizzato
        if known is None or (workdir and not known[0]):
            seen[project] = (workdir, files)
    if not seen:
        return

    existing = {
        row.name: row
        for row in db.scalars(select(ComposeProject).where(ComposeProject.name.in_(seen)))
    }
    for name, (workdir, files) in seen.items():
        row = existing.get(name)
        if row is None:
            db.add(ComposeProject(name=name, working_dir=workdir, config_files=files))
            continue
        if workdir and row.working_dir != workdir:
            row.working_dir = workdir
        if files and list(row.config_files or []) != files:
            row.config_files = files
        row.last_seen = utcnow()
    db.commit()


def known(db: Session) -> list[ComposeProject]:
    return list(db.scalars(select(ComposeProject).order_by(ComposeProject.name)))


def context(db: Session, project: str) -> tuple[str, list[str]] | None:
    """(working_dir, config_files) memorizzati per lo stack, se noti."""
    row = db.scalar(select(ComposeProject).where(ComposeProject.name == project))
    if row is None or not row.working_dir:
        return None
    return row.working_dir, list(row.config_files or [])


def forget(db: Session, project: str) -> None:
    row = db.scalar(select(ComposeProject).where(ComposeProject.name == project))
    if row is not None:
        db.delete(row)
        db.commit()
