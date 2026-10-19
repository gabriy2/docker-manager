#!/usr/bin/env python3
"""Aggiorna le versioni pinnate del progetto.

- `requirements.txt`: ultima release stabile di ogni pacchetto su PyPI
- `Dockerfile`: ultima versione di docker CLI (binari statici) e del plugin compose

Usa solo la libreria standard: nessuna dipendenza da installare in CI.

    python scripts/bump_versions.py            # applica le modifiche
    python scripts/bump_versions.py --check    # solo report, exit 1 se ci sono novità
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = [ROOT / "requirements.txt", ROOT / "requirements-dev.txt"]
DOCKERFILE = ROOT / "Dockerfile"

REQ_LINE = re.compile(
    r"^(?P<name>[A-Za-z0-9._-]+)(?P<extras>\[[^\]]+\])?==(?P<version>[^\s;#]+)(?P<rest>.*)$"
)
PRERELEASE = re.compile(r"(rc|a|b|alpha|beta|dev|pre)", re.IGNORECASE)


def fetch(url: str) -> bytes:
    headers = {"User-Agent": "docker-manager-bump/1.0", "Accept": "application/json"}
    token = os.getenv("GITHUB_TOKEN", "")
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def version_key(value: str) -> tuple:
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r"[.\-+]", value)[:4])


def latest_pypi(name: str) -> str | None:
    try:
        data = json.loads(fetch(f"https://pypi.org/pypi/{name}/json"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        print(f"  ! {name}: interrogazione PyPI non riuscita ({exc})", file=sys.stderr)
        return None
    version = data.get("info", {}).get("version")
    return None if version and PRERELEASE.search(version) else version


def latest_docker_cli() -> str | None:
    try:
        page = fetch("https://download.docker.com/linux/static/stable/x86_64/").decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"  ! docker CLI: elenco non raggiungibile ({exc})", file=sys.stderr)
        return None
    found = re.findall(r"docker-(\d+\.\d+\.\d+)\.tgz", page)
    return max(set(found), key=version_key) if found else None


def latest_compose() -> str | None:
    try:
        data = json.loads(fetch("https://api.github.com/repos/docker/compose/releases/latest"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        print(f"  ! compose: release non raggiungibile ({exc})", file=sys.stderr)
        return None
    tag = data.get("tag_name", "")
    return tag if tag.startswith("v") and not PRERELEASE.search(tag) else None


def update_requirements(path: Path, changes: list[str]) -> str:
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = REQ_LINE.match(line.strip())
        if not match:
            out.append(line)
            continue
        name, extras, current = match["name"], match["extras"] or "", match["version"]
        latest = latest_pypi(name)
        if latest and latest != current:
            changes.append(f"{name}: {current} → {latest}")
            out.append(f"{name}{extras}=={latest}{match['rest']}")
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def update_dockerfile(changes: list[str]) -> str:
    content = DOCKERFILE.read_text(encoding="utf-8")
    for arg, getter, label in (
        ("DOCKER_CLI_VERSION", latest_docker_cli, "docker CLI"),
        ("COMPOSE_VERSION", latest_compose, "docker compose"),
    ):
        latest = getter()
        if not latest:
            continue
        pattern = re.compile(rf"^(ARG {arg}=)(\S+)$", re.MULTILINE)
        match = pattern.search(content)
        if match and match.group(2) != latest:
            changes.append(f"{label}: {match.group(2)} → {latest}")
            content = pattern.sub(rf"\g<1>{latest}", content, count=1)
    return content


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="non scrivere nulla, solo report")
    args = parser.parse_args()

    changes: list[str] = []
    print("Controllo dipendenze Python…")
    new_requirements = {path: update_requirements(path, changes) for path in REQUIREMENTS if path.exists()}
    print("Controllo versioni docker CLI e compose…")
    new_dockerfile = update_dockerfile(changes)

    if not changes:
        print("Tutto già aggiornato.")
        _emit_summary([])
        return 0

    print("\nAggiornamenti disponibili:")
    for change in changes:
        print(f"  - {change}")

    if not args.check:
        for path, content in new_requirements.items():
            path.write_text(content, encoding="utf-8")
        DOCKERFILE.write_text(new_dockerfile, encoding="utf-8")
        print("\nFile aggiornati.")

    _emit_summary(changes)
    return 1 if args.check else 0


def _emit_summary(changes: list[str]) -> None:
    """Espone l'elenco a GitHub Actions (output `changes` e riepilogo del job)."""
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        body = "\n".join(f"- {c}" for c in changes)
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"has_changes={'true' if changes else 'false'}\n")
            handle.write(f"changes<<CHANGES_EOF\n{body}\nCHANGES_EOF\n")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            if changes:
                handle.write("### Aggiornamenti settimanali\n\n")
                handle.write("\n".join(f"- {c}" for c in changes) + "\n")
            else:
                handle.write("### Aggiornamenti settimanali\n\nNessuna novità.\n")


if __name__ == "__main__":
    raise SystemExit(main())
