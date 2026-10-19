import pytest

from app.models import Schedule
from app.scheduler import ScheduleError, build_trigger, preview_next_runs, validate


def cron(expr: str) -> Schedule:
    return Schedule(name="t", action="stop", target_kind="all", trigger_kind="cron", cron_expr=expr)


def test_trigger_cron_valido():
    trigger = build_trigger(cron("30 2 * * 1,5"))
    assert trigger is not None


def test_trigger_cron_non_valido():
    with pytest.raises(ScheduleError):
        validate(cron("non valido"))


def test_trigger_intervallo_minimo():
    schedule = Schedule(name="t", action="start", target_kind="all",
                        trigger_kind="interval", interval_minutes=0)
    with pytest.raises(ScheduleError):
        validate(schedule)


def test_trigger_una_tantum_senza_data():
    schedule = Schedule(name="t", action="start", target_kind="all", trigger_kind="once", run_at=None)
    with pytest.raises(ScheduleError):
        validate(schedule)


def test_preview_restituisce_occorrenze_crescenti():
    runs = preview_next_runs(cron("0 3 * * *"), count=3)
    assert len(runs) == 3
    assert runs[0] < runs[1] < runs[2]
    assert all(r.hour == 3 and r.minute == 0 for r in runs)


def test_preview_cron_invalido_vuoto():
    assert preview_next_runs(cron("@@@"), count=3) == []
