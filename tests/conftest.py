import os
import tempfile

# Va impostato prima di importare l'app: la configurazione è letta all'import.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="dm-test-"))
os.environ.setdefault("TZ", "Europe/Rome")
os.environ.setdefault("AUTH_PASSWORD", "")
os.environ.setdefault("ENABLE_STATS", "false")

import pytest
from fastapi.testclient import TestClient

from app import docker_service as ds
from app.main import app

FAKE_CONTAINERS = [
    {
        "id": "a" * 64, "short_id": "a" * 12, "name": "web", "image": "nginx:alpine",
        "state": "running", "status_text": "running", "health": "healthy", "exit_code": 0,
        "created_at": None, "started_at": None, "finished_at": None, "uptime_seconds": 120,
        "ports": [], "stack": "blog", "service": "web", "restart_policy": "unless-stopped",
        "networks": ["blog_default"], "is_self": False, "stats": {},
    },
    {
        "id": "b" * 64, "short_id": "b" * 12, "name": "db", "image": "postgres:16",
        "state": "exited", "status_text": "exited", "health": None, "exit_code": 0,
        "created_at": None, "started_at": None, "finished_at": None, "uptime_seconds": None,
        "ports": [], "stack": "blog", "service": "db", "restart_policy": "no",
        "networks": ["blog_default"], "is_self": False, "stats": {},
    },
]


@pytest.fixture(autouse=True)
def fake_docker(monkeypatch):
    monkeypatch.setattr(ds, "list_containers", lambda: [dict(c) for c in FAKE_CONTAINERS])
    monkeypatch.setattr(ds, "ping", lambda: {
        "connected": True, "docker_version": "99.0", "api_version": "1.47", "host": "test",
        "containers_total": 2, "containers_running": 1, "images": 3, "cpus": 4,
        "memory": 8 * 1024**3, "error": None,
    })
    monkeypatch.setattr(ds, "bulk_action", lambda names, action: {
        "status": "ok", "results": [{"target": n, "ok": True, "message": action} for n in names],
    })


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
