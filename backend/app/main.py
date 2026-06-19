"""SelfMailer FastAPI-App. Bedient API fuer WebUI und (spaeter) APK.

Wenn ein gebautes Frontend unter ../frontend/dist liegt, wird es als Static-
SPA mitausgeliefert (Single-Container-Deployment).
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
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
    dav,
    feeds,
    mail,
    notes,
    rules,
    tasks,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "version": "0.1.0"}


# Optionales Static-Frontend (Produktion). Der Pfad unterscheidet sich je nach
# Umgebung: lokal liegt main.py unter backend/app/ (zwei Ebenen bis zum Repo-
# Root), im Container kopiert das Dockerfile nur den backend-Inhalt nach /app,
# sodass main.py unter /app/app/ liegt (nur eine Ebene bis /app). Beide
# Kandidaten pruefen und den ersten existierenden mounten.
_here = os.path.dirname(__file__)
_dist_candidates = [
    os.path.join(_here, "..", "..", "frontend", "dist"),  # lokal: repo-root/frontend/dist
    os.path.join(_here, "..", "frontend", "dist"),        # container: /app/frontend/dist
]
_dist = next((d for d in _dist_candidates if os.path.isdir(d)), None)
if _dist:
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
