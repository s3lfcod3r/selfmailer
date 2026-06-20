"""Hintergrund-Sync: haelt den lokalen Cache dauerhaft WARM.

Idee: Ein Daemon-Thread synct in Intervallen je Konto den INBOX (neue Koepfe,
geloeschte raus, Flags) und die Ordnerzaehler in die DB. Dadurch kommt die WebUI
immer SOFORT aus dem Cache und muss nie auf einen langsamen IMAP-Provider warten.

Bewusst SYNCHRON in einem eigenen Thread (passt zu imap_tools/SQLite). Je Konto
eine eigene kurze Session; ein defektes/langsames Konto kippt den Lauf nie. Per
Env steuerbar:
  SELFMAILER_SYNC_DISABLE=1     -> aus
  SELFMAILER_SYNC_INTERVAL=300  -> Sekunden zwischen den Laeufen (Default 300)
"""
from __future__ import annotations

import os
import threading

from sqlmodel import Session, select

from ..core.crypto import decrypt
from ..core.db import engine
from ..models import MailAccount
from . import cache as cache_mod
from . import imap as imap_mod

_INTERVAL = max(60, int(os.getenv("SELFMAILER_SYNC_INTERVAL", "300") or 300))
_WARM_FOLDERS = ["INBOX"]          # Ordner, deren NACHRICHTEN warmgehalten werden
_STARTUP_DELAY = 15.0              # nicht direkt beim Boot loslegen

_started = False
_thread: threading.Thread | None = None
_stop = threading.Event()


def _sync_account(acc: MailAccount) -> None:
    """Einen Account warmhalten: INBOX-Nachrichten + alle Ordnerzaehler."""
    try:
        pw = decrypt(acc.secret_enc)
    except Exception:  # noqa: BLE001 - z. B. Schluessel-Mismatch -> Konto ueberspringen
        return

    # 1) Nachrichten der wichtigen Ordner (Delta-Sync fuellt CachedMessage + Counts)
    for folder in _WARM_FOLDERS:
        if _stop.is_set():
            return
        try:
            with Session(engine) as s:
                cache_mod.sync_folder(s, acc, pw, folder)
        except Exception:  # noqa: BLE001 - ein Ordner/Konto darf den Lauf nie kippen
            pass

    # 2) Ordnerliste + Zaehler fuer die Seitenleiste (CachedFolder)
    if _stop.is_set():
        return
    try:
        counts = imap_mod.folder_counts(acc, pw)
        with Session(engine) as s:
            cache_mod.write_folder_counts(s, acc.id, counts)
    except Exception:  # noqa: BLE001
        pass


def _sync_once() -> None:
    try:
        with Session(engine) as s:
            accounts = list(s.exec(select(MailAccount)).all())
    except Exception:  # noqa: BLE001
        return
    for acc in accounts:
        if _stop.is_set():
            return
        _sync_account(acc)


def _loop() -> None:
    # kleiner Anlauf, damit der Sync nicht mit dem App-Boot kollidiert
    if _stop.wait(_STARTUP_DELAY):
        return
    while not _stop.is_set():
        _sync_once()
        _stop.wait(_INTERVAL)


def start_scheduler() -> None:
    """Startet den Hintergrund-Sync (idempotent)."""
    global _started, _thread
    if _started:
        return
    if os.getenv("SELFMAILER_SYNC_DISABLE", "").strip().lower() in {"1", "true", "yes"}:
        return
    _started = True
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="selfmailer-sync", daemon=True)
    _thread.start()


def stop_scheduler() -> None:
    _stop.set()
