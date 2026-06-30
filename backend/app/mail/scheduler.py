"""Hintergrund-Sync: hält den lokalen Cache dauerhaft WARM.

Idee: Ein Daemon-Thread synct in Intervallen je Konto den INBOX (neue Köpfe,
gelöschte raus, Flags) und die Ordnerzähler in die DB. Dadurch kommt die WebUI
immer SOFORT aus dem Cache und muss nie auf einen langsamen IMAP-Provider warten.

Bewusst SYNCHRON in einem eigenen Thread (passt zu imap_tools/SQLite). Je Konto
eine eigene kurze Session; ein defektes/langsames Konto kippt den Lauf nie. Per
Env steuerbar:
  SELFMAILER_SYNC_DISABLE=1        -> aus
  SELFMAILER_SYNC_INTERVAL=120     -> Sekunden zwischen den Mail-Laeufen (Default 120)
  SELFMAILER_DAV_SYNC_INTERVAL=120 -> Kalender/Adressbuch getrennt + häufiger (Default 120)

Kalender/Adressbücher (DAV) werden bewusst in einem EIGENEN, kürzeren Takt
abgeglichen als die IMAP-Postfächer: ein Termin aus Google soll schneller
auftauchen, ohne dafür jeden IMAP-Provider öfter pollen zu müssen.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, datetime

from sqlmodel import Session, select

from ..core.crypto import decrypt
from ..core.db import engine
from ..events import bus
from ..models import DavAccount, FolderNotify, MailAccount, MailRule, User
from . import cache as cache_mod
from . import imap as imap_mod
from . import push as push_mod

logger = logging.getLogger(__name__)

_INTERVAL = max(60, int(os.getenv("SELFMAILER_SYNC_INTERVAL", "120") or 120))
# DAV (Kalender/Kontakte) läuft getrennt + häufiger; min 30s als Schutz vor
# übermäßigen Google-API-Calls.
_DAV_INTERVAL = max(30, int(os.getenv("SELFMAILER_DAV_SYNC_INTERVAL", "120") or 120))
_TICK = min(_INTERVAL, _DAV_INTERVAL)   # Basis-Takt des Loops (kleinstes Intervall)
_WARM_FOLDERS = ["INBOX"]          # Ordner, deren NACHRICHTEN warmgehalten werden
_STARTUP_DELAY = 15.0              # nicht direkt beim Boot loslegen
_BACKUP_HOUR = 3                   # nächtliches DB-Backup ~03:00 Ortszeit

_started = False
_thread: threading.Thread | None = None
_stop = threading.Event()
_last_backup_date: date | None = None   # an welchem Tag zuletzt gesichert wurde


def _sync_account(acc: MailAccount) -> None:
    """Einen Account warmhalten: INBOX-Nachrichten + alle Ordnerzähler."""
    try:
        pw = decrypt(acc.secret_enc)
    except Exception:  # noqa: BLE001 - z. B. Schlüssel-Mismatch -> Konto überspringen
        logger.warning("Sync uebersprungen: Entschlüsselung fehlgeschlagen (account_id=%s)", acc.id, exc_info=True)
        return

    # 0) Filterregeln automatisch anwenden (löschen/verschieben/markieren), BEVOR
    #    der Cache gefüllt und gepusht wird — so taucht geblockter Spam gar nicht
    #    erst als „neue Mail" auf. Danach ggf. den Spam-Ordner endgültig leeren.
    try:
        with Session(engine) as s:
            rules = list(
                s.exec(
                    select(MailRule)
                    .where(MailRule.account_id == acc.id, MailRule.enabled == True)  # noqa: E712
                    .order_by(MailRule.position, MailRule.id)
                ).all()
            )
        if rules and not _stop.is_set():
            imap_mod.apply_rules(acc, pw, rules)
    except Exception:  # noqa: BLE001 - Regeln dürfen den Sync nie kippen
        logger.warning("Auto-Regeln fehlgeschlagen (account_id=%s)", acc.id, exc_info=True)

    if acc.spam_purge_days >= 0 and not _stop.is_set():
        try:
            imap_mod.purge_spam(acc, pw, acc.spam_purge_days)
        except Exception:  # noqa: BLE001 - Spam-Purge darf den Sync nie kippen
            logger.warning("Spam-Auto-Purge fehlgeschlagen (account_id=%s)", acc.id, exc_info=True)

    if acc.trash_purge_days >= 0 and not _stop.is_set():
        try:
            imap_mod.purge_trash(acc, pw, acc.trash_purge_days)
        except Exception:  # noqa: BLE001 - Papierkorb-Purge darf den Sync nie kippen
            logger.warning("Papierkorb-Auto-Purge fehlgeschlagen (account_id=%s)", acc.id, exc_info=True)

    # 1) Nachrichten der wichtigen Ordner (Delta-Sync füllt CachedMessage + Counts).
    #    Kamen NEUE Mails an, sofort ein Live-Sync-Event senden, damit offene
    #    Clients (Web/App) auffrischen — UNABHÄNGIG von Benachrichtigungs-Ordnern.
    #    (Die eigentliche Push-Notification weiter unten bleibt an FolderNotify
    #    gebunden; das hier ist nur das „bitte neu laden" für offene Ansichten.)
    for folder in _WARM_FOLDERS:
        if _stop.is_set():
            return
        try:
            with Session(engine) as s:
                res = cache_mod.sync_folder(s, acc, pw, folder)
            if int(res.get("new", 0) or 0) > 0:
                bus.publish(acc.user_id, {"type": "mail", "account_id": acc.id, "folder": folder})
        except Exception:  # noqa: BLE001 - ein Ordner/Konto darf den Lauf nie kippen
            logger.warning(
                "Hintergrund-Sync fehlgeschlagen (account_id=%s, folder=%s)", acc.id, folder, exc_info=True
            )

    # 2) Ordnerliste + Zähler für die Seitenleiste (CachedFolder)
    if _stop.is_set():
        return
    counts: list[dict] | None = None
    try:
        counts = imap_mod.folder_counts(acc, pw)
        with Session(engine) as s:
            cache_mod.write_folder_counts(s, acc.id, counts)
    except Exception:  # noqa: BLE001
        logger.warning("Ordnerzähler-Sync fehlgeschlagen (account_id=%s)", acc.id, exc_info=True)

    # 3) Push bei neuer Mail: pro ausgewähltem Ordner die Ungelesen-Zahl mit der
    #    zuletzt gemeldeten vergleichen. Erster Lauf (Basis -1) setzt nur die Basis.
    if counts is not None and not _stop.is_set():
        try:
            with Session(engine) as s:
                rows = list(s.exec(select(FolderNotify).where(FolderNotify.account_id == acc.id)).all())
                if rows:
                    by_name = {str(c.get("name", "")): int(c.get("unseen", 0) or 0) for c in counts}
                    changed = False
                    decreased = False
                    for row in rows:
                        unseen = by_name.get(row.folder)
                        if unseen is None:
                            continue
                        base = row.last_unseen
                        if base >= 0 and unseen > base:
                            push_mod.push_new_mail(s, acc, row.folder, unseen - base)
                        if base >= 0 and unseen < base:
                            decreased = True   # woanders gelesen → Geräte aufräumen lassen
                        if unseen != base:
                            row.last_unseen = unseen
                            s.add(row)
                            changed = True
                            # Live-Sync: offene Clients dieses Users auffrischen lassen.
                            bus.publish(acc.user_id, {"type": "mail", "account_id": acc.id, "folder": row.folder})
                    if changed:
                        s.commit()
                    if decreased:
                        # Stiller FCM-Refresh → Handy entfernt erledigte Benachrichtigungen.
                        push_mod.push_refresh(s, acc.user_id)
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
        logger.warning("Konten für Hintergrund-Sync laden fehlgeschlagen", exc_info=True)
        return
    for acc in accounts:
        if _stop.is_set():
            return
        _sync_account(acc)


def _sync_dav() -> None:
    """Externe Kalender/Adressbücher (CalDAV/Google/iCal) periodisch abgleichen,
    damit neue Termine ohne manuelles Synchronisieren in WebUI/APK erscheinen."""
    try:
        from ..api.dav import run_dav_sync  # lazy: vermeidet Import-Zyklen beim Boot
    except Exception:  # noqa: BLE001
        logger.warning("DAV-Sync-Import fehlgeschlagen", exc_info=True)
        return
    try:
        with Session(engine) as s:
            accs = list(
                s.exec(
                    select(DavAccount).join(User, DavAccount.user_id == User.id).where(User.is_active == True)  # noqa: E712
                ).all()
            )
    except Exception:  # noqa: BLE001
        logger.warning("DAV-Konten für Hintergrund-Sync laden fehlgeschlagen", exc_info=True)
        return
    for acc in accs:
        if _stop.is_set():
            return
        try:
            with Session(engine) as s:
                fresh = s.get(DavAccount, acc.id)
                if fresh is not None:
                    run_dav_sync(fresh, s)
        except Exception:  # noqa: BLE001 - ein Konto darf den Lauf nie kippen
            logger.warning("DAV-Hintergrund-Sync fehlgeschlagen (id=%s)", acc.id, exc_info=True)


def _maybe_backup() -> None:
    """Einmal pro Tag (ab ~03:00) ein konsistentes DB-Backup ziehen.

    Der Loop tickt im Sync-Intervall; dieser Wachposten löst höchstens einmal
    je Kalendertag aus, sobald die Backup-Stunde erreicht ist. Ein Fehler im
    Backup darf den Scheduler/Container NIE kippen (try/except + Logging, wie
    überall hier). Lazy-Import vermeidet Import-Zyklen beim Boot.
    """
    global _last_backup_date
    now = datetime.now()
    if now.hour < _BACKUP_HOUR:
        return
    if _last_backup_date == now.date():
        return
    try:
        from ..core.backup import create_backup
        create_backup()
        _last_backup_date = now.date()
    except Exception:  # noqa: BLE001 - Backup darf den Scheduler nie kippen
        logger.warning("Nächtliches DB-Backup fehlgeschlagen", exc_info=True)
        # Tag trotzdem markieren, damit ein dauerhaft kaputtes Backup nicht jeden
        # Loop-Durchlauf erneut feuert (Log-Flut). Nächster Versuch morgen.
        _last_backup_date = now.date()


def _loop() -> None:
    # kleiner Anlauf, damit der Sync nicht mit dem App-Boot kollidiert
    if _stop.wait(_STARTUP_DELAY):
        return
    # Getrennte Fälligkeits-Timer: der Loop tickt im kleinsten Intervall (_TICK),
    # Mail und DAV feuern unabhängig nach ihrem eigenen Intervall. Start bei 0.0
    # => beide laufen direkt im ersten Tick (initialer Sync).
    last_mail = 0.0
    last_dav = 0.0
    while not _stop.is_set():
        now = time.monotonic()
        if now - last_mail >= _INTERVAL:
            _sync_once()
            last_mail = time.monotonic()
        if now - last_dav >= _DAV_INTERVAL:
            _sync_dav()
            last_dav = time.monotonic()
        _maybe_backup()
        _stop.wait(_TICK)


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
