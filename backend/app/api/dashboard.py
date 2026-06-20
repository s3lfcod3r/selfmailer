"""Aggregierte Mail-Uebersicht ueber ALLE Postfaecher eines Users.

Fuer externe Anzeigen (z. B. ein SelfDashboard-Widget): liefert die Summe der
ungelesenen Mails plus die neuesten ungelesenen Koepfe ueber alle Konten — egal
welches Postfach gerade Post hat.

Authentifizierung wahlweise per Bearer-Token (WebUI) ODER per Feed-Token in der
URL (``?token=...``), damit ein Dashboard ganz ohne Login pollen kann.

Cache-first: ohne ``?live=1`` kommen die Zahlen SOFORT aus dem DB-Cache. Mit
``?live=1`` wird je Konto der INBOX-Ordner frisch synchronisiert (ein IMAP-Login
pro Konto) und der Cache aktualisiert. Ein defektes Konto kippt die Uebersicht
nie — es zaehlt dann eben 0.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..core.crypto import decrypt
from ..core.db import get_session
from ..mail import cache as cache_mod
from ..mail import imap as imap_mod
from ..models import MailAccount, User
from .feeds import feed_or_bearer_user

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

INBOX = "INBOX"
RECENT_LIMIT = 10          # max. Vorschau-Mails ueber alle Konten zusammen
PER_ACCOUNT_RECENT = 5     # je Konto so viele neueste Ungelesene einsammeln


def _cached_unseen(session: Session, account_id: int) -> int:
    counts = cache_mod.read_counts(session, account_id)
    fs = counts.get(INBOX)
    return int(fs.unseen) if fs else 0


def _account_block(session: Session, acc: MailAccount, live: bool) -> tuple[int, list[dict]]:
    """(ungelesen, neueste Ungelesene) eines Kontos.

    live=1: NUR schneller IMAP-STATUS der INBOX (1 Login, kein Header-Download) →
    aktuelle Ungelesen-Zahl ohne Timeout-Risiko grosser Postfaecher. Die Vorschau
    (recent) kommt weiter aus dem Cache (wird beim Nutzen der WebUI aufgefrischt).
    """
    if live:
        try:
            unseen = imap_mod.inbox_unseen(acc, decrypt(acc.secret_enc), INBOX)
        except Exception:  # noqa: BLE001 - ein defektes Konto darf die Uebersicht nicht kippen
            unseen = _cached_unseen(session, acc.id)
    else:
        unseen = _cached_unseen(session, acc.id)
    recent = cache_mod.recent_unseen(session, acc.id, INBOX, limit=PER_ACCOUNT_RECENT)
    return unseen, recent


@router.get("/summary")
def summary(
    live: bool = False,
    user: User = Depends(feed_or_bearer_user),
    session: Session = Depends(get_session),
) -> dict:
    """Gebuendelte Uebersicht ueber alle Postfaecher des Users.

    Antwort::

        {
          "total_unseen": 12,
          "accounts": [{"id": 1, "label": "Web.de", "email": "...", "unseen": 7}, ...],
          "recent":   [{"account": "Web.de", "from": "...", "subject": "...",
                        "date": "...", "uid": "...", "ts": "..."}, ...]
        }
    """
    accounts = session.exec(select(MailAccount).where(MailAccount.user_id == user.id)).all()
    total = 0
    items: list[dict] = []
    recent_all: list[dict] = []
    for acc in accounts:
        unseen, recent = _account_block(session, acc, live)
        total += unseen
        label = acc.label or acc.email
        items.append({"id": acc.id, "label": label, "email": acc.email, "unseen": unseen})
        for m in recent:
            recent_all.append({
                "account": label, "uid": m["uid"], "from": m["from"],
                "subject": m["subject"], "date": m["date"], "ts": m["ts"],
            })
    recent_all.sort(key=lambda m: m["ts"], reverse=True)
    return {"total_unseen": total, "accounts": items, "recent": recent_all[:RECENT_LIMIT]}
