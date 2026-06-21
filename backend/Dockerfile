# --- Stage 1: Frontend bauen ---
FROM node:24-alpine AS frontend
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Backend + gebautes Frontend ---
FROM python:3.13-slim AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 SELFMAILER_DB_PATH=/data/selfmailer.db
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=frontend /fe/dist ./frontend/dist

# Non-Root-Betrieb: Unraid-freundliche IDs (nobody:users = 99:100). /data und
# /app gehoeren dem Laufzeit-User, damit die SQLite-DB im gemounteten Volume
# beschreibbar bleibt. Gruppe 100 ("users") existiert im Debian-Basisimage.
RUN useradd -u 99 -g 100 -M -s /usr/sbin/nologin appuser \
    && mkdir -p /data \
    && chown -R 99:100 /data /app
USER appuser

VOLUME ["/data"]
EXPOSE 8090

# Healthcheck ueber den /api/health-Endpoint (kein curl im slim-Image noetig).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/api/health', timeout=4).status==200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
