"""Aggregierte Mail-Übersicht über ALLE Postfächer eines Users.

Für externe Anzeigen (z. B. ein SelfDashboard-Widget): liefert die Summe der
ungelesenen Mails plus die neuesten ungelesenen Köpfe über alle Konten — egal
welches Postfach gerade Post hat.

Authentifizierung wahlweise per Bearer-Token (WebUI) ODER per Feed-Token in der
URL (``?token=...``), damit ein Dashboard ganz ohne Login pollen kann.

Cache-first: ohne ``?live=1`` kommen die Zahlen SOFORT aus dem DB-Cache. Mit
``?live=1`` wird je Konto der INBOX-Ordner frisch synchronisiert (ein IMAP-Login
pro Konto) und der Cache aktualisiert. Ein defektes Konto kippt die Übersicht
nie — es zählt dann eben 0.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..core.crypto import decrypt
from ..core.db import get_session
from ..mail import cache as cache_mod
from ..mail import imap as imap_mod
from ..models import MailAccount, User
from .feeds import feed_or_bearer_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

INBOX = "INBOX"
RECENT_LIMIT = 10          # max. Vorschau-Mails über alle Konten zusammen
PER_ACCOUNT_RECENT = 5     # je Konto so viele neueste Ungelesene einsammeln

# Bei folders=all NICHT mitzählende Sonderordner: dort liegt keine "neue
# eingehende Mail" (Papierkorb/Spam/Gesendet/Entwürfe). Inbox/Archiv/eigene
# Ordner + Unterordner zählen mit.
_EXCLUDED_KINDS = {"trash", "spam", "sent", "drafts"}


def _cached_unseen(session: Session, account_id: int) -> int:
    counts = cache_mod.read_counts(session, account_id)
    fs = counts.get(INBOX)
    return int(fs.unseen) if fs else 0


def _leaf(folder: str) -> str:
    """Letzter Pfadteil eines Ordnernamens (Trenner . oder /)."""
    return folder.replace("/", ".").rsplit(".", 1)[-1]


def _all_folders_unseen(session: Session, account_id: int) -> int:
    """Ungelesen über ALLE Ordner des Kontos (aus dem warmen Ordner-Cache),
    OHNE Papierkorb/Spam/Gesendet/Entwürfe. Quelle: CachedFolder, vom
    Hintergrund-Scheduler je Konto alle paar Minuten via IMAP STATUS gepflegt."""
    total = 0
    for fc in cache_mod.read_folder_counts(session, account_id):
        name = fc.get("name") or ""
        if imap_mod._special_kind(_leaf(name)) in _EXCLUDED_KINDS:
            continue
        total += int(fc.get("unseen", 0) or 0)
    return total


def _live_unseen(acc: MailAccount) -> tuple[int, int | None, str | None]:
    """(ungelesen, dauer_ms, fehler) per schnellem IMAP-STATUS. NUR IMAP, KEIN
    Session-/DB-Zugriff (damit es thread-safe in einem Pool laufen kann)."""
    t0 = time.monotonic()
    try:
        u = imap_mod.inbox_unseen(acc, decrypt(acc.secret_enc), INBOX)
        return u, int((time.monotonic() - t0) * 1000), None
    except Exception:  # noqa: BLE001
        # Detail NUR ins Server-Log (account_id), dem Client nur eine generische
        # Meldung — interne Exception-Texte könnten Host/Pfade/Interna verraten.
        logger.warning("Dashboard-Live-Abruf fehlgeschlagen (account_id=%s)", acc.id, exc_info=True)
        return -1, int((time.monotonic() - t0) * 1000), "Abruf fehlgeschlagen"


@router.get("/summary")
def summary(
    live: bool = False,
    folders: str = "inbox",
    user: User = Depends(feed_or_bearer_user),
    session: Session = Depends(get_session),
) -> dict:
    """Gebündelte Übersicht über alle Postfächer des Users.

    Antwort::

        {
          "total_unseen": 12,
          "accounts": [{"id": 1, "label": "Web.de", "email": "...", "unseen": 7}, ...],
          "recent":   [{"account": "Web.de", "from": "...", "subject": "...",
                        "date": "...", "uid": "...", "ts": "..."}, ...]
        }
    """
    accounts = list(session.exec(select(MailAccount).where(MailAccount.user_id == user.id)).all())

    # folders=all -> ALLE Ordner (ohne Papierkorb/Spam/Gesendet/Entwürfe) aus dem
    # warmen Ordner-Cache. Bewusst KEIN Live-IMAP über alle Ordner (STATUS je
    # Ordner je Konto = zu langsam fürs Polling) — der Scheduler hält die Zähler
    # frisch. folders=inbox (Default) = bisheriges Verhalten (nur Posteingang).
    include_all = folders.strip().lower() in {"all", "sub", "subfolders"}

    # Live: alle Konten PARALLEL abfragen (jedes durch IMAP-Timeout gebunden) ->
    # Gesamtdauer ~ langsamstes Konto statt Summe. Threads machen NUR IMAP.
    # Nur im Inbox-Modus relevant (all zählt immer aus dem Cache).
    live_by_id: dict[int, tuple[int, int | None, str | None]] = {}
    if live and accounts and not include_all:
        with ThreadPoolExecutor(max_workers=min(8, len(accounts))) as pool:
            for acc, res in zip(accounts, pool.map(_live_unseen, accounts)):
                live_by_id[acc.id] = res

    total = 0
    items: list[dict] = []
    recent_all: list[dict] = []
    for acc in accounts:
        ms: int | None = None
        err: str | None = None
        if include_all:
            unseen = _all_folders_unseen(session, acc.id)
        elif acc.id in live_by_id:
            u, ms, err = live_by_id[acc.id]
            unseen = u if u >= 0 else _cached_unseen(session, acc.id)  # Fehler -> Cache
        else:
            unseen = _cached_unseen(session, acc.id)
        total += unseen
        label = acc.label or acc.email
        items.append({"id": acc.id, "label": label, "email": acc.email, "unseen": unseen, "ms": ms, "error": err})
        for m in cache_mod.recent_unseen(session, acc.id, INBOX, limit=PER_ACCOUNT_RECENT):
            recent_all.append({
                "account": label, "uid": m["uid"], "from": m["from"],
                "subject": m["subject"], "date": m["date"], "ts": m["ts"],
            })
    recent_all.sort(key=lambda m: m["ts"], reverse=True)
    return {"total_unseen": total, "accounts": items, "recent": recent_all[:RECENT_LIMIT]}
