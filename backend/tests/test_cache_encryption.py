"""Mail-Cache verschluesselt (1.97.2).

Betreff, Absender, Vorschau und Volltext lagen im Klartext in der SQLite-Datei
und damit in jedem Backup. Seit 1.97.2 legt EncryptedText sie Fernet-
verschluesselt ab; Migration v5 stellt den Bestand um.
"""
from __future__ import annotations

import itertools
import json

from sqlalchemy import select, text
from sqlmodel import Session

from app.core import db as db_mod
from app.core.crypto import SEALED_PREFIX
from app.core.db import engine
from app.core.encrypted_types import open_text, seal_text
from app.models import CachedMessage

BETREFF = "Kuendigung Mietvertrag 4711"
ABSENDER = '"Vermieter Huber" <huber@example.com>'
VORSCHAU = "Sehr geehrter Herr, hiermit kuendige ich"
VOLLTEXT = json.dumps({"text": "Geheimer Volltext 0815", "html": ""})
SPALTEN = ("subject", "from_addr", "snippet", "detail_json")


def _roh(row_id: int) -> dict[str, str]:
    with engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT {', '.join(SPALTEN)} FROM cachedmessage WHERE id = :id"), {"id": row_id}
        ).one()
    return dict(zip(SPALTEN, row))


def _orm(row_id: int) -> CachedMessage:
    with Session(engine) as s:
        row = s.get(CachedMessage, row_id)
        assert row is not None
        s.expunge(row)
        return row


_uids = itertools.count(1)


def _anlegen(account_id: int, **werte) -> int:
    with Session(engine) as s:
        row = CachedMessage(account_id=account_id, folder="INBOX-enc-test", uid=str(next(_uids)), **werte)
        s.add(row)
        s.commit()
        return row.id


def _klartext_einfuegen(account_id: int, **werte) -> int:
    """Altbestand wie vor 1.97.2: direkt per SQL, am TypeDecorator vorbei."""
    row_id = _anlegen(account_id)
    if werte:
        setzen = ", ".join(f"{k} = :{k}" for k in werte)
        with engine.begin() as conn:
            conn.execute(text(f"UPDATE cachedmessage SET {setzen} WHERE id = :id"), {**werte, "id": row_id})
    return row_id


def _aufraeumen() -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM cachedmessage WHERE folder = 'INBOX-enc-test'"))


# --- seal_text / open_text -----------------------------------------------------

def test_text_versiegeln_und_oeffnen():
    v = seal_text(BETREFF)
    assert v.startswith(SEALED_PREFIX) and BETREFF not in v
    assert open_text(v) == BETREFF
    assert seal_text(BETREFF) != v  # jedes Schreiben neu verschluesselt


def test_leer_bleibt_leer_und_klartext_bleibt_lesbar():
    assert seal_text("") == ""
    assert open_text("") == "" and open_text(None) is None
    assert open_text("alter Klartext") == "alter Klartext"


def test_unlesbarer_wert_wird_leer():
    assert open_text(SEALED_PREFIX + "kaputt") == ""


# --- ORM ----------------------------------------------------------------------

def test_cache_liegt_verschluesselt_in_der_datei(account):
    try:
        rid = _anlegen(account, subject=BETREFF, from_addr=ABSENDER, snippet=VORSCHAU, detail_json=VOLLTEXT)
        roh = _roh(rid)
        for spalte, klartext in zip(SPALTEN, (BETREFF, ABSENDER, VORSCHAU, VOLLTEXT)):
            assert roh[spalte].startswith(SEALED_PREFIX), spalte
            assert klartext not in roh[spalte], spalte
        row = _orm(rid)
        assert (row.subject, row.from_addr, row.snippet, row.detail_json) == (BETREFF, ABSENDER, VORSCHAU, VOLLTEXT)
    finally:
        _aufraeumen()


def test_leerer_volltext_und_filter_funktionieren(account):
    try:
        offen = _anlegen(account, subject="a", detail_json=VOLLTEXT)
        nie_geoeffnet = _anlegen(account, subject="b")
        assert _roh(nie_geoeffnet)["detail_json"] == ""
        with Session(engine) as s:
            ids = set(s.exec(select(CachedMessage.id).where(
                CachedMessage.folder == "INBOX-enc-test", CachedMessage.detail_json != "")).scalars())
        assert ids == {offen}
    finally:
        _aufraeumen()


def test_altbestand_im_klartext_bleibt_lesbar(account):
    try:
        rid = _klartext_einfuegen(account, subject=BETREFF, from_addr=ABSENDER, snippet=VORSCHAU)
        row = _orm(rid)
        assert (row.subject, row.from_addr, row.snippet, row.detail_json) == (BETREFF, ABSENDER, VORSCHAU, "")
    finally:
        _aufraeumen()


# --- Migration v5 ---------------------------------------------------------------

def test_migration_verschluesselt_altbestand(account, monkeypatch):
    monkeypatch.setattr(db_mod, "_CACHE_SEAL_BATCH", 2)  # mehrere Pakete durchlaufen
    try:
        alt = [_klartext_einfuegen(account, subject=f"{BETREFF} {i}", from_addr=ABSENDER,
                                   snippet=VORSCHAU, detail_json=VOLLTEXT if i % 2 else "")
               for i in range(5)]
        neu = _anlegen(account, subject="schon neu")
        vorher = _roh(neu)

        with engine.begin() as conn:
            conn.execute(text("PRAGMA user_version = 4"))
        db_mod._run_one_time_backfills()

        with engine.connect() as conn:
            assert int(conn.execute(text("PRAGMA user_version")).scalar()) >= 5
            assert int(conn.exec_driver_sql("PRAGMA secure_delete").scalar()) == 0
        for i, rid in enumerate(alt):
            roh = _roh(rid)
            assert all(BETREFF not in v and "huber@example.com" not in v for v in roh.values())
            assert roh["subject"].startswith(SEALED_PREFIX)
            assert roh["detail_json"] == ("" if i % 2 == 0 else roh["detail_json"])
            row = _orm(rid)
            assert row.subject == f"{BETREFF} {i}" and row.from_addr == ABSENDER
            assert row.detail_json == (VOLLTEXT if i % 2 else "")
        assert _roh(neu) == vorher  # schon verschluesselt: nicht doppelt

        # Zweiter Lauf aendert nichts mehr.
        stand = {rid: _roh(rid) for rid in alt}
        db_mod._seal_cache_texts()
        assert {rid: _roh(rid) for rid in alt} == stand
    finally:
        _aufraeumen()
