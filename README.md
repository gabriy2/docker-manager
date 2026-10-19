# Docker Manager

Pannello web per **avviare, fermare, riavviare e pianificare** i container Docker di un host,
con **raggruppamento per categorie** per fare accensioni e spegnimenti di massa e supporto agli
**stack compose**.

Un solo container, nessun database esterno, nessuna dipendenza da CDN: l'interfaccia funziona
anche su una macchina senza accesso a Internet.

```
┌────────────────────────────────────────────────────────────────────┐
│  DockerManager        ● Docker 29.7     [ cerca… ]   Live  ⟳  ☾    │
├──────────────┬─────────────────────────────────────────────────────┤
│ Container 12 │  12 container   8 in esecuzione   4 fermi   1 ⚡     │
│ Categorie  4 │                                                     │
│ Pianific.  3 │  🎬 Media            6/6 attivi   ▶ Avvia  ■ Ferma  │
│ Attività     │   ● jellyfin     nginx:alpine   attivo 3g 4h  ▮▮▯   │
│              │   ● sonarr       …                                  │
│ Stato        │  🗄️ Database         2/3 attivi   ▶ Avvia  ■ Ferma  │
│ Categorie    │   ○ postgres     fermo da 2h                        │
│ Stack        │                                                     │
└──────────────┴─────────────────────────────────────────────────────┘
                        ┌──────────────────────────────────┐
                        │ 3 selezionati  ▶ ■ ⟳ ⏸ │ 🕑 🏷 ✕ │
                        └──────────────────────────────────┘
```

## Funzionalità

| | |
|---|---|
| **Azioni** | avvio, arresto, riavvio, pausa/ripresa su singolo container, su selezione multipla, su categoria, su stack o su tutti |
| **Categorie** | gruppi liberi e personalizzabili (nome, icona, colore) con pulsanti «avvia tutti» / «ferma tutti»; un container può stare in più categorie |
| **Stack** | rilevamento automatico dei progetti `docker compose` dalle label, con azioni di gruppo e, opzionalmente, `compose up -d` / `compose down` reali |
| **Pianificazione** | giornaliera, settimanale, mensile, a intervalli, espressione cron libera oppure una-tantum su data/ora; anteprima delle prossime esecuzioni |
| **Metriche live** | CPU, RAM, uptime, stato di health, porte pubblicate con link cliccabile |
| **Log** | ultime righe di log di ogni container direttamente dal pannello |
| **Storico** | registro di tutte le azioni, manuali e pianificate, con esito |
| **Interfaccia** | tema chiaro/scuro, ricerca, filtri, responsive, tutto via web — nessun comando da terminale |

Tutte le funzionalità sono disponibili anche via **API REST** documentata in `/api/docs`.

## Avvio rapido

### docker compose (consigliato)

```yaml
services:
  docker-manager:
    image: ghcr.io/<tuo-utente>/docker-manager:latest
    container_name: docker-manager
    restart: unless-stopped
    ports:
      - "8420:8000"
    environment:
      TZ: Europe/Rome
      AUTH_USER: admin
      AUTH_PASSWORD: cambiami
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - docker-manager-data:/data

volumes:
  docker-manager-data:
```

```bash
docker compose up -d
```

### docker run

```bash
docker run -d --name docker-manager \
  -p 8420:8000 \
  -e TZ=Europe/Rome \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v docker-manager-data:/data \
  --restart unless-stopped \
  ghcr.io/<tuo-utente>/docker-manager:latest
```

Poi apri **http://localhost:8420**.

### Build locale

```bash
git clone https://github.com/<tuo-utente>/docker-manager.git
cd docker-manager
docker compose up -d --build
```

## Configurazione

Tutto si configura con variabili d'ambiente; nessun file di configurazione da modificare.

| Variabile | Default | Descrizione |
|---|---|---|
| `TZ` | `Europe/Rome` | fuso orario usato da **tutte** le pianificazioni |
| `AUTH_USER` | `admin` | utente dell'interfaccia |
| `AUTH_PASSWORD` | *(vuoto)* | se vuoto **l'interfaccia è aperta**; impostala per attivare il login |
| `SESSION_DAYS` | `14` | durata del cookie di sessione |
| `SECRET_KEY` | *(generata)* | chiave per firmare la sessione; se assente viene creata in `/data/secret.key` |
| `PORT` | `8000` | porta interna del server |
| `DATA_DIR` | `/data` | cartella del database SQLite (da persistere!) |
| `STOP_TIMEOUT` | `10` | secondi di grazia prima del `SIGKILL` sullo stop |
| `ENABLE_STATS` | `true` | raccolta metriche CPU/RAM |
| `STATS_INTERVAL` | `8` | secondi tra due raccolte di metriche |
| `STATS_WORKERS` | `8` | letture di metriche in parallelo |
| `COMPOSE_CLI` | `false` | abilita `compose up/down` sugli stack (vedi sotto) |
| `HIDE_SELF` | `false` | nasconde il container del pannello dall'elenco |
| `LOG_RETENTION` | `500` | voci di storico conservate |
| `DOCKER_HOST` | *(socket)* | per usare un socket proxy o un daemon remoto, es. `tcp://docker-proxy:2375` |

### Pianificazioni

Dalla scheda **Pianificazioni** → *Nuova pianificazione* scegli:

1. **cosa fare** — avvia / ferma / riavvia / pausa / riprendi (e `compose up`/`down` per gli stack);
2. **su cosa** — container selezionati, una categoria, uno stack, oppure tutti;
3. **quando** — a orario (giornaliero, settimanale, mensile o cron libero), a intervalli, o una volta sola.

L'anteprima mostra in tempo reale le prossime tre esecuzioni, così eviti sorprese.
Le pianificazioni sono salvate nel database e ricostruite ad ogni riavvio del container;
quelle una-tantum si disattivano da sole dopo l'esecuzione.

Esempio tipico: *«ogni notte alle 2:00 ferma la categoria **Media**»* e *«ogni mattina alle 7:30 riavviala»*.

### Stack compose

Gli stack vengono riconosciuti automaticamente dalle label `com.docker.compose.project`:
avvio, arresto e riavvio di uno stack funzionano sempre, senza configurazione.

Per usare anche `compose up -d` e `compose down` reali (che **creano e rimuovono** i container)
servono due cose:

```yaml
environment:
  COMPOSE_CLI: "true"
volumes:
  # i file compose devono essere visibili con lo STESSO percorso dell'host
  - /opt/stacks:/opt/stacks:ro
```

Se la cartella del progetto non è montata, il pannello lo dice chiaramente invece di fallire a metà.

Gli stack visti almeno una volta vengono memorizzati: dopo un `compose down` lo stack resta
in elenco, marcato come *rimosso*, con il pulsante **compose up** per ricrearlo (`compose down`
cancella i container e con essi le label da cui si ricava dove sta il file compose). Se uno stack
non ti serve più, il pulsante **Dimentica** lo toglie dall'elenco senza toccare nulla sull'host.

## Sicurezza

Montare `/var/run/docker.sock` dà al container il controllo completo del daemon Docker:
chi accede al pannello può fare tutto quello che fa il pannello. Di conseguenza:

- **imposta `AUTH_PASSWORD`** se il pannello è raggiungibile da altri dispositivi;
- esponilo dietro un reverse proxy con HTTPS (il server gestisce `X-Forwarded-Proto`);
- se vuoi ridurre la superficie, usa un **socket proxy** in sola lettura sulle API necessarie
  (es. [`tecnativa/docker-socket-proxy`](https://github.com/Tecnativa/docker-socket-proxy)) e
  punta `DOCKER_HOST` al proxy — servono `CONTAINERS=1`, `POST=1`, `INFO=1`, `VERSION=1`;
- il pannello non si spegne mai da solo durante un'azione di massa (categoria, stack, «tutti»).

## Sviluppo

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
DATA_DIR=./data uvicorn app.main:app --reload --port 8000

ruff check .     # lint
pytest -q        # test (non serve un daemon Docker: è simulato)
```

Struttura:

```
app/
  main.py            avvio FastAPI, lifespan, static
  config.py          variabili d'ambiente
  db.py / models.py  SQLite + SQLAlchemy (categorie, pianificazioni, storico)
  docker_service.py  tutto il dialogo col daemon Docker
  actions.py         risoluzione dei target ed esecuzione delle azioni
  scheduler.py       APScheduler, trigger, anteprime
  routers/           API REST
  static/            interfaccia (HTML + CSS + JS, zero dipendenze)
```

## Aggiornamenti automatici settimanali

Il repository contiene tre automazioni GitHub Actions:

| Workflow | Quando | Cosa fa |
|---|---|---|
| `ci.yml` | push e pull request | lint, test e build dell'immagine |
| `publish.yml` | push su `main` e tag `v*` | build multi-arch (amd64 + arm64) e push su GHCR |
| `weekly-update.yml` | **ogni lunedì alle 04:00 UTC** | aggiorna le dipendenze Python e le versioni di docker CLI/compose, lancia i test e apre una pull request; in ogni caso ricostruisce e ripubblica l'immagine per incorporare le patch dell'immagine base |

In più `dependabot.yml` apre PR settimanali per `pip`, le GitHub Actions e l'immagine base.

Per avere anche il **container già in esecuzione** sempre aggiornato, abilita il profilo
Watchtower incluso nel `docker-compose.yml`:

```bash
docker compose --profile autoupdate up -d
```

Aggiorna `docker-manager` ogni lunedì alle 04:30 scaricando l'ultima immagine pubblicata.

### Primo setup del repository

```bash
gh repo create docker-manager --private --source=. --remote=origin --push
```

Non serve creare segreti: i workflow usano il `GITHUB_TOKEN` automatico. Verifica solo che in
*Settings → Actions → General* siano attivi **Read and write permissions** e
**Allow GitHub Actions to create and approve pull requests**.

## Licenza

MIT — vedi [LICENSE](LICENSE).
