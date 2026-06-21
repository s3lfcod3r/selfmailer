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

import logging
import os
import threading

from sqlmodel import Session, select

from ..core.crypto import decrypt
from ..core.db import engine
from ..models import MailAccount, User
from . import cache as cache_mod
from . import imap as imap_mod
from . import push as push_mod

logger = logging.getLogger(__name__)

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
        logger.warning("Sync uebersprungen: Entschluesselung fehlgeschlagen (account_id=%s)", acc.id, exc_info=True)
        return

    # 1) Nachrichten der wichtigen Ordner (Delta-Sync fuellt CachedMessage + Counts)
    for folder in _WARM_FOLDERS:
        if _stop.is_set():
            return
        try:
            with Session(engine) as s:
                cache_mod.sync_folder(s, acc, pw, folder)
        except Exception:  # noqa: BLE001 - ein Ordner/Konto darf den Lauf nie kippen
            logger.warning(
                "Hintergrund-Sync fehlgeschlagen (account_id=%s, folder=%s)", acc.id, folder, exc_info=True
            )

    # 2) Ordnerliste + Zaehler fuer die Seitenleiste (CachedFolder)
    if _stop.is_set():
        return
    counts: list[dict] | None = None
    try:
        counts = imap_mod.folder_counts(acc, pw)
        with Session(engine) as s:
            cache_mod.write_folder_counts(s, acc.id, counts)
    except Exception:  # noqa: BLE001
        logger.warning("Ordnerzaehler-Sync fehlgeschlagen (account_id=%s)", acc.id, exc_info=True)

    # 3) Push bei neuer Mail: INBOX-Ungelesen mit der zuletzt gemeldeten Zahl
    #    vergleichen. Erster Lauf (Basis -1) setzt nur die Basis, ohne zu pushen.
    if counts is not None and not _stop.is_set():
        inbox_unseen = next(
            (c.get("unseen", 0) for c in counts if str(c.get("name", "")).upper().endswith("INBOX")),
            None,
        )
        if inbox_unseen is not None:
            try:
                with Session(engine) as s:
                    row = s.get(MailAccount, acc.id)
                    if row is not None:
                        base = row.last_notified_unseen
                        if base >= 0 and inbox_unseen > base:
                            push_mod.push_new_mail(s, row, inbox_unseen - base)
                        if inbox_unseen != base:
                            row.last_notified_unseen = inbox_unseen
                            s.add(row)
                            s.commit()
            except Exception:  # noqa: BLE001 - Push darf den Sync nie kippen
                logger.warning("Push-Check fehlgeschlagen (account_id=%s)", acc.id, exc_info=True)


def _sync_once() -> None:
    try:
        with Session(engine) as s:
            # Nur Konten AKTIVER User warmhalten — gesperrte User nicht weiter cachen.
            accounts = list(
                s.exec(
                    select(MailAccount).join(User, MailAccount.user_id == User.id).where(User.is_active == True)  # noqa: E712
                ).all()
            )
    except Exception:  # noqa: BLE001
        logger.warning("Konten fuer Hintergrund-Sync laden fehlgeschlagen", exc_info=True)
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
