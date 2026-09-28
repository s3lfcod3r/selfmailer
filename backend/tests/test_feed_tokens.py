"""Befunde 1-3 und 14 (Durchsicht 2026-09-27): Feed-Token.

Geprueft wird:
  1. Token per Header "X-Feed-Token" (Vorrang) und weiterhin per Query.
  2. Ein SCHREIB-Token auf dem Lesepfad wird akzeptiert, aber hoechstens
     einmal je Token und Stunde protokolliert - ohne den Token-Wert.
  3. In der DB liegt nur der SHA-256-Hash; Klartext genau einmal (Erzeugen/
     Rotieren); expires_at/last_used_at; Bestandstoken ueberleben die
     Migration.
 14. Das Rate-Limit ist grosszuegig (120/min je Token).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging

import pytest
from sqlalchemy import text

from app.core.db import engine

_PW = "feed-supersecret-123"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _nutzer(client, admin, name: str) -> dict:
    """Legt (idempotent) einen Nicht-Admin an und gibt dessen Bearer-Header."""
    client.post("/api/v1/admin/users", headers=admin, json={
        "username": name, "password": _PW, "display_name": name, "role": "user",
    })  # 201 oder 409 - beides ok
    r = client.post("/api/v1/auth/login", json={"username": name, "password": _PW})
    assert r.status_code == 200, r.text
    client.cookies.clear()  # rein ueber Bearer testen (wie die admin-Fixture)
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def _frischer_lesetoken(client, kopf: dict) -> str:
    """Rotieren liefert den Klartext garantiert (auch wenn schon einer existiert)."""
    r = client.post("/api/v1/feeds/token/rotate", headers=kopf)
    assert r.status_code == 200, r.text
    roh = r.json()["token"]
    assert roh
    return roh


def _frischer_schreibtoken(client, kopf: dict) -> str:
    r = client.post("/api/v1/feeds/write-token/rotate", headers=kopf)
    assert r.status_code == 200, r.text
    roh = r.json()["write_token"]
    assert roh
    return roh


def _spalte(hashwert: str, spalte: str):
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT " + spalte + " FROM feedtoken WHERE token = :h"),
            {"h": hashwert},
        ).scalar()


@pytest.fixture()
def leser(client, admin) -> dict:
    return _nutzer(client, admin, "feedleser@self")


def test_db_speichert_nur_den_hash_und_zeigt_klartext_nur_einmal(client, leser):
    roh = _frischer_lesetoken(client, leser)

    # Zweiter Blick auf denselben Token: kein Klartext mehr, nur noch Metadaten.
    r = client.get("/api/v1/feeds/token", headers=leser)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"] == "", "Klartext darf nur beim Erzeugen/Rotieren kommen"
    assert body["has_token"] is True
    assert body["expires_at"], "Ablaufdatum fehlt"
    assert body["calendar_url"] == ""

    with engine.begin() as conn:
        alle = [row[0] for row in conn.execute(text("SELECT token FROM feedtoken"))]
    assert roh not in alle, "Klartext-Token steht in der DB"
    assert _hash(roh) in alle, "Hash des Tokens fehlt in der DB"


def test_token_wirkt_per_header_und_per_query(client, leser):
    roh = _frischer_lesetoken(client, leser)

    # Rueckfall Query (Handy-Kalender, heutige SelfDashboard-Kachel).
    r = client.get("/api/v1/dashboard/summary", params={"token": roh})
    assert r.status_code == 200, r.text

    # Neuer Weg: Header - so landet der Token nicht in der Request-Line.
    r = client.get("/api/v1/dashboard/summary", headers={"X-Feed-Token": roh})
    assert r.status_code == 200, r.text

    # Header hat VORRANG: gueltiger Header + Muell im Query bleibt gueltig ...
    r = client.get("/api/v1/dashboard/summary", params={"token": "falsch"},
                   headers={"X-Feed-Token": roh})
    assert r.status_code == 200, r.text
    # ... und ein falscher Header gewinnt ebenfalls (kein stiller Rueckfall).
    r = client.get("/api/v1/dashboard/summary", params={"token": roh},
                   headers={"X-Feed-Token": "falsch"})
    assert r.status_code == 401


def test_rotieren_macht_den_alten_token_ungueltig(client, leser):
    alt = _frischer_lesetoken(client, leser)
    assert client.get("/api/v1/dashboard/summary",
                      headers={"X-Feed-Token": alt}).status_code == 200
    neu = _frischer_lesetoken(client, leser)
    assert neu != alt
    assert client.get("/api/v1/dashboard/summary",
                      headers={"X-Feed-Token": alt}).status_code == 401
    assert client.get("/api/v1/dashboard/summary",
                      headers={"X-Feed-Token": neu}).status_code == 200


def test_abgelaufener_token_wird_abgelehnt(client, leser):
    roh = _frischer_lesetoken(client, leser)
    frueher = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE feedtoken SET expires_at = :e WHERE token = :h"),
            {"e": frueher, "h": _hash(roh)},
        )
    r = client.get("/api/v1/dashboard/summary", headers={"X-Feed-Token": roh})
    assert r.status_code == 401


def test_last_used_at_wird_gedrosselt(client, leser):
    roh = _frischer_lesetoken(client, leser)
    h = _hash(roh)
    assert _spalte(h, "last_used_at") is None
    assert client.get("/api/v1/dashboard/summary",
                      headers={"X-Feed-Token": roh}).status_code == 200
    erst = _spalte(h, "last_used_at")
    assert erst is not None, "last_used_at wurde nie gesetzt"
    # Zweiter Aufruf sofort danach: KEIN weiterer Schreibvorgang (die Kachel
    # pollt etwa 11-mal pro Minute, jeder Write waere eine SQLite-Transaktion).
    assert client.get("/api/v1/dashboard/summary",
                      headers={"X-Feed-Token": roh}).status_code == 200
    assert _spalte(h, "last_used_at") == erst


def test_schreibtoken_auf_lesepfad_warnt_einmal_ohne_tokenwert(client, admin, caplog):
    kopf = _nutzer(client, admin, "feedschreiber@self")
    schreib = _frischer_schreibtoken(client, kopf)

    with caplog.at_level(logging.WARNING, logger="app.api.feeds"):
        r = client.get("/api/v1/dashboard/summary", headers={"X-Feed-Token": schreib})
        assert r.status_code == 200, "Schreib-Token muss auf dem Lesepfad weiter gehen"
        treffer = [rec for rec in caplog.records
                   if "Schreib-Token auf Lesepfad benutzt" in rec.getMessage()]
        assert len(treffer) == 1, "genau eine Warnung erwartet"
        assert schreib not in treffer[0].getMessage(), "Token-Wert im Log"
        assert _hash(schreib) not in treffer[0].getMessage()

        # Zweiter Aufruf innerhalb der Stunde: keine zweite Warnung.
        assert client.get("/api/v1/dashboard/summary",
                          headers={"X-Feed-Token": schreib}).status_code == 200
        treffer = [rec for rec in caplog.records
                   if "Schreib-Token auf Lesepfad benutzt" in rec.getMessage()]
        assert len(treffer) == 1, "Warnung darf nur 1x je Token und Stunde kommen"


def test_lesetoken_darf_nicht_schreiben(client, admin):
    kopf = _nutzer(client, admin, "feedschreibpfad@self")
    lese = _frischer_lesetoken(client, kopf)
    schreib = _frischer_schreibtoken(client, kopf)

    r = client.put("/api/v1/calendar/hidden", json={"keys": []},
                   headers={"X-Feed-Token": lese})
    assert r.status_code == 401, "Lese-Token (steckt in Abo-URLs) darf nicht schreiben"
    r = client.put("/api/v1/calendar/hidden", json={"keys": []},
                   headers={"X-Feed-Token": schreib})
    assert r.status_code == 200, r.text


def test_bestandstoken_ueberlebt_die_migration(client, admin):
    """Befund 3, Rueckwaertskompatibilitaet: ein Klartext-Token aus 1.95.x wird
    beim Start gehasht UEBERNOMMEN - der eingerichtete Abo-Link und die Kachel
    funktionieren unveraendert weiter, nur anzeigen kann man ihn nicht mehr."""
    from app.core.db import _migrate_feed_tokens_to_hash

    kopf = _nutzer(client, admin, "feedaltbestand@self")
    weg = _frischer_lesetoken(client, kopf)  # erzeugt die Zeile

    # Zustand von 1.95.5 nachstellen: Klartext in der Spalte, kein Ablaufdatum.
    alt = "AltBestandsToken-1234567890abcXYZ"
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE feedtoken SET token = :t, expires_at = NULL WHERE token = :h"),
            {"t": alt, "h": _hash(weg)},
        )

    _migrate_feed_tokens_to_hash()

    with engine.begin() as conn:
        alle = [row[0] for row in conn.execute(text("SELECT token FROM feedtoken"))]
    assert alt not in alle, "Klartext nach Migration noch in der DB"
    assert _hash(alt) in alle

    # Wichtigster Punkt: der alte Token funktioniert weiter.
    r = client.get("/api/v1/dashboard/summary", params={"token": alt})
    assert r.status_code == 200, r.text

    # Und er hat ein Ablaufdatum bekommen (Migrationszeit + 180 Tage).
    roh_exp = _spalte(_hash(alt), "expires_at")
    assert roh_exp is not None
    exp = roh_exp if isinstance(roh_exp, dt.datetime) else dt.datetime.fromisoformat(str(roh_exp))
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=dt.timezone.utc)
    tage = (exp - dt.datetime.now(dt.timezone.utc)).days
    assert 178 <= tage <= 180, tage

    # Zweiter Lauf darf nichts kaputt machen (idempotent, falls user_version fehlt).
    _migrate_feed_tokens_to_hash()
    assert client.get("/api/v1/dashboard/summary",
                      params={"token": alt}).status_code == 200


def test_rate_limit_ist_grosszuegig_aber_vorhanden(client, admin):
    """Befund 14: 120 Aufrufe je Minute und Token - die Kachel schafft ~11."""
    from app.api.feeds import FEED_RATE_LIMIT

    assert FEED_RATE_LIMIT >= 120
    kopf = _nutzer(client, admin, "feedlimit@self")
    roh = _frischer_lesetoken(client, kopf)
    kopfzeilen = {"X-Feed-Token": roh}
    for i in range(FEED_RATE_LIMIT):
        r = client.get("/api/v1/dashboard/summary", headers=kopfzeilen)
        assert r.status_code == 200, "Aufruf " + str(i + 1) + ": " + r.text
    r = client.get("/api/v1/dashboard/summary", headers=kopfzeilen)
    assert r.status_code == 429
    assert r.headers.get("Retry-After")

def test_nutzung_verschiebt_den_ablauf(client, leser):
    """Ein benutzter Token (Kachel, Kalender-Abo) laeuft nicht nach 180 Tagen
    fest ab - jede Nutzung schiebt das Ablaufdatum nach vorn."""
    roh = _frischer_lesetoken(client, leser)
    h = _hash(roh)
    bald = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE feedtoken SET expires_at = :e, last_used_at = NULL WHERE token = :h"),
            {"e": bald, "h": h},
        )
    assert client.get("/api/v1/dashboard/summary",
                      headers={"X-Feed-Token": roh}).status_code == 200
    neu = _spalte(h, "expires_at")
    if isinstance(neu, str):
        neu = dt.datetime.fromisoformat(neu)
    if neu.tzinfo is None:
        neu = neu.replace(tzinfo=dt.timezone.utc)
    assert neu > dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=170)
