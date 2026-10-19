"""Tutto ciò che parla con il daemon Docker."""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import docker
from docker.errors import APIError, DockerException, NotFound

from .config import settings

log = logging.getLogger("docker-manager.docker")

CONTAINER_ACTIONS = {"start", "stop", "restart", "pause", "unpause", "kill"}
STACK_ACTIONS = CONTAINER_ACTIONS | {"up", "down"}

LABEL_PROJECT = "com.docker.compose.project"
LABEL_SERVICE = "com.docker.compose.service"
LABEL_WORKDIR = "com.docker.compose.project.working_dir"
LABEL_CONFIG_FILES = "com.docker.compose.project.config_files"


class DockerUnavailable(RuntimeError):
    pass


class ActionError(RuntimeError):
    pass


_client: docker.DockerClient | None = None
_client_lock = threading.Lock()


def get_client() -> docker.DockerClient:
    global _client
    with _client_lock:
        if _client is None:
            try:
                if settings.docker_host:
                    _client = docker.DockerClient(base_url=settings.docker_host, timeout=30)
                else:
                    _client = docker.from_env(timeout=30)
            except DockerException as exc:  # pragma: no cover - dipende dall'host
                raise DockerUnavailable(str(exc)) from exc
        return _client


def reset_client() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            with contextlib.suppress(Exception):
                _client.close()
        _client = None


def ping() -> dict:
    try:
        client = get_client()
        info = client.info()
        version = client.version()
        return {
            "connected": True,
            "docker_version": version.get("Version", "?"),
            "api_version": version.get("ApiVersion", "?"),
            "host": info.get("Name", "?"),
            "containers_total": info.get("Containers", 0),
            "containers_running": info.get("ContainersRunning", 0),
            "images": info.get("Images", 0),
            "cpus": info.get("NCPU", 0),
            "memory": info.get("MemTotal", 0),
            "error": None,
        }
    except (DockerUnavailable, DockerException, OSError) as exc:
        reset_client()
        return {"connected": False, "error": str(exc)}


# --------------------------------------------------------------------------- #
# Identità del container in cui girano noi stessi                              #
# --------------------------------------------------------------------------- #

def _self_id() -> str:
    for var in ("HOSTNAME", "DOCKER_MANAGER_SELF_ID"):
        value = os.getenv(var, "").strip()
        if len(value) >= 12:
            return value
    try:
        return socket.gethostname()
    except OSError:
        return ""


SELF_ID = _self_id()


# --------------------------------------------------------------------------- #
# Metriche live (cache aggiornata da un thread di background)                  #
# --------------------------------------------------------------------------- #

class StatsCollector:
    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not settings.enable_stats or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="stats-collector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def get(self, container_id: str) -> dict:
        with self._lock:
            return self._cache.get(container_id, {})

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._refresh()
            except Exception as exc:
                log.debug("raccolta metriche fallita: %s", exc)
            self._stop.wait(settings.stats_interval)

    def _refresh(self) -> None:
        client = get_client()
        running = client.containers.list(filters={"status": "running"})
        if not running:
            with self._lock:
                self._cache.clear()
            return
        fresh: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=settings.stats_workers) as pool:
            for container, stats in zip(running, pool.map(self._one, running), strict=False):
                if stats:
                    fresh[container.id] = stats
        with self._lock:
            self._cache = fresh

    @staticmethod
    def _one(container) -> dict | None:
        try:
            raw = container.stats(stream=False)
        except (DockerException, OSError):
            return None
        return parse_stats(raw)


def parse_stats(raw: dict) -> dict:
    cpu_percent = 0.0
    try:
        cpu = raw["cpu_stats"]
        pre = raw.get("precpu_stats") or {}
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre.get("cpu_usage", {}).get("total_usage", 0)
        sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        ncpu = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or []) or 1
        if sys_delta > 0 and cpu_delta > 0:
            cpu_percent = (cpu_delta / sys_delta) * ncpu * 100.0
    except (KeyError, TypeError, ZeroDivisionError):
        cpu_percent = 0.0

    mem_usage = 0
    mem_limit = 0
    try:
        mem = raw["memory_stats"]
        mem_limit = int(mem.get("limit") or 0)
        detail = mem.get("stats") or {}
        inactive = int(detail.get("inactive_file") or detail.get("total_inactive_file") or 0)
        mem_usage = max(int(mem.get("usage") or 0) - inactive, 0)
    except (KeyError, TypeError):
        pass

    net_rx = net_tx = 0
    for iface in (raw.get("networks") or {}).values():
        net_rx += int(iface.get("rx_bytes") or 0)
        net_tx += int(iface.get("tx_bytes") or 0)

    return {
        "cpu_percent": round(cpu_percent, 2),
        "mem_usage": mem_usage,
        "mem_limit": mem_limit,
        "mem_percent": round(mem_usage / mem_limit * 100, 2) if mem_limit else 0.0,
        "net_rx": net_rx,
        "net_tx": net_tx,
    }


stats_collector = StatsCollector()


# --------------------------------------------------------------------------- #
# Lettura container                                                            #
# --------------------------------------------------------------------------- #

def _parse_docker_time(value: str | None) -> datetime | None:
    if not value or value.startswith("0001-01-01"):
        return None
    cleaned = value.replace("Z", "+00:00")
    # Docker usa nanosecondi, Python ne accetta 6 cifre
    if "." in cleaned:
        head, _, tail = cleaned.partition(".")
        frac = tail
        offset = ""
        for marker in ("+", "-"):
            if marker in tail:
                frac, _, off = tail.partition(marker)
                offset = marker + off
                break
        cleaned = f"{head}.{frac[:6]}{offset}"
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _ports(attrs: dict) -> list[dict]:
    out: list[dict] = []
    mapping = (attrs.get("NetworkSettings") or {}).get("Ports") or {}
    for container_port, bindings in sorted(mapping.items()):
        if not bindings:
            out.append({"container": container_port, "host": None, "url": None})
            continue
        for binding in bindings:
            host_ip = binding.get("HostIp") or "0.0.0.0"
            host_port = binding.get("HostPort") or ""
            display_ip = "localhost" if host_ip in {"0.0.0.0", "::", ""} else host_ip
            out.append(
                {
                    "container": container_port,
                    "host": f"{host_ip}:{host_port}",
                    "url": f"http://{display_ip}:{host_port}" if host_port else None,
                }
            )
    return out


def serialize(container, *, with_stats: bool = True) -> dict:
    attrs = container.attrs or {}
    state = attrs.get("State") or {}
    config = attrs.get("Config") or {}
    labels = config.get("Labels") or {}
    started_at = _parse_docker_time(state.get("StartedAt"))
    finished_at = _parse_docker_time(state.get("FinishedAt"))
    status = (state.get("Status") or container.status or "unknown").lower()

    uptime = None
    if status == "running" and started_at:
        uptime = int((datetime.now(UTC) - started_at).total_seconds())

    image_tags = (attrs.get("Config") or {}).get("Image") or ""
    try:
        if container.image and container.image.tags:
            image_tags = container.image.tags[0]
    except (DockerException, AttributeError):
        pass

    data = {
        "id": container.id,
        "short_id": container.id[:12],
        "name": container.name,
        "image": image_tags,
        "state": status,
        "status_text": container.status,
        "health": ((state.get("Health") or {}).get("Status") or None),
        "exit_code": state.get("ExitCode"),
        "created_at": _parse_docker_time(attrs.get("Created")),
        "started_at": started_at,
        "finished_at": finished_at,
        "uptime_seconds": uptime,
        "ports": _ports(attrs),
        "stack": labels.get(LABEL_PROJECT) or None,
        "service": labels.get(LABEL_SERVICE) or None,
        "compose_workdir": labels.get(LABEL_WORKDIR) or "",
        "compose_files": [f for f in (labels.get(LABEL_CONFIG_FILES) or "").split(",") if f],
        "restart_policy": ((attrs.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name") or "no",
        "networks": sorted((attrs.get("NetworkSettings") or {}).get("Networks", {}).keys()),
        "is_self": bool(SELF_ID) and container.id.startswith(SELF_ID),
        "stats": stats_collector.get(container.id) if with_stats else {},
    }
    return data


def list_containers() -> list[dict]:
    client = get_client()
    try:
        containers = client.containers.list(all=True)
    except (DockerException, OSError) as exc:
        reset_client()
        raise DockerUnavailable(str(exc)) from exc
    items = [serialize(c) for c in containers]
    if settings.hide_self:
        items = [c for c in items if not c["is_self"]]
    items.sort(key=lambda c: (c["stack"] or "￿", c["name"].lower()))
    return items


def get_container(ref: str):
    client = get_client()
    try:
        return client.containers.get(ref)
    except NotFound as exc:
        raise ActionError(f"container '{ref}' non trovato") from exc
    except (DockerException, OSError) as exc:
        reset_client()
        raise DockerUnavailable(str(exc)) from exc


def resolve_names(names: list[str]) -> list[str]:
    """Filtra i nomi che esistono realmente, mantenendo l'ordine."""
    existing = {c["name"] for c in list_containers()}
    return [n for n in names if n in existing]


def logs(ref: str, tail: int = 300, timestamps: bool = True) -> str:
    container = get_container(ref)
    try:
        raw = container.logs(tail=tail, timestamps=timestamps, stdout=True, stderr=True)
    except (DockerException, OSError) as exc:
        raise ActionError(str(exc)) from exc
    return raw.decode("utf-8", errors="replace")


def inspect(ref: str) -> dict:
    container = get_container(ref)
    return serialize(container)


# --------------------------------------------------------------------------- #
# Azioni                                                                       #
# --------------------------------------------------------------------------- #

def apply_action(ref: str, action: str) -> str:
    """Esegue l'azione su un singolo container. Ritorna un messaggio breve."""
    if action not in CONTAINER_ACTIONS:
        raise ActionError(f"azione '{action}' non supportata")
    container = get_container(ref)
    state = (container.attrs.get("State") or {}).get("Status", "")

    try:
        if action == "start":
            if state == "running":
                return "già in esecuzione"
            if state == "paused":
                container.unpause()
                return "ripreso"
            container.start()
            return "avviato"
        if action == "stop":
            if state in {"exited", "created", "dead"}:
                return "già fermo"
            container.stop(timeout=settings.stop_timeout)
            return "fermato"
        if action == "restart":
            container.restart(timeout=settings.stop_timeout)
            return "riavviato"
        if action == "pause":
            if state != "running":
                return "non in esecuzione"
            container.pause()
            return "in pausa"
        if action == "unpause":
            if state != "paused":
                return "non in pausa"
            container.unpause()
            return "ripreso"
        if action == "kill":
            container.kill()
            return "terminato"
    except APIError as exc:
        raise ActionError(exc.explanation or str(exc)) from exc
    except (DockerException, OSError) as exc:
        raise ActionError(str(exc)) from exc
    return "ok"


def bulk_action(names: list[str], action: str) -> dict:
    """Esegue l'azione su più container in parallelo."""
    unique: list[str] = list(dict.fromkeys(n for n in names if n))
    if not unique:
        return {"status": "error", "results": [], "message": "nessun target selezionato"}

    def run(name: str) -> dict:
        try:
            return {"target": name, "ok": True, "message": apply_action(name, action)}
        except (ActionError, DockerUnavailable) as exc:
            return {"target": name, "ok": False, "message": str(exc)}

    with ThreadPoolExecutor(max_workers=min(8, len(unique))) as pool:
        results = list(pool.map(run, unique))

    failed = [r for r in results if not r["ok"]]
    status = "ok" if not failed else ("error" if len(failed) == len(results) else "partial")
    return {"status": status, "results": results}


# --------------------------------------------------------------------------- #
# Stack (docker compose)                                                       #
# --------------------------------------------------------------------------- #

def list_stacks() -> list[dict]:
    grouped: dict[str, dict] = {}
    for container in list_containers():
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


def _compose_context(project: str) -> tuple[str, list[str]]:
    """Ricava dalle label di un container dello stack dove si trova il file
    compose. Funziona solo se lo stack ha ancora almeno un container: dopo un
    `down` il contesto arriva dal registro su database (vedi app/stacks.py)."""
    client = get_client()
    containers = client.containers.list(all=True, filters={"label": f"{LABEL_PROJECT}={project}"})
    if not containers:
        raise ActionError(
            f"stack '{project}' non trovato: nessun container e nessun percorso compose memorizzato"
        )
    labels = (containers[0].attrs.get("Config") or {}).get("Labels") or {}
    workdir = labels.get(LABEL_WORKDIR) or ""
    files = [f for f in (labels.get(LABEL_CONFIG_FILES) or "").split(",") if f]
    return workdir, files


def compose_cli_available() -> bool:
    if not settings.compose_cli:
        return False
    return shutil.which("docker") is not None


def compose_action(
    project: str,
    action: str,
    context: tuple[str, list[str]] | None = None,
) -> dict:
    """`up -d` / `down` reali via CLI compose. Richiede COMPOSE_CLI=true e il
    file compose raggiungibile dentro al container (volume montato).

    `context` è la coppia (working_dir, config_files) presa dal registro degli
    stack; se manca si ripiega sulle label dei container ancora esistenti."""
    if action not in {"up", "down"}:
        raise ActionError(f"azione stack '{action}' non supportata")
    if not compose_cli_available():
        raise ActionError(
            "operazioni compose up/down disattivate: imposta COMPOSE_CLI=true e monta "
            "le directory dei progetti compose nel container"
        )
    workdir, files = context if context and context[0] else _compose_context(project)
    if not workdir or not os.path.isdir(workdir):
        raise ActionError(
            f"la directory del progetto '{workdir or '?'}' non è visibile dentro il container; "
            "montala con lo stesso path per usare up/down"
        )
    cmd = ["docker", "compose", "--project-name", project, "--project-directory", workdir]
    for file in files:
        if os.path.isfile(file):
            cmd += ["--file", file]
    cmd += ["up", "--detach"] if action == "up" else ["down"]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise ActionError(output[-800:] or f"compose {action} uscito con codice {proc.returncode}")
    return {
        "status": "ok",
        "results": [{"target": project, "ok": True, "message": f"compose {action} eseguito"}],
        "output": output[-4000:],
    }


def stack_action(
    project: str,
    action: str,
    context: tuple[str, list[str]] | None = None,
) -> dict:
    if action in {"up", "down"}:
        return compose_action(project, action, context)
    stacks = {s["name"]: s for s in list_stacks()}
    if project not in stacks:
        raise ActionError(f"stack '{project}' non trovato")
    return bulk_action(stacks[project]["containers"], action)
