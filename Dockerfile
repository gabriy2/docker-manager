# syntax=docker/dockerfile:1.7

# --------------------------------------------------------------------------- #
# Stage 1 — CLI docker + plugin compose (serve solo per le azioni up/down)
# --------------------------------------------------------------------------- #
FROM debian:bookworm-slim AS dockercli

ARG TARGETARCH
ARG DOCKER_CLI_VERSION=29.9.1
ARG COMPOSE_VERSION=v5.6.0

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    rm -rf /var/lib/apt/lists/*; \
    mkdir -p /out/cli-plugins; \
    case "${TARGETARCH}" in \
      amd64)  STATIC_ARCH=x86_64;  PLUGIN_ARCH=x86_64 ;; \
      arm64)  STATIC_ARCH=aarch64; PLUGIN_ARCH=aarch64 ;; \
      arm)    STATIC_ARCH=armhf;   PLUGIN_ARCH=armv7 ;; \
      *) echo "architettura non supportata: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://download.docker.com/linux/static/stable/${STATIC_ARCH}/docker-${DOCKER_CLI_VERSION}.tgz" -o /tmp/docker.tgz; \
    tar -xzf /tmp/docker.tgz -C /tmp docker/docker; \
    install -m 0755 /tmp/docker/docker /out/docker; \
    curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${PLUGIN_ARCH}" \
      -o /out/cli-plugins/docker-compose; \
    chmod 0755 /out/cli-plugins/docker-compose; \
    /out/docker --version

# --------------------------------------------------------------------------- #
# Stage 2 — dipendenze Python
# --------------------------------------------------------------------------- #
FROM python:3.14-slim AS deps

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r /tmp/requirements.txt

# --------------------------------------------------------------------------- #
# Stage 3 — immagine finale
# --------------------------------------------------------------------------- #
FROM python:3.14-slim

LABEL org.opencontainers.image.title="Docker Manager" \
      org.opencontainers.image.description="Avvio, spegnimento, raggruppamento per categorie e pianificazione dei container Docker da interfaccia web" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DATA_DIR=/data \
    TZ=Europe/Rome \
    PORT=8000

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends tzdata; \
    rm -rf /var/lib/apt/lists/*

COPY --from=deps /opt/venv /opt/venv
COPY --from=dockercli /out/docker /usr/local/bin/docker
COPY --from=dockercli /out/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

WORKDIR /app
COPY app ./app

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"8000\")}/api/health', timeout=4).status==200 else 1)"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
