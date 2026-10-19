from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from pydantic import Field as PydField
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import actions as act
from ..auth import require_user
from ..db import get_db
from ..models import Category, CategoryMember
from ..schemas import CategoryIn, CategoryPatch
from .containers import categories_payload, enriched_containers

router = APIRouter(prefix="/api/categories", tags=["categorie"], dependencies=[Depends(require_user)])


def _payload(db: Session) -> list[dict]:
    return categories_payload(db, enriched_containers(db))


def _get(db: Session, category_id: int) -> Category:
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="categoria non trovata")
    return category


def _set_members(db: Session, category: Category, names: list[str]) -> None:
    category.members.clear()
    db.flush()
    for name in dict.fromkeys(n.strip() for n in names if n and n.strip()):
        category.members.append(CategoryMember(container_name=name))


@router.get("")
def list_categories(db: Session = Depends(get_db)) -> list[dict]:
    return _payload(db)


@router.post("", status_code=201)
def create_category(payload: CategoryIn, db: Session = Depends(get_db)) -> dict:
    category = Category(
        name=payload.name.strip(),
        color=payload.color,
        icon=payload.icon or "📦",
        description=payload.description,
        position=payload.position,
    )
    _set_members(db, category, payload.containers)
    db.add(category)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="esiste già una categoria con questo nome") from exc
    act.record_activity(
        db, source="ui", action="category.create", target=category.name, status="ok", message=""
    )
    return {"id": category.id, "categories": _payload(db)}


@router.patch("/{category_id}")
def update_category(category_id: int, payload: CategoryPatch, db: Session = Depends(get_db)) -> dict:
    category = _get(db, category_id)
    data = payload.model_dump(exclude_unset=True)
    containers = data.pop("containers", None)
    for key, value in data.items():
        if value is not None:
            setattr(category, key, value.strip() if key == "name" else value)
    if containers is not None:
        _set_members(db, category, containers)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="esiste già una categoria con questo nome") from exc
    return {"id": category.id, "categories": _payload(db)}


@router.delete("/{category_id}")
def delete_category(category_id: int, db: Session = Depends(get_db)) -> dict:
    category = _get(db, category_id)
    name = category.name
    db.delete(category)
    db.commit()
    act.record_activity(db, source="ui", action="category.delete", target=name, status="ok", message="")
    return {"categories": _payload(db)}


@router.put("/{category_id}/containers")
def set_containers(
    category_id: int,
    containers: list[str] = Body(..., embed=True),
    db: Session = Depends(get_db),
) -> dict:
    category = _get(db, category_id)
    _set_members(db, category, containers)
    db.commit()
    return {"categories": _payload(db)}


@router.post("/{category_id}/action")
def category_action(
    category_id: int,
    action: str = Body(..., embed=True),
    db: Session = Depends(get_db),
) -> dict:
    category = _get(db, category_id)
    return act.execute(
        db,
        action=action,
        target_kind="category",
        target_ref=str(category.id),
        source="ui",
        label=f"categoria {category.name}",
    )


class AssignIn(BaseModel):
    container: str
    category_ids: list[int] = PydField(default_factory=list)


@router.post("/assign")
def assign_container(payload: AssignIn, db: Session = Depends(get_db)) -> dict:
    """Imposta l'insieme completo di categorie per un singolo container."""
    name = payload.container.strip()
    if not name:
        raise HTTPException(status_code=400, detail="nome container mancante")
    wanted = set(payload.category_ids)
    for category in db.scalars(select(Category)):
        has = any(m.container_name == name for m in category.members)
        if category.id in wanted and not has:
            category.members.append(CategoryMember(container_name=name))
        elif category.id not in wanted and has:
            for member in list(category.members):
                if member.container_name == name:
                    category.members.remove(member)
    db.commit()
    return {"categories": _payload(db)}
