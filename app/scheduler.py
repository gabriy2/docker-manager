"""Pianificazione: traduce le righe della tabella `schedules` in job APScheduler.

I job non vengono persistiti da APScheduler: la fonte di verità è il database e
all'avvio i job vengono ricostruiti. Così l'UI resta sempre coerente col DB.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from croniter import croniter

from . import actions
from .config import settings
from .db import session_scope
from .models import Schedule, as_utc

log = logging.getLogger("docker-manager.scheduler")

TRIGGER_KINDS = {"cron", "interval", "once"}


class ScheduleError(ValueError):
    pass


def tz_for(name: str | None):
    if not name:
        return settings.timezone
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return settings.timezone


def build_trigger(schedule: Schedule):
    tz = tz_for(schedule.timezone_name)
    kind = schedule.trigger_kind
    if kind == "cron":
        expr = (schedule.cron_expr or "").strip()
        if not croniter.is_valid(expr):
            raise ScheduleError(f"espressione cron non valida: '{expr}'")
        return CronTrigger.from_crontab(expr, timezone=tz)
    if kind == "interval":
        minutes = int(schedule.interval_minutes or 0)
        if minutes < 1:
            raise ScheduleError("l'intervallo deve essere di almeno 1 minuto")
        return IntervalTrigger(minutes=minutes, timezone=tz)
    if kind == "once":
        if not schedule.run_at:
            raise ScheduleError("data/ora di esecuzione mancante")
        return DateTrigger(run_date=as_utc(schedule.run_at), timezone=tz)
    raise ScheduleError(f"tipo di pianificazione '{kind}' non valido")


def validate(schedule: Schedule) -> None:
    """Solleva ScheduleError se la pianificazione non è eseguibile."""
    if schedule.trigger_kind not in TRIGGER_KINDS:
        raise ScheduleError(f"tipo di pianificazione '{schedule.trigger_kind}' non valido")
    build_trigger(schedule)


def _run_job(schedule_id: int) -> None:
    with session_scope() as db:
        schedule = db.get(Schedule, schedule_id)
        if schedule is None or not schedule.enabled:
            return
        log.info("eseguo pianificazione #%s '%s' (%s)", schedule.id, schedule.name, schedule.action)
        targets = list(schedule.targets or [])
        description = actions.describe_target(schedule.target_kind, schedule.target_ref, targets, db)
        result = actions.execute(
            db,
            action=schedule.action,
            target_kind=schedule.target_kind,
            target_ref=schedule.target_ref,
            targets=targets,
            source="schedule",
            label=f"{schedule.name} → {description}",
        )
        schedule.last_run_at = datetime.now(UTC)
        schedule.last_status = result["status"]
        schedule.last_message = actions.summarize(result)[:2000]
        schedule.run_count = (schedule.run_count or 0) + 1
        one_shot = schedule.trigger_kind == "once"
        if one_shot:
            schedule.enabled = False
        db.add(schedule)
        db.commit()
    if one_shot:
        # la pianificazione una-tantum si auto-disattiva: rimuoviamo il job
        manager.remove(schedule_id)


class ScheduleManager:
    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler(
            timezone=settings.timezone,
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600},
        )

    # ---------------- lifecycle ---------------- #

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()
        self.sync_all()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    # ---------------- sync DB -> job ---------------- #

    @staticmethod
    def job_id(schedule_id: int) -> str:
        return f"schedule-{schedule_id}"

    def sync_all(self) -> None:
        with session_scope() as db:
            for schedule in db.query(Schedule).all():
                self._apply(schedule)

    def refresh(self, schedule_id: int) -> None:
        with session_scope() as db:
            schedule = db.get(Schedule, schedule_id)
            if schedule is None:
                self.remove(schedule_id)
            else:
                self._apply(schedule)

    def sync(self, schedule: Schedule) -> None:
        self._apply(schedule)

    def _apply(self, schedule: Schedule) -> None:
        job_id = self.job_id(schedule.id)
        self.remove(schedule.id)
        if not schedule.enabled:
            return
        try:
            trigger = build_trigger(schedule)
        except ScheduleError as exc:
            log.warning("pianificazione #%s ignorata: %s", schedule.id, exc)
            return
        if schedule.trigger_kind == "once" and trigger.run_date < datetime.now(UTC):
            log.info("pianificazione una-tantum #%s già passata, non registrata", schedule.id)
            return
        self.scheduler.add_job(
            _run_job,
            trigger=trigger,
            args=[schedule.id],
            id=job_id,
            name=schedule.name,
            replace_existing=True,
        )

    def remove(self, schedule_id: int) -> None:
        # JobLookupError se il job non esiste: nulla da fare
        with contextlib.suppress(Exception):
            self.scheduler.remove_job(self.job_id(schedule_id))

    # ---------------- introspezione ---------------- #

    def next_run(self, schedule_id: int) -> datetime | None:
        job = self.scheduler.get_job(self.job_id(schedule_id))
        return getattr(job, "next_run_time", None) if job else None

    def run_now(self, schedule_id: int) -> None:
        _run_job(schedule_id)


manager = ScheduleManager()


def preview_next_runs(schedule: Schedule, count: int = 3) -> list[datetime]:
    """Prossime esecuzioni previste, per mostrarle nell'interfaccia."""
    try:
        trigger = build_trigger(schedule)
    except ScheduleError:
        return []
    tz = tz_for(schedule.timezone_name)
    out: list[datetime] = []
    previous = None
    now = datetime.now(tz)
    for _ in range(count):
        nxt = trigger.get_next_fire_time(previous, now if previous is None else previous)
        if nxt is None:
            break
        out.append(nxt)
        previous = nxt
    return out
