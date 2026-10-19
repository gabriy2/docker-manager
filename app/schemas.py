from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .docker_service import CONTAINER_ACTIONS, STACK_ACTIONS

ALL_ACTIONS = sorted(STACK_ACTIONS)


class LoginIn(BaseModel):
    username: str = ""
    password: str = ""


class ActionIn(BaseModel):
    action: str
    target_kind: str = "containers"
    target_ref: str = ""
    targets: list[str] = Field(default_factory=list)

    @field_validator("action")
    @classmethod
    def _action_ok(cls, value: str) -> str:
        if value not in STACK_ACTIONS:
            raise ValueError(f"azione non valida (ammesse: {', '.join(ALL_ACTIONS)})")
        return value

    @field_validator("target_kind")
    @classmethod
    def _kind_ok(cls, value: str) -> str:
        if value not in {"containers", "category", "stack", "all"}:
            raise ValueError("target_kind non valido")
        return value


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = "#6366f1"
    icon: str = "📦"
    description: str = ""
    position: int = 0
    containers: list[str] = Field(default_factory=list)


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = None
    icon: str | None = None
    description: str | None = None
    position: int | None = None
    containers: list[str] | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    color: str
    icon: str
    description: str
    position: int
    containers: list[str] = Field(default_factory=list)
    running: int = 0
    total: int = 0


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    action: str
    target_kind: str = "containers"
    target_ref: str = ""
    targets: list[str] = Field(default_factory=list)
    trigger_kind: str = "cron"
    cron_expr: str = ""
    interval_minutes: int = 60
    run_at: datetime | None = None
    timezone_name: str = ""

    @field_validator("action")
    @classmethod
    def _action_ok(cls, value: str) -> str:
        if value not in STACK_ACTIONS:
            raise ValueError(f"azione non valida (ammesse: {', '.join(ALL_ACTIONS)})")
        return value

    @field_validator("target_kind")
    @classmethod
    def _kind_ok(cls, value: str) -> str:
        if value not in {"containers", "category", "stack", "all"}:
            raise ValueError("target_kind non valido")
        return value

    @field_validator("trigger_kind")
    @classmethod
    def _trigger_ok(cls, value: str) -> str:
        if value not in {"cron", "interval", "once"}:
            raise ValueError("trigger_kind non valido")
        return value


class ScheduleOut(BaseModel):
    id: int
    name: str
    enabled: bool
    action: str
    target_kind: str
    target_ref: str
    targets: list[str]
    target_label: str
    trigger_kind: str
    cron_expr: str
    interval_minutes: int
    run_at: datetime | None
    timezone_name: str
    next_run: datetime | None
    next_runs: list[datetime]
    last_run_at: datetime | None
    last_status: str
    last_message: str
    run_count: int


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    source: str
    action: str
    target: str
    status: str
    message: str


CONTAINER_ONLY_ACTIONS = sorted(CONTAINER_ACTIONS)
