"""Registro degli stack compose: uno stack spento con `compose down` non ha più
container (né label), ma deve restare visibile e ricreabile dal pannello."""

from app import docker_service as ds


def test_stack_memorizzato_resta_visibile_e_ricreabile(client, monkeypatch):
    # prima lettura: lo stack "blog" viene registrato a partire dalle label
    assert any(s["name"] == "blog" for s in client.get("/api/stacks").json())

    # ora simuliamo un `compose down`: nessun container residuo
    monkeypatch.setattr(ds, "list_containers", lambda: [])

    blog = next(s for s in client.get("/api/stacks").json() if s["name"] == "blog")
    assert blog["removed"] is True
    assert blog["total"] == 0
    assert blog["has_compose"] is True

    # il contesto compose memorizzato permette di ricostruire il comando
    from app.db import SessionLocal
    from app.stacks import context

    with SessionLocal() as db:
        assert context(db, "blog") == ("/srv/blog", ["/srv/blog/docker-compose.yml"])

    # e si può dimenticare esplicitamente
    assert client.delete("/api/stacks/blog").status_code == 200
    assert all(s["name"] != "blog" for s in client.get("/api/stacks").json())


def test_non_si_dimentica_uno_stack_ancora_attivo(client):
    client.get("/api/stacks")  # assicura che "blog" sia registrato
    res = client.delete("/api/stacks/blog")
    assert res.status_code == 409
    assert "container" in res.json()["detail"]


def test_compose_action_usa_il_contesto_memorizzato(monkeypatch, tmp_path):
    """Con un contesto esplicito non serve interrogare il daemon."""
    monkeypatch.setattr(ds, "compose_cli_available", lambda: True)

    def esplodi(_project):
        raise AssertionError("non deve ricadere sulle label dei container")

    monkeypatch.setattr(ds, "_compose_context", esplodi)

    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    eseguiti = []

    class Esito:
        returncode = 0
        stdout = "Container ricreato"
        stderr = ""

    monkeypatch.setattr(ds.subprocess, "run", lambda cmd, **_kw: (eseguiti.append(cmd), Esito())[1])

    result = ds.compose_action("blog", "up", (str(tmp_path), [str(compose_file)]))
    assert result["status"] == "ok"
    assert eseguiti[0][:4] == ["docker", "compose", "--project-name", "blog"]
    assert eseguiti[0][-2:] == ["up", "--detach"]
    assert str(compose_file) in eseguiti[0]
