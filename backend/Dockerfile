# --- Stage 1: Frontend bauen ---
FROM node:24-alpine AS frontend
WORKDIR /fe
COPY frontend/package*.json ./
# Nur npm ci: der frueher angehaengte "|| npm install"-Fallback hat bei einem
# kaputten Lockfile leise andere Versionen gezogen (Befund 19). Jetzt soll der
# Build fehlschlagen, damit das Lockfile gilt, das auditiert wurde.
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Backend + gebautes Frontend ---
FROM python:3.13-slim AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 SELFMAILER_DB_PATH=/data/selfmailer.db
COPY backend/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY backend/ ./
COPY --from=frontend /fe/dist ./frontend/dist

# Non-Root-Betrieb: Unraid-freundliche IDs (nobody:users = 99:100). NUR /data
# gehoert dem Laufzeit-User (SQLite-DB, Backups). Der App-Code unter /app
# bleibt root-eigen und damit fuer den laufenden Prozess schreibgeschuetzt
# (seit 1.97.0; vorher chown -R auch auf /app) - eine Luecke im Code kann so
# den Code selbst nicht dauerhaft veraendern. Python legt dann keine
# __pycache__-Dateien unter /app an, das ist harmlos.
# Gruppe 100 ("users") existiert im Debian-Basisimage.
RUN useradd -u 99 -g 100 -M -s /usr/sbin/nologin appuser \
    && mkdir -p /data \
    && chown -R 99:100 /data
ENV PYTHONDONTWRITEBYTECODE=1
USER appuser

VOLUME ["/data"]
# 8090 = HTTP (immer). 8443 = optionales HTTPS, nur aktiv mit
# SELFMAILER_TLS_CERT/SELFMAILER_TLS_KEY (siehe app/serve.py, docs/HTTPS.md).
EXPOSE 8090 8443

# Healthcheck ueber den /api/health-Endpoint (kein curl im slim-Image noetig).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/api/health', timeout=4).status==200 else 1)"]

# app.serve startet uvicorn mit Proxy-Header-Unterstuetzung und optional einem
# zweiten HTTPS-Listener. Ohne TLS-Variablen verhaelt es sich wie bisher
# (HTTP auf 8090), nur ohne "server: uvicorn"-Header.
CMD ["python", "-m", "app.serve"]
