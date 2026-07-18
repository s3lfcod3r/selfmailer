"""SelfMailer FastAPI-App. Bedient API für WebUI und (später) APK.

Wenn ein gebautes Frontend unter ../frontend/dist liegt, wird es als Static-
SPA mitausgeliefert (Single-Container-Deployment).
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .core.config import get_settings
from .core.db import init_db
from .api import (
    accounts,
    admin,
    admin_accounts,
    auth,
    calendar,
    contacts,
    dashboard,
    dav,
    events,
    feeds,
    mail,
    notes,
    push,
    rules,
    tasks,
    translate,
)
# Alias: das Modul heißt settings, die Konfigurationsvariable unten aber auch —
# ohne Umbenennung würde die Variable das Modul überschreiben.
from .api import settings as settings_api

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Event-Loop dem Live-Sync-Bus geben (für thread-sicheres publish).
    import asyncio

    from .events import bus
    bus.set_loop(asyncio.get_running_loop())
    from .mail.scheduler import start_scheduler, stop_scheduler
    start_scheduler()  # hält den Cache im Hintergrund warm -> UI wartet nie auf IMAP
    try:
        yield
    finally:
        stop_scheduler()


# Eine einzige Versionsquelle — muss zum README-Badge und der UI passen.
APP_VERSION = "1.12.0"

# Öffentliche API-Docs (Swagger/ReDoc/OpenAPI-Schema) in Produktion abschalten —
# reduziert die Angriffsfläche/Info-Preisgabe; die WebUI/APK brauchen sie nicht.
app = FastAPI(
    title=settings.app_name,
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Hard-Limit fürs rohe Request. Große Uploads (Anhänge) werden anhand von
# Content-Length früh abgewiesen, BEVOR der Body in den Speicher gelesen wird.
_MAX_REQUEST_BYTES = 30 * 1024 * 1024  # ~30 MB


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    """Weist zu große Requests anhand des Content-Length-Headers ab (413), bevor
    der Body gelesen wird — schützt vor Speicher-Spitzen durch riesige Uploads."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_REQUEST_BYTES:
                return Response(
                    "Anfrage zu groß",
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )
        except ValueError:
            pass  # unlesbarer Header -> normal weiterreichen
    return await call_next(request)


# CSP für die App-Shell (die ausgelieferte React-SPA). Bewusst konservativ, damit
# die App NICHT bricht:
#  - default/script/connect 'self' (gehashte Vite-Assets + eigene API, same-origin);
#  - style-src 'unsafe-inline' (React setzt Inline-Styles; Vite kann Style-Tags injizieren);
#  - img/font data: (Icons/Inline-Bilder), img auch https: (externe Bilder nach Freigabe);
#  - frame-src 'self' + frame-ancestors 'none' (Clickjacking-Schutz, ergänzt X-Frame-Options);
#  - object-src 'none', base-uri 'self'.
# Die Mail-Vorschau selbst ist ein sandboxed srcdoc-iframe mit EIGENER, strengerer
# CSP im srcdoc — die hier gesetzte Header-CSP betrifft sie nicht.
_APP_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Setzt defensive Response-Header. Bewusst KEIN HSTS (TLS wird extern
    terminiert; http-Zugriff im LAN soll möglich bleiben). Die App-Shell bekommt
    eine bewusst nachsichtige CSP (siehe _APP_CSP); die Mail-Vorschau nutzt ein
    sandboxed srcdoc-iframe mit eigener, strengerer CSP im srcdoc."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", _APP_CSP)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(admin_accounts.router)
app.include_router(accounts.router)
app.include_router(mail.router)
app.include_router(rules.router)
app.include_router(notes.router)
app.include_router(tasks.router)
app.include_router(calendar.router)
app.include_router(contacts.router)
app.include_router(feeds.router)
app.include_router(dav.router)
app.include_router(dashboard.router)
app.include_router(push.router)
app.include_router(events.router)
app.include_router(translate.router)
app.include_router(settings_api.router)


# Build-Marker: erlaubt von außen zu prüfen, welche Version wirklich LÄUFT
# (Image gezogen != Container neu erstellt). Bei jedem relevanten Deploy erhöhen.
APP_BUILD = "2026-07-03-dav-account-edit"


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "version": APP_VERSION, "build": APP_BUILD}


# Optionales Static-Frontend (Produktion). Der Pfad unterscheidet sich je nach
# Umgebung: lokal liegt main.py unter backend/app/ (zwei Ebenen bis zum Repo-
# Root), im Container kopiert das Dockerfile nur den backend-Inhalt nach /app,
# sodass main.py unter /app/app/ liegt (nur eine Ebene bis /app). Beide
# Kandidaten prüfen und den ersten existierenden mounten.
_here = os.path.dirname(__file__)
_dist_candidates = [
    os.path.join(_here, "..", "..", "frontend", "dist"),  # lokal: repo-root/frontend/dist
    os.path.join(_here, "..", "frontend", "dist"),        # container: /app/frontend/dist
]
_dist = next((d for d in _dist_candidates if os.path.isdir(d)), None)
if _dist:
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
