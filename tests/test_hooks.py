"""Webhook di backup: sospensione prima del backup e ripresa dopo."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import docker_service as ds
from app import hooks as hk
from app.db import session_scope
from app.models import BackupHook


@pytest.fixture(autouse=True)
def db_pulito():
    """Il database di test è condiviso: ogni test parte senza hook."""
    with session_scope() as db:
        db.query(BackupHook).delete()
        db.commit()


def crea(client, **payload) -> dict:
    res = client.post("/api/hooks", json={"name": "Hook", **payload})
    assert res.status_code == 201, res.text
    creato = res.json()
    return stato(client, creato["id"])


def stato(client, hook_id: int) -> dict:
    righe = client.get("/api/hooks").json()
    return next(h for h in righe if h["id"] == hook_id)


@pytest.fixture
def hook(client):
    return crea(client, name="Backup notturno", target_kind="stack", target_ref="blog")


def test_create_returns_token_and_urls(hook):
    assert hook["token"]
    assert hook["pre_url"] == f"/hooks/{hook['token']}/pre"
    assert hook["post_url"] == f"/hooks/{hook['token']}/post"
    assert hook["mode"] == "stop"
    assert hook["suspended"] == []


def test_pre_sospende_solo_i_container_attivi(client, hook):
    # nello stack "blog" solo `web` è in esecuzione, `db` è già fermo
    res = client.post(hook["pre_url"])
    assert res.status_code == 200
    assert res.json()["suspended"] == ["web"]

    stato_hook = stato(client, hook["id"])
    assert stato_hook["suspended"] == ["web"]
    assert stato_hook["suspended_at"] is not None


def test_post_riprende_solo_quelli_sospesi(client, hook):
    client.post(hook["pre_url"])
    res = client.post(hook["post_url"], json={"phase": "post", "status": "success"})
    assert res.status_code == 200
    assert res.json()["resumed"] == ["web"]

    stato_hook = stato(client, hook["id"])
    assert stato_hook["suspended"] == []
    assert "success" in stato_hook["last_message"]


def test_post_riprende_anche_se_il_backup_e_fallito(client, hook):
    client.post(hook["pre_url"])
    res = client.post(hook["post_url"], json={"phase": "post", "status": "error"})
    assert res.status_code == 200
    assert res.json()["resumed"] == ["web"]


def test_post_tollera_un_corpo_vuoto_o_non_json(client, hook):
    client.post(hook["pre_url"])
    res = client.post(hook["post_url"], content=b"non-json", headers={"Content-Type": "text/plain"})
    assert res.status_code == 200
    assert res.json()["resumed"] == ["web"]


def test_pre_ripetuto_non_perde_elenco_sospesi(client, hook):
    client.post(hook["pre_url"])
    res = client.post(hook["pre_url"])
    assert res.status_code == 200
    assert stato(client, hook["id"])["suspended"] == ["web"]


def test_token_sconosciuto(client, hook):
    assert client.post("/hooks/inventato/pre").status_code == 404


def test_hook_disattivato_rifiuta(client, hook):
    client.post(f"/api/hooks/{hook['id']}/toggle")
    assert client.post(hook["pre_url"]).status_code == 409


def test_pre_fallito_blocca_il_backup(client, hook, monkeypatch):
    monkeypatch.setattr(ds, "bulk_action", lambda names, action: {
        "status": "error",
        "results": [{"target": n, "ok": False, "message": "boom"} for n in names],
    })
    res = client.post(hook["pre_url"])
    # non 2xx: ZeroByte annulla il backup invece di copiare dati incoerenti
    assert res.status_code == 500
    assert stato(client, hook["id"])["suspended"] == []


def test_rotazione_token_invalida_il_vecchio(client, hook):
    client.post(f"/api/hooks/{hook['id']}/rotate")
    assert client.post(hook["pre_url"]).status_code == 404
    nuovo = stato(client, hook["id"])
    assert nuovo["token"] != hook["token"]
    assert client.post(nuovo["pre_url"]).status_code == 200


def test_non_sospende_mai_il_pannello(client, monkeypatch):
    monkeypatch.setattr(ds, "list_containers", lambda: [
        {"name": "web", "state": "running", "is_self": False, "stack": "blog"},
        {"name": "docker-manager", "state": "running", "is_self": True, "stack": "blog"},
    ])
    tutto = crea(client, name="Tutto", target_kind="all")
    assert client.post(tutto["pre_url"]).json()["suspended"] == ["web"]


def test_eliminazione_bloccata_con_container_sospesi(client, hook):
    client.post(hook["pre_url"])
    assert client.delete(f"/api/hooks/{hook['id']}").status_code == 409
    client.post(hook["post_url"])
    assert client.delete(f"/api/hooks/{hook['id']}").status_code == 200


def test_watchdog_riprende_dopo_la_scadenza(client, hook):
    client.post(hook["pre_url"])
    with session_scope() as db:
        row = db.scalar(select(BackupHook).where(BackupHook.id == hook["id"]))
        row.resume_after_minutes = 30
        row.suspended_at = datetime.now(UTC) - timedelta(minutes=31)
        db.commit()
        assert hk.watchdog(db) == 1

    stato_hook = stato(client, hook["id"])
    assert stato_hook["suspended"] == []
    assert stato_hook["last_phase"] == "resume"


def test_watchdog_non_tocca_sospensioni_recenti(client, hook):
    client.post(hook["pre_url"])
    with session_scope() as db:
        assert hk.watchdog(db) == 0
    assert stato(client, hook["id"])["suspended"] == ["web"]


def test_watchdog_disattivabile_con_zero(client, hook):
    client.post(hook["pre_url"])
    with session_scope() as db:
        row = db.scalar(select(BackupHook).where(BackupHook.id == hook["id"]))
        row.resume_after_minutes = 0
        row.suspended_at = datetime.now(UTC) - timedelta(days=7)
        db.commit()
        assert hk.watchdog(db) == 0
    assert stato(client, hook["id"])["suspended"] == ["web"]


def test_modalita_pause_usa_pause_e_unpause(client, monkeypatch):
    chiamate = []
    monkeypatch.setattr(ds, "bulk_action", lambda names, action: (
        chiamate.append(action),
        {"status": "ok", "results": [{"target": n, "ok": True, "message": action} for n in names]},
    )[1])
    creato = crea(client, name="Pausa", target_kind="stack", target_ref="blog", mode="pause")
    client.post(creato["pre_url"])
    client.post(creato["post_url"])
    assert chiamate == ["pause", "unpause"]


def test_meta_espone_la_base_interna_degli_url(client, monkeypatch):
    """L'interfaccia da sola vedrebbe solo l'URL da cui la stai navigando:
    dietro a un reverse proxy quello pubblico. Il server deve dirle anche
    come lo si raggiunge dalla rete docker."""
    monkeypatch.setattr(ds, "self_name", lambda: "docker-manager")
    meta = client.get("/api/meta").json()
    assert meta["internal_base_url"] == "http://docker-manager:8000"
    assert meta["hook_base_url_fixed"] is False


def test_hook_base_url_ha_la_precedenza(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "hook_base_url", "https://pannello.casa.lan")
    monkeypatch.setattr(ds, "self_name", lambda: "docker-manager")
    meta = client.get("/api/meta").json()
    assert meta["internal_base_url"] == "https://pannello.casa.lan"
    assert meta["hook_base_url_fixed"] is True


def test_base_interna_vuota_se_non_ci_riconosciamo(client, monkeypatch):
    monkeypatch.setattr(ds, "self_name", lambda: "")
    assert client.get("/api/meta").json()["internal_base_url"] == ""
