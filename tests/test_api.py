def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_sessione_senza_autenticazione(client):
    data = client.get("/api/session").json()
    assert data["auth_enabled"] is False
    assert data["authenticated"] is True


def test_overview(client):
    data = client.get("/api/overview").json()
    assert data["counts"] == {"total": 2, "running": 1, "stopped": 1, "paused": 0, "unhealthy": 0}
    assert sorted(c["name"] for c in data["containers"]) == ["db", "web"]
    assert data["stacks"][0]["name"] == "blog"
    assert data["stacks"][0]["running"] == 1


def test_ciclo_vita_categoria(client):
    created = client.post("/api/categories", json={"name": "Media", "containers": ["web"], "icon": "🎬"})
    assert created.status_code == 201
    category_id = created.json()["id"]

    duplicate = client.post("/api/categories", json={"name": "Media"})
    assert duplicate.status_code == 409

    listed = client.get("/api/categories").json()
    media = next(c for c in listed if c["id"] == category_id)
    assert media["containers"] == ["web"]
    assert media["running"] == 1 and media["total"] == 1

    patched = client.patch(f"/api/categories/{category_id}", json={"containers": ["web", "db"]})
    assert patched.status_code == 200
    media = next(c for c in patched.json()["categories"] if c["id"] == category_id)
    assert sorted(media["containers"]) == ["db", "web"]
    assert media["running"] == 1 and media["total"] == 2

    acted = client.post(f"/api/categories/{category_id}/action", json={"action": "stop"})
    assert acted.status_code == 200
    assert acted.json()["status"] == "ok"
    assert sorted(acted.json()["targets"]) == ["db", "web"]

    assert client.delete(f"/api/categories/{category_id}").status_code == 200
    assert all(c["id"] != category_id for c in client.get("/api/categories").json())


def test_azione_su_container_e_audit(client):
    res = client.post("/api/actions", json={"action": "restart", "targets": ["web"]})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    log = client.get("/api/activity?limit=5").json()
    assert log[0]["action"] == "restart"
    assert log[0]["target"] == "web"
    assert log[0]["status"] == "ok"


def test_azione_non_valida(client):
    assert client.post("/api/actions", json={"action": "formatta", "targets": ["web"]}).status_code == 422
    assert client.post("/api/actions", json={"action": "up", "targets": ["web"]}).status_code == 400


def test_azione_su_stack(client):
    res = client.post("/api/actions", json={"action": "stop", "target_kind": "stack", "target_ref": "blog"})
    assert res.status_code == 200
    assert sorted(res.json()["targets"]) == ["db", "web"]


def test_ciclo_vita_pianificazione(client):
    payload = {
        "name": "Spegni di notte",
        "action": "stop",
        "target_kind": "containers",
        "targets": ["web"],
        "trigger_kind": "cron",
        "cron_expr": "0 2 * * *",
    }
    created = client.post("/api/schedules", json=payload)
    assert created.status_code == 201
    schedule_id = created.json()["id"]

    schedule = next(s for s in created.json()["schedules"] if s["id"] == schedule_id)
    assert schedule["next_run"] is not None
    assert schedule["target_label"] == "web"

    toggled = client.post(f"/api/schedules/{schedule_id}/toggle").json()["schedules"]
    assert next(s for s in toggled if s["id"] == schedule_id)["enabled"] is False

    executed = client.post(f"/api/schedules/{schedule_id}/run").json()["schedules"]
    # disattivata: l'esecuzione manuale non fa nulla e non incrementa il contatore
    assert next(s for s in executed if s["id"] == schedule_id)["run_count"] == 0

    assert client.post(f"/api/schedules/{schedule_id}/toggle").status_code == 200
    ran = client.post(f"/api/schedules/{schedule_id}/run").json()["schedules"]
    entry = next(s for s in ran if s["id"] == schedule_id)
    assert entry["run_count"] == 1
    assert entry["last_status"] == "ok"

    assert client.delete(f"/api/schedules/{schedule_id}").status_code == 200


def test_run_at_sopravvive_al_round_trip_su_sqlite(client):
    """SQLite scarta il fuso orario: l'orario deve restare lo stesso istante
    anche dopo essere stato riletto dal database."""
    created = client.post("/api/schedules", json={
        "name": "Una tantum", "action": "start", "target_kind": "all",
        "trigger_kind": "once", "run_at": "2030-01-15T22:40:00",  # ora locale Europe/Rome
    })
    assert created.status_code == 201
    schedule_id = created.json()["id"]

    # nuova richiesta = nuova sessione = valore riletto dal file SQLite
    reread = next(s for s in client.get("/api/schedules").json() if s["id"] == schedule_id)
    assert reread["run_at"] == "2030-01-15T21:40:00Z"  # 22:40 CET == 21:40 UTC
    assert reread["next_run"] == "2030-01-15T21:40:00Z"

    client.delete(f"/api/schedules/{schedule_id}")


def test_pianificazione_valida_input(client):
    senza_target = client.post("/api/schedules", json={
        "name": "x", "action": "stop", "target_kind": "containers", "targets": [],
        "trigger_kind": "cron", "cron_expr": "0 2 * * *"})
    assert senza_target.status_code == 400

    cron_rotto = client.post("/api/schedules", json={
        "name": "x", "action": "stop", "target_kind": "all",
        "trigger_kind": "cron", "cron_expr": "pippo"})
    assert cron_rotto.status_code == 400


def test_anteprima_pianificazione(client):
    ok = client.post("/api/schedules/preview", json={
        "name": "p", "action": "start", "target_kind": "all",
        "trigger_kind": "cron", "cron_expr": "*/30 * * * *"}).json()
    assert ok["valid"] is True
    assert len(ok["next_runs"]) == 5

    ko = client.post("/api/schedules/preview", json={
        "name": "p", "action": "start", "target_kind": "all",
        "trigger_kind": "cron", "cron_expr": "boh"}).json()
    assert ko["valid"] is False
    assert ko["next_runs"] == []


def test_assegnazione_categorie_a_container(client):
    first = client.post("/api/categories", json={"name": "Infra"}).json()["id"]
    second = client.post("/api/categories", json={"name": "Backup"}).json()["id"]

    client.post("/api/categories/assign", json={"container": "web", "category_ids": [first, second]})
    categories = {c["id"]: c for c in client.get("/api/categories").json()}
    assert categories[first]["containers"] == ["web"]
    assert categories[second]["containers"] == ["web"]

    client.post("/api/categories/assign", json={"container": "web", "category_ids": [second]})
    categories = {c["id"]: c for c in client.get("/api/categories").json()}
    assert categories[first]["containers"] == []
    assert categories[second]["containers"] == ["web"]
