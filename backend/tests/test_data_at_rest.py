"""Daten im Ruhezustand (Durchsicht 2026-10-24).

1. Geplante Mails: payload_json (Body, BCC, Anhaenge) lag im Klartext in DB und
   Backups und blieb bei failed/uncertain fuer immer liegen.
2. Backups: lagen mit umask-Rechten (meist 644) fuer jeden Host-Nutzer lesbar.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.core import backup as backup_mod
from app.core import db as db_mod
from app.core.crypto import SEALED_PREFIX, open_json, seal_json
from app.core.db import engine
from app.mail import scheduler
from app.models import ScheduledMail

GEHEIM = "Streng vertraulicher Inhalt 4711"
ANHANG = base64.b64encode(b"Anhang-Klartext-0815").decode("ascii")


# --- seal_json / open_json --------------------------------------------------

def test_versiegeln_und_oeffnen():
    roh = {"body": GEHEIM, "bcc": ["geheim@example.com"]}
    versiegelt = seal_json(roh)
    assert versiegelt.startswith(SEALED_PREFIX)
    assert GEHEIM not in versiegelt and "geheim@example.com" not in versiegelt
    assert open_json(versiegelt) == roh


def test_altbestand_im_klartext_bleibt_lesbar():
    assert open_json(json.dumps({"body": "alt"})) == {"body": "alt"}
    assert open_json("") == {}
    assert open_json(None) == {}


def test_manipulierter_payload_wird_abgelehnt():
    with pytest.raises(ValueError):
        open_json(SEALED_PREFIX + "kaputt")


# --- API + Scheduler ------------------------------------------------------------

def _zeile(row_id: int) -> ScheduledMail:
    with Session(engine) as s:
        row = s.get(ScheduledMail, row_id)
        assert row is not None
        s.expunge(row)
        return row


def test_api_speichert_geplante_mail_verschluesselt(client, admin, account):
    send_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat()
    r = client.post(f"/api/v1/mail/{account}/schedule", headers=admin, json={
        "to": ["empf@example.com"], "bcc": ["versteckt@example.com"], "subject": "Plan",
        "body": GEHEIM, "send_at": send_at,
        "attachments": [{"filename": "a.txt", "content_type": "text/plain", "content_b64": ANHANG}],
    })
    assert r.status_code == 201, r.text
    row = _zeile(r.json()["id"])
    assert row.payload_json.startswith(SEALED_PREFIX)
    for klartext in (GEHEIM, "versteckt@example.com", ANHANG):
        assert klartext not in row.payload_json
    daten = open_json(row.payload_json)
    assert daten["body"] == GEHEIM and daten["bcc"] == ["versteckt@example.com"]
    # Die Liste unter "Geplant" funktioniert weiter (Betreff/Empfaenger bleiben lesbar).
    liste = client.get("/api/v1/schedule", headers=admin).json()
    assert any(e["id"] == row.id and e["subject"] == "Plan" and e["to"] == ["empf@example.com"] for e in liste)
    client.delete(f"/api/v1/schedule/{row.id}", headers=admin)


def _faellige_mail(account_id: int, payload: str) -> int:
    with Session(engine) as s:
        user_id = s.exec(text("SELECT user_id FROM mailaccount WHERE id = :id").bindparams(id=account_id)).scalar_one()
        row = ScheduledMail(
            user_id=user_id, account_id=account_id, subject="Faellig", to_addrs="empf@example.com",
            payload_json=payload, send_at=dt.datetime(2000, 1, 1), status="pending",
        )
        s.add(row)
        s.commit()
        return row.id


def test_scheduler_entschluesselt_und_leert_nach_versand(account, monkeypatch):
    row_id = _faellige_mail(account, seal_json({"to": ["empf@example.com"], "subject": "S", "body": GEHEIM}))
    gesehen: list[dict] = []

    async def fake_send(acc, pw, **kw):
        gesehen.append(kw)
        return b"raw"

    monkeypatch.setattr(scheduler.smtp_mod, "send_message", fake_send)
    monkeypatch.setattr(scheduler.imap_mod, "save_to_sent", lambda *a, **k: None)
    scheduler._send_due_scheduled()
    assert any(k["body"] == GEHEIM for k in gesehen)
    row = _zeile(row_id)
    assert row.status == "sent" and row.payload_json == ""


def test_scheduler_liest_alten_klartext_payload(account, monkeypatch):
    row_id = _faellige_mail(account, json.dumps({"to": ["empf@example.com"], "body": "alt"}))
    gesehen: list[dict] = []

    async def fake_send(acc, pw, **kw):
        gesehen.append(kw)
        return b"raw"

    monkeypatch.setattr(scheduler.smtp_mod, "send_message", fake_send)
    monkeypatch.setattr(scheduler.imap_mod, "save_to_sent", lambda *a, **k: None)
    scheduler._send_due_scheduled()
    assert any(k["body"] == "alt" for k in gesehen)
    assert _zeile(row_id).status == "sent"


def test_fehlgeschlagener_versand_leert_den_inhalt(account, monkeypatch):
    row_id = _faellige_mail(account, seal_json({"to": ["empf@example.com"], "body": GEHEIM}))

    async def kaputt(*a, **k):
        raise TimeoutError("synthetisch")

    monkeypatch.setattr(scheduler.smtp_mod, "send_message", kaputt)
    scheduler._send_due_scheduled()
    row = _zeile(row_id)
    assert row.status == "uncertain" and row.payload_json == ""
    assert row.subject == "Faellig"  # Anzeige bleibt


def test_neustart_waehrend_versand_leert_den_inhalt(account):
    row_id = _faellige_mail(account, seal_json({"body": GEHEIM}))
    with Session(engine) as s:
        s.exec(text("UPDATE scheduledmail SET status='sending' WHERE id=:id").bindparams(id=row_id))
        s.commit()
    scheduler.recover_interrupted_sends()
    row = _zeile(row_id)
    assert row.status == "uncertain" and row.payload_json == ""


# --- Migration v4 ---------------------------------------------------------------

def test_migration_verschluesselt_offene_und_leert_abgeschlossene(account):
    alt = json.dumps({"to": ["empf@example.com"], "body": GEHEIM})
    ids = {}
    for status in ("pending", "sent", "failed", "uncertain", "sending"):
        rid = _faellige_mail(account, alt)
        with Session(engine) as s:
            s.exec(text("UPDATE scheduledmail SET status=:st, send_at=:t WHERE id=:id").bindparams(
                st=status, t=dt.datetime(2999, 1, 1), id=rid))
            s.commit()
        ids[status] = rid
    schon_versiegelt = _faellige_mail(account, seal_json({"body": "x"}))
    vorher = _zeile(schon_versiegelt).payload_json

    with engine.begin() as conn:
        conn.execute(text("PRAGMA user_version = 3"))
    db_mod._run_one_time_backfills()

    with engine.connect() as conn:
        assert int(conn.execute(text("PRAGMA user_version")).scalar()) >= 4
        # Die Migration laesst secure_delete nicht an der Verbindung haengen.
        assert int(conn.exec_driver_sql("PRAGMA secure_delete").scalar()) == 0
    pending = _zeile(ids["pending"]).payload_json
    assert pending.startswith(SEALED_PREFIX) and GEHEIM not in pending
    assert open_json(pending)["body"] == GEHEIM
    for status in ("sent", "failed", "uncertain"):
        assert _zeile(ids[status]).payload_json == "", status
    assert _zeile(ids["sending"]).payload_json == alt  # recover_interrupted_sends kuemmert sich
    assert _zeile(schon_versiegelt).payload_json == vorher  # idempotent, nicht doppelt

    with Session(engine) as s:
        s.exec(text("DELETE FROM scheduledmail WHERE send_at = :t OR id = :id").bindparams(
            t=dt.datetime(2999, 1, 1), id=schon_versiegelt))
        s.commit()


# --- Backup-Rechte --------------------------------------------------------------

posix_only = pytest.mark.skipif(os.name == "nt", reason="chmod-Rechte gibt es nur unter POSIX")


class _Settings:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.backup_keep = 7
        self.backup_enabled = True


@pytest.fixture()
def quell_db(tmp_path, monkeypatch) -> Path:
    pfad = tmp_path / "selfmailer.db"
    con = sqlite3.connect(str(pfad))
    con.execute("CREATE TABLE t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    monkeypatch.setattr(backup_mod, "get_settings", lambda: _Settings(str(pfad)))
    return pfad


def _modus(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


@posix_only
def test_backup_ordner_700_und_datei_600(quell_db):
    alt_umask = os.umask(0o022)
    try:
        snapshot = backup_mod.create_backup()
    finally:
        os.umask(alt_umask)
    assert snapshot is not None
    assert _modus(snapshot.parent) == 0o700
    assert _modus(snapshot) == 0o600


@posix_only
def test_vorhandene_backups_werden_nachgezogen(quell_db):
    ordner = quell_db.parent / "backups"
    ordner.mkdir(mode=0o755)
    alt = ordner / "selfmailer-20000101-000000.db"
    alt.write_bytes(b"")
    os.chmod(alt, 0o644)
    backup_mod.create_backup()
    assert _modus(ordner) == 0o700
    assert _modus(alt) == 0o600


def test_backup_bleibt_gueltige_datenbank(quell_db):
    """Die vorab leer angelegte Datei darf das Backup nicht verderben."""
    snapshot = backup_mod.create_backup()
    con = sqlite3.connect(f"file:{snapshot.as_posix()}?mode=ro", uri=True)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT x FROM t").fetchall() == [(1,)]
    finally:
        con.close()


def test_abgebrochenes_backup_hinterlaesst_keine_halbe_datei(quell_db, monkeypatch):
    echt = sqlite3.connect
    aufrufe = []

    def connect(ziel, *a, **k):
        aufrufe.append(ziel)
        if len(aufrufe) == 2:  # 1. = Quelle (read-only), 2. = Ziel
            raise sqlite3.OperationalError("synthetisch: Platte voll")
        return echt(ziel, *a, **k)

    monkeypatch.setattr(backup_mod.sqlite3, "connect", connect)
    with pytest.raises(sqlite3.OperationalError):
        backup_mod.create_backup()
    assert list((quell_db.parent / "backups").glob("selfmailer-*.db")) == []
