from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite non conserva il fuso orario: per convenzione in tutte le colonne
    DateTime salviamo UTC, quindi in lettura ci limitiamo a riattaccarlo."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def to_utc(value: datetime | None) -> datetime | None:
    """Normalizza un valore in arrivo (naive = già UTC) prima di salvarlo."""
    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    color: Mapped[str] = mapped_column(String(16), default="#6366f1")
    icon: Mapped[str] = mapped_column(String(8), default="📦")
    description: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list[CategoryMember]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def container_names(self) -> list[str]:
        return sorted(m.container_name for m in self.members)


class CategoryMember(Base):
    """Associazione categoria <-> container, per *nome* così da sopravvivere
    alla ricreazione del container (l'ID cambia, il nome no)."""

    __tablename__ = "category_members"
    __table_args__ = (UniqueConstraint("category_id", "container_name", name="uq_cat_container"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    container_name: Mapped[str] = mapped_column(String(255), index=True)

    category: Mapped[Category] = relationship(back_populates="members")


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # cosa fare
    action: Mapped[str] = mapped_column(String(16))  # start|stop|restart|pause|unpause|up|down

    # su cosa
    target_kind: Mapped[str] = mapped_column(String(16))  # containers|category|stack|all
    target_ref: Mapped[str] = mapped_column(String(255), default="")  # id categoria / nome stack
    targets: Mapped[list] = mapped_column(JSON, default=list)  # nomi container

    # quando
    trigger_kind: Mapped[str] = mapped_column(String(16))  # cron|interval|once
    cron_expr: Mapped[str] = mapped_column(String(120), default="")
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone_name: Mapped[str] = mapped_column(String(64), default="")

    # esito
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(16), default="")
    last_message: Mapped[str] = mapped_column(Text, default="")
    run_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(16), default="ui")  # ui|schedule|api
    action: Mapped[str] = mapped_column(String(24), default="")
    target: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|error|partial
    message: Mapped[str] = mapped_column(Text, default="")
