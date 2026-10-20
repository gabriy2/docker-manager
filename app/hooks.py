"""Webhook di backup: sospensione e ripresa dei container intorno a un backup.

Pensato per ZeroByte (https://zerobyte.app/docs/guides/backup-webhooks), che
chiama un URL in POST prima che restic inizi e un altro quando ha finito:

* un `pre` che non risponde 2xx **annulla il backup**, quindi qui rispondiamo
  con errore se anche un solo container non si è fermato: meglio un backup
  saltato che uno incoerente;
* il `post` viene chiamato anche quando il backup fallisce o viene annullato,
  perciò la ripresa dei container non dipende dall'esito del backup.

Il contratto vale per qualsiasi altro backup tool capace di chiamare due URL.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import actions
from . import docker_service as ds
from .models import BackupHook, to_utc

log = logging.getLogger("docker-manager.hooks")

MODES = {"stop", "pause"}

# quale azione usare per sospendere e per riprendere, a seconda del modo
_SUSPEND = {"stop": "stop", "pause": "pause"}
_RESUME = {"stop": "start", "pause": "unpause"}

# stati considerati "attivi": solo questi vengono sospesi e quindi ripresi
_LIVE_STATES = {"running", "restarting"}


def generate_token() -> str:
    return secrets.token_urlsafe(24)


def find_by_token(db: Session, token: str) -> BackupHook | None:
    """Ricerca a tempo costante: confrontiamo tutti i token con compare_digest
    per non far trapelare quale prefisso è corretto."""
    if not token:
        return None
    found = None
    for hook in db.scalars(select(BackupHook)).all():
        if secrets.compare_digest(hook.token, token):
            found = hook
    return found


def _targets_now(db: Session, hook: BackupHook) -> list[str]:
    """Container su cui agisce l'hook, escluso sempre il pannello stesso:
    se si spegnesse da solo nessuno potrebbe più chiamare il webhook post."""
    names = actions.resolve_targets(db, hook.target_kind, hook.target_ref, list(hook.targets or []))
    by_name = {c["name"]: c for c in ds.list_containers()}
    return [n for n in names if not by_name.get(n, {}).get("is_self")]


def _live_subset(names: list[str]) -> list[str]:
    by_name = {c["name"]: c for c in ds.list_containers()}
    return [n for n in names if by_name.get(n, {}).get("state") in _LIVE_STATES]


def _record(db: Session, hook: BackupHook, phase: str, result: dict, *, note: str = "") -> None:
    message = actions.summarize(result)
    if note:
        message = f"{note} — {message}" if message else note
    hook.last_phase = phase
    hook.last_run_at = datetime.now(UTC)
    hook.last_status = result["status"]
    hook.last_message = message[:2000]
    hook.run_count = (hook.run_count or 0) + 1
    db.add(hook)
    db.commit()
    bersaglio = actions.describe_target(
        hook.target_kind, hook.target_ref, list(hook.targets or []), db
    )
    actions.record_activity(
        db,
        source="webhook",
        action=f"backup {phase}",
        target=f"{hook.name} → {bersaglio}",
        status=result["status"],
        message=message,
    )


def run_pre(db: Session, hook: BackupHook) -> dict:
    """Sospende i container attivi e ricorda quali erano, per il post."""
    if hook.suspended:
        # un pre ripetuto senza post (backup incatenati, retry di ZeroByte):
        # non tocchiamo l'elenco già memorizzato, altrimenti lo perderemmo
        result = {
            "status": "ok",
            "results": [],
            "message": f"sospensione già attiva su {len(hook.suspended)} container",
        }
        _record(db, hook, "pre", result)
        return {**result, "suspended": list(hook.suspended)}

    try:
        names = _targets_now(db, hook)
    except (actions.TargetError, ds.DockerUnavailable) as exc:
        result = {"status": "error", "results": [], "message": str(exc)}
        _record(db, hook, "pre", result)
        return {**result, "suspended": []}

    live = _live_subset(names)
    if not live:
        result = {"status": "ok", "results": [], "message": "nessun container attivo da sospendere"}
        _record(db, hook, "pre", result)
        return {**result, "suspended": []}

    result = ds.bulk_action(live, _SUSPEND[hook.mode])
    # memorizziamo solo quelli davvero sospesi: gli altri non vanno riavviati
    stopped = [r["target"] for r in result["results"] if r["ok"]]
    hook.suspended = stopped
    hook.suspended_at = datetime.now(UTC) if stopped else None
    _record(db, hook, "pre", result)
    return {**result, "suspended": stopped}


def run_post(db: Session, hook: BackupHook, *, phase: str = "post", note: str = "") -> dict:
    """Riavvia esattamente i container sospesi dal pre."""
    pending = list(hook.suspended or [])
    if not pending:
        result = {"status": "ok", "results": [], "message": "nessun container da riprendere"}
        _record(db, hook, phase, result, note=note)
        return {**result, "resumed": []}

    result = ds.bulk_action(pending, _RESUME[hook.mode])
    resumed = [r["target"] for r in result["results"] if r["ok"]]
    # i container che non sono ripartiti restano in elenco: la rete di sicurezza
    # e il pulsante «riprendi adesso» potranno riprovare
    hook.suspended = [n for n in pending if n not in resumed]
    hook.suspended_at = None if not hook.suspended else hook.suspended_at
    _record(db, hook, phase, result, note=note)
    return {**result, "resumed": resumed}


def watchdog(db: Session) -> int:
    """Riprende i container rimasti sospesi troppo a lungo.

    Serve quando il backup si pianta o il container di ZeroByte muore: senza
    questa rete di sicurezza i servizi resterebbero spenti indefinitamente.
    """
    now = datetime.now(UTC)
    recovered = 0
    for hook in db.scalars(select(BackupHook)).all():
        limit = int(hook.resume_after_minutes or 0)
        if not hook.suspended or limit <= 0 or hook.suspended_at is None:
            continue
        if now - to_utc(hook.suspended_at) < timedelta(minutes=limit):
            continue
        log.warning(
            "hook '%s': post non arrivato entro %s minuti, riprendo %s container",
            hook.name,
            limit,
            len(hook.suspended),
        )
        run_post(db, hook, phase="resume", note=f"post non arrivato entro {limit} minuti")
        recovered += 1
    return recovered
