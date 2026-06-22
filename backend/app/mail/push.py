"""Push bei neuer Mail — ntfy (self-hosted) UND/ODER FCM (Google).

Der Hintergrund-Sync erkennt steigende Ungelesen-Zahlen je ausgewaehltem Ordner
und ruft ``push_new_mail`` auf. Wir bauen eine kurze Vorschau und schicken sie an
beide aktivierten Kanaele: ntfy (an den ntfy-Server des Users) und FCM (an die
Android-Geraetetokens des Users). Best-effort — ein Fehler darf den Sync nie kippen.
"""
from __future__ import annotations

import logging

import httpx
from sqlmodel import Session, select

from ..dav.client import DavUrlError, validate_external_url
from ..models import CachedMessage, MailAccount, PushConfig
from . import fcm as fcm_mod

logger = logging.getLogger(__name__)


def _preview(session: Session, account: MailAccount, folder: str, count: int) -> tuple[str, str]:
    """Liefert (Titel, Text) fuer die Benachrichtigung."""
    label = account.label or account.email
    leaf = folder.rsplit("/", 1)[-1].rsplit(".", 1)[-1]
    is_inbox = folder.upper().endswith("INBOX")

    # Vorschau (Absender/Betreff) nur fuer gecachte Ordner (i. d. R. INBOX).
    msg = session.exec(
        select(CachedMessage)
        .where(
            CachedMessage.account_id == account.id,
            CachedMessage.folder == folder,
            CachedMessage.seen == False,  # noqa: E712
        )
        .order_by(CachedMessage.sort_date.desc())
    ).first()

    if count == 1 and msg is not None:
        sender = (msg.from_addr or "").split("<")[0].strip() or (msg.from_addr or "Neue Mail")
        text = f"{sender}: {msg.subject or '(kein Betreff)'}"
    else:
        text = f"{count} neue E-Mails"
    if not is_inbox:
        text = f"{text} · {leaf}"
    return label, text


def _push_ntfy(session: Session, account: MailAccount, title: str, text: str) -> None:
    cfg = session.exec(select(PushConfig).where(PushConfig.user_id == account.user_id)).first()
    if cfg is None or not cfg.enabled or not cfg.ntfy_url or not cfg.topic:
        return
    # SSRF-Schutz: Altbestand-URLs (vor Validierung gespeichert) defensiv pruefen.
    try:
        validate_external_url(cfg.ntfy_url.rstrip("/"))
    except DavUrlError:
        logger.warning("ntfy-URL blockiert (SSRF-Schutz, user_id=%s)", account.user_id)
        return
    payload = {"topic": cfg.topic, "title": title, "message": text, "tags": ["email"]}
    try:
        httpx.post(cfg.ntfy_url.rstrip("/"), json=payload, timeout=10.0)
    except Exception:  # noqa: BLE001 - Push ist best-effort
        logger.warning("ntfy-Push fehlgeschlagen (user_id=%s)", account.user_id, exc_info=True)


def push_new_mail(session: Session, account: MailAccount, folder: str, count: int) -> None:
    title, text = _preview(session, account, folder, count)
    _push_ntfy(session, account, title, text)
    try:
        fcm_mod.notify(session, account.user_id, title, text, account_id=account.id, folder=folder)
    except Exception:  # noqa: BLE001 - FCM ist best-effort
        logger.warning("FCM-Push fehlgeschlagen (user_id=%s)", account.user_id, exc_info=True)


def push_refresh(session: Session, user_id: int) -> None:
    """Stiller Refresh-Push (FCM), damit Geräte gelesene Benachrichtigungen
    aufräumen. ntfy kann nicht aufräumen → nur FCM."""
    try:
        fcm_mod.push_refresh(session, user_id)
    except Exception:  # noqa: BLE001 - best-effort
        logger.warning("FCM-Refresh fehlgeschlagen (user_id=%s)", user_id, exc_info=True)
