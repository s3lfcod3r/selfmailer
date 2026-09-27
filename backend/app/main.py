"""SelfMailer FastAPI-App. Bedient API für WebUI und (später) APK.

Wenn ein gebautes Frontend unter ../frontend/dist liegt, wird es als Static-
SPA mitausgeliefert (Single-Container-Deployment).
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .core.config import get_settings
from .core.db import init_db
from .core.logfilter import install_secret_log_filter
from .mail import timing as mail_timing
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
    identities,
    mail,
    notes,
    labels,
    push,
    rules,
    schedule,
    tasks,
    templates,
    translate,
    vacation,
)
# Alias: das Modul heißt settings, die Konfigurationsvariable unten aber auch —
# ohne Umbenennung würde die Variable das Modul überschreiben.
from .api import settings as settings_api

settings = get_settings()

# Geheimnisse aus den Logzeilen maskieren (Befund 1). Zweimal aufrufen ist
# Absicht und idempotent: hier beim Import (uvicorn hat sein Logging da schon
# per dictConfig gesetzt, unsere Filter am Logger ueberleben das) und noch
# einmal im Lifespan-Start, falls die App vor der Logging-Konfiguration
# importiert wurde (Tests, gunicorn, uvicorn --reload).
install_secret_log_filter()

# Reverse-Proxy-Betrieb (Befund 23): ein zu weiter CORS-Eintrag hebelt die
# Same-Origin-Grenze aus, und weil allow_credentials=True gesetzt ist, wuerde
# ein fremdes Origin das Session-Cookie mitschicken duerfen. Starlette lehnt
# "*" zusammen mit Credentials selbst ab; alles andere pruefen wir hier.
_CORS_FORBIDDEN = {"*", "null", "http://*", "https://*"}


def _check_cors_origins(origins: list[str]) -> list[str]:
    """Verwirft unsichere CORS-Origins und meldet sie laut im Log.

    Erlaubt sind nur exakte Origins der Form schema://host[:port] - kein
    Wildcard, kein Pfad, kein leerer Eintrag. Bewusst nur WARNEN und den
    Eintrag fallen lassen (nicht den Start abbrechen): SelfMailer soll nach
    einem Konfigurationsfehler erreichbar bleiben, statt still nicht mehr zu
    starten.
    """
    ok: list[str] = []
    for raw in origins:
        origin = (raw or "").strip()
        if not origin:
            continue
        low = origin.lower()
        bad = (
            low in _CORS_FORBIDDEN
            or "*" in low
            or not (low.startswith("http://") or low.startswith("https://"))
            or low.rstrip("/").count("/") != 2
        )
        if bad:
            _cors_logger.warning(
                "CORS-Origin verworfen: %r. Erlaubt sind nur exakte Origins wie "
                "https://mail.example.org - niemals Wildcards.",
                origin,
            )
            continue
        ok.append(origin.rstrip("/"))
    return ok


_cors_logger = logging.getLogger("selfmailer.cors")
_cors_origins = _check_cors_origins(list(settings.cors_list or []))


@asynccontextmanager
async def lifespan(app: FastAPI):
    install_secret_log_filter()
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


# Server-Version: mit README-Badge, Git-Tag und frontend/package*.json abstimmen.
# Die WebUI zeigt sie über /api/health an. Reine Backend-Releases brauchen keine
# neue APK; dann die kompatible Android-Version ausdrücklich im Release nennen.
APP_VERSION = "1.95.5"

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
async def measure_mail_request(request: Request, call_next):
    target = mail_timing.classify(request.method, request.url.path)
    if target is None:
        return await call_next(request)
    with mail_timing.request_scope(*target) as trace:
        response = await call_next(request)
        if trace is not None:
            trace.status = response.status_code
        return response


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    """Begrenzt die Rohgröße eines Requests — schützt vor Speicher-Spitzen.

    1. Mit ``Content-Length``: früh mit 413 abweisen, bevor der Body gelesen wird.
    2. OHNE ``Content-Length`` (``Transfer-Encoding: chunked``): die Bytes beim Lesen
       mitzählen und den Body bei Überschreitung abschneiden. Die App bekommt dann
       einen unvollständigen Body und lehnt ihn ab (4xx) — statt dass ein riesiger
       chunked-Upload das Content-Length-Limit umgeht und den RAM vollpuffert.
    """
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
    else:
        received = 0
        original_receive = request._receive

        async def _capped_receive():
            nonlocal received
            message = await original_receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b"") or b"")
                if received > _MAX_REQUEST_BYTES:
                    # Nicht weiter puffern: Body hier beenden → unvollständig → App lehnt ab.
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        request._receive = _capped_receive
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
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    # X-Feed-Token (Befund 1): der Feed-Token darf statt im Query-String im
    # Header stehen. Ohne diesen Eintrag wuerde ein Browser-Client (Dashboard-
    # Kachel auf fremdem Origin) am CORS-Preflight scheitern.
    allow_headers=["Authorization", "Content-Type", "X-Feed-Token"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(admin_accounts.router)
app.include_router(accounts.router)
app.include_router(mail.router)
app.include_router(rules.router)
app.include_router(notes.router)
app.include_router(templates.router)
app.include_router(identities.router)
app.include_router(vacation.router)
app.include_router(labels.router)
app.include_router(schedule.router)
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
APP_BUILD = "2026-09-08-v1.95.5"


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
