"""Pytest-Setup: frische Temp-DB + TestClient + Admin-Auth-Fixtures.

Wichtig: ENV muss VOR dem Import von app gesetzt sein (config validiert das
Secret beim Import, die DB-Engine wird mit dem Pfad erzeugt).
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("SELFMAILER_SECRET", "test-secret-this-is-long-enough-1234567890")
os.environ["SELFMAILER_SYNC_DISABLE"] = "1"  # kein Hintergrund-Sync in Tests
_DB = os.path.join(tempfile.gettempdir(), "selfmailer_pytest.db")
os.environ["SELFMAILER_DB_PATH"] = _DB
for _ext in ("", "-wal", "-shm"):
    try:
        os.remove(_DB + _ext)
    except FileNotFoundError:
        pass

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

# Rate-Limiter in Tests deaktivieren (sonst kippen wiederholte Logins auf 429).
import app.api.auth as _auth  # noqa: E402

_auth.check_rate_limit = lambda *a, **k: None  # type: ignore[assignment]

# SSRF-Pruefung in Tests neutralisieren: die Test-URLs (z. B. ntfy.example.com)
# sind Fakes, die in einer CI-Umgebung nicht aufloesen und sonst — voellig
# korrekt — als "nicht erlaubt" (400) abgelehnt wuerden. Der SSRF-Schutz selbst
# ist nicht Gegenstand dieser Endpunkt-Tests.
import app.api.push as _push  # noqa: E402

_push.validate_external_url = lambda *a, **k: None  # type: ignore[assignment]

init_db()

_ADMIN = {"username": "admin@self", "password": "supersecret-123", "display_name": "Admin"}


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin(client) -> dict:
    """Stellt den Admin sicher (Setup beim ersten Mal, sonst Login) und gibt den Auth-Header."""
    status = client.get("/api/v1/auth/status").json()
    if status.get("needs_setup"):
        r = client.post("/api/v1/auth/setup", json=_ADMIN)
        assert r.status_code == 201, r.text
        token = r.json()["access_token"]
    else:
        r = client.post("/api/v1/auth/login", json={"username": _ADMIN["username"], "password": _ADMIN["password"]})
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
    # Das Login/Setup setzt zusaetzlich ein httpOnly-Session-Cookie. Im session-
    # scoped TestClient bliebe es im Cookie-Jar haengen und wuerde die
    # "requires_auth"-Tests (bewusst OHNE Bearer) faelschlich authentifizieren.
    # Wir testen rein ueber den Bearer-Header -> Cookie hier verwerfen.
    client.cookies.clear()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def account(client, admin) -> int:
    """Legt ein (nicht erreichbares) Mailkonto an und gibt dessen id zurück."""
    r = client.post("/api/v1/accounts", headers=admin, json={
        "label": "Test", "email": "test@example.com", "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com", "password": "pw",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]
