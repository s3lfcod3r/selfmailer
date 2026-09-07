"""C2 — Cache-Duplikate: Serialisierung, UNIQUE-Index und Entdoppelung.

Vor dem Fix konnten zwei Syncs desselben Ordners parallel laufen (der
Verbindungs-Pool von 3+2 macht das real): beide lasen denselben Cache-Stand,
bestimmten dieselben "neuen" UIDs und legten die Mail DOPPELT an — es gab weder
UNIQUE-Constraint noch Upsert. Folge: Mail doppelt in der Liste, Ungelesen-Badge
zu hoch (zählt Zeilen, nicht distinct UIDs).

Drei Ebenen abgesichert:
  1. Sperre je (Konto, Ordner) serialisiert Syncs — verschiedene Ordner nicht.
  2. UNIQUE-Index (account_id, folder, uid) als hartes Sicherheitsnetz.
  3. Einmalige Entdoppelung von Alt-Beständen, ohne Nutzer-Zustand zu verlieren.
"""
from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, select

from app.core import db as db_mod
from app.mail import cache as cache_mod
from app.models import CachedMessage, MailAccount


def _konto(acc_id: int = 4242) -> MailAccount:
    return MailAccount(id=acc_id, email="sven@example.org", imap_host="imap.example.org")


# ---------------------------------------------------------------------------
# 1) Die Sperre serialisiert je (Konto, Ordner) — verschiedene Ordner nicht.
# ---------------------------------------------------------------------------
def _lauf_gleichzeitig(monkeypatch, jobs):
    """Ruft cache.sync_folder für jede (account, folder)-Paarung nebenläufig auf
    und misst die maximale gleichzeitige Verschachtelung der Kernfunktion."""
    aktiv = 0
    max_aktiv = 0
    zaehl_lock = threading.Lock()

    def fake_unlocked(session, account, password, folder, cap=0, *,
                      store_bodies=True, lock_timeout=None, op="sync"):
        nonlocal aktiv, max_aktiv
        with zaehl_lock:
            aktiv += 1
            max_aktiv = max(max_aktiv, aktiv)
        time.sleep(0.15)
        with zaehl_lock:
            aktiv -= 1
        return {"ok": True}

    monkeypatch.setattr(cache_mod, "_sync_folder_unlocked", fake_unlocked)

    threads = [
        threading.Thread(target=cache_mod.sync_folder,
                         args=(None, acc, "pw", folder))
        for acc, folder in jobs
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return max_aktiv


def test_gleicher_ordner_wird_serialisiert(monkeypatch):
    acc = _konto()
    max_aktiv = _lauf_gleichzeitig(monkeypatch, [(acc, "INBOX")] * 3)
    assert max_aktiv == 1, (
        f"drei Syncs desselben Ordners liefen {max_aktiv}-fach gleichzeitig — "
        "die Sperre greift nicht, Duplikate wären wieder möglich"
    )


def test_verschiedene_ordner_blockieren_sich_nicht(monkeypatch):
    acc = _konto()
    max_aktiv = _lauf_gleichzeitig(
        monkeypatch, [(acc, "INBOX"), (acc, "Gesendet"), (acc, "Archiv")]
    )
    assert max_aktiv >= 2, (
        "verschiedene Ordner wurden serialisiert — die Sperre ist zu grob "
        "(muss je Ordner getrennt sein)"
    )


def test_verschiedene_konten_blockieren_sich_nicht(monkeypatch):
    max_aktiv = _lauf_gleichzeitig(
        monkeypatch, [(_konto(1), "INBOX"), (_konto(2), "INBOX"), (_konto(3), "INBOX")]
    )
    assert max_aktiv >= 2, "gleicher Ordnername verschiedener Konten wurde serialisiert"


# ---------------------------------------------------------------------------
# 2) Der UNIQUE-Index weist ein echtes Duplikat ab (Sicherheitsnetz).
# ---------------------------------------------------------------------------
def test_unique_index_weist_duplikat_ab(client):
    """init_db() (in conftest) hat den UNIQUE-Index angelegt. Ein zweites INSERT
    derselben (account_id, folder, uid) muss scheitern."""
    from sqlalchemy.exc import IntegrityError

    acc_id = 991001
    with Session(db_mod.engine) as s:
        s.add(CachedMessage(account_id=acc_id, folder="INBOX", uid="7"))
        s.commit()
    try:
        with pytest.raises(IntegrityError):
            with Session(db_mod.engine) as s:
                s.add(CachedMessage(account_id=acc_id, folder="INBOX", uid="7"))
                s.commit()
    finally:
        with Session(db_mod.engine) as s:
            for row in s.exec(
                select(CachedMessage).where(CachedMessage.account_id == acc_id)
            ).all():
                s.delete(row)
            s.commit()


# ---------------------------------------------------------------------------
# 3) Die Entdoppelung behält die Zeile mit Nutzer-Zustand (hidden/seen_sticky).
#    Getestet auf einer Wegwerf-DB mit EXAKT den Statements aus db.py.
# ---------------------------------------------------------------------------
def test_dedup_behaelt_nutzerzustand_und_index_wird_moeglich():
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text(
            "CREATE TABLE cachedmessage ("
            "id INTEGER PRIMARY KEY, account_id INTEGER, folder TEXT, uid TEXT, "
            "hidden INTEGER DEFAULT 0, seen_sticky INTEGER DEFAULT 0)"
        ))
        # Gruppe A: drei Dubletten, die mittlere trägt hidden=1 → sie muss bleiben.
        c.execute(text("INSERT INTO cachedmessage (id,account_id,folder,uid,hidden,seen_sticky) VALUES"
                       " (1,1,'INBOX','10',0,0),"
                       " (2,1,'INBOX','10',1,0),"
                       " (3,1,'INBOX','10',0,0)"))
        # Gruppe B: zwei Dubletten ohne Zustand → kleinste id (4) bleibt.
        c.execute(text("INSERT INTO cachedmessage (id,account_id,folder,uid,hidden,seen_sticky) VALUES"
                       " (4,1,'INBOX','11',0,0),(5,1,'INBOX','11',0,0)"))
        # Eindeutige Zeile bleibt unangetastet.
        c.execute(text("INSERT INTO cachedmessage (id,account_id,folder,uid) VALUES (6,1,'Archiv','10')"))

    with eng.begin() as c:
        c.execute(text(db_mod._DEDUP_CACHE_SQL))
        # Muss NACH der Entdoppelung fehlerfrei anlegbar sein.
        c.execute(text(db_mod._UNIQUE_CM_INDEX_SQL))

    with eng.connect() as c:
        rows = c.execute(text(
            "SELECT id, uid, hidden FROM cachedmessage WHERE folder='INBOX' ORDER BY uid"
        )).all()
    # Je (acc,folder,uid) genau eine Zeile; für uid=10 die hidden-Zeile (id 2).
    assert [(r[1], r[0]) for r in rows] == [("10", 2), ("11", 4)], rows
    assert any(r[0] == 2 and r[2] == 1 for r in rows), "hidden-Zeile wurde verworfen"
