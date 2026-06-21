"""ntfy-Push bei neuer Mail (self-hosted, kein Google/FCM).

Der Hintergrund-Sync erkennt steigende INBOX-Ungelesen-Zahlen und ruft hier
``push_new_mail`` auf. Wir posten als JSON an den ntfy-Server des Users; die
ntfy-App auf dem Handy zeigt die Benachrichtigung. Best-effort — ein Fehler
darf den Sync nie kippen.
"""
from __future__ import annotations

import logging

import httpx
from sqlmodel import Session, select

from ..models import CachedMessage, MailAccount, PushConfig

logger = logging.getLogger(__name__)


def push_new_mail(session: Session, account: MailAccount, count: int) -> None:
    cfg = session.exec(select(PushConfig).where(PushConfig.user_id == account.user_id)).first()
    if cfg is None or not cfg.enabled or not cfg.ntfy_url or not cfg.topic:
        return

    label = account.label or account.email
    # Neueste ungelesene Mail im INBOX als Vorschau (aus dem frisch gesyncten Cache).
    msg = session.exec(
        select(CachedMessage)
        .where(
            CachedMessage.account_id == account.id,
            CachedMessage.folder == "INBOX",
            CachedMessage.seen == False,  # noqa: E712
        )
        .order_by(CachedMessage.sort_date.desc())
    ).first()

    if count == 1 and msg is not None:
        sender = (msg.from_addr or "").split("<")[0].strip() or (msg.from_addr or "Neue Mail")
        message = f"{sender}: {msg.subject or '(kein Betreff)'}"
    else:
        message = f"{count} neue E-Mails"

    payload = {"topic": cfg.topic, "title": label, "message": message, "tags": ["email"]}
    try:
        httpx.post(cfg.ntfy_url.rstrip("/"), json=payload, timeout=10.0)
    except Exception:  # noqa: BLE001 - Push ist best-effort
        logger.warning("ntfy-Push fehlgeschlagen (user_id=%s)", account.user_id, exc_info=True)
