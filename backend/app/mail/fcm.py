"""Google FCM (Firebase Cloud Messaging) Push — HTTP v1.

Schlafend, solange keine Service-Account-JSON konfiguriert ist
(`SELFMAILER_FCM_CREDENTIALS`). Das OAuth-Access-Token wird selbst gemintet
(JWT-Bearer-Flow per PyJWT) — keine extra Google-Bibliothek nötig.

Best-effort: jeder Fehler wird geloggt und geschluckt; Push darf den Sync nie kippen.
Tote Tokens (UNREGISTERED) werden entfernt.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

import httpx
import jwt
from sqlmodel import Session, select

from ..core.config import get_settings
from ..models import DeviceToken

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_sa: dict | None = None
_sa_loaded = False
_token: str = ""
_token_exp: float = 0.0


def _load_sa() -> dict | None:
    global _sa, _sa_loaded
    if _sa_loaded:
        return _sa
    _sa_loaded = True
    path = get_settings().fcm_credentials
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _sa = json.load(f)
        except Exception:  # noqa: BLE001
            logger.warning("FCM: Service-Account-JSON nicht lesbar (%s)", path, exc_info=True)
            _sa = None
    return _sa


def enabled() -> bool:
    sa = _load_sa()
    return bool(sa and sa.get("private_key") and sa.get("client_email") and sa.get("project_id"))


def _access_token(sa: dict) -> str:
    """Gemintetes, kurzlebig gecachtes OAuth-Token fuer die FCM-API."""
    global _token, _token_exp
    now = time.time()
    with _lock:
        if _token and now < _token_exp - 60:
            return _token
        payload = {
            "iss": sa["client_email"],
            "scope": "https://www.googleapis.com/auth/firebase.messaging",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": int(now),
            "exp": int(now) + 3600,
        }
        assertion = jwt.encode(payload, sa["private_key"], algorithm="RS256")
        resp = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        _token = data["access_token"]
        _token_exp = now + float(data.get("expires_in", 3600))
        return _token


def _send_all(session: Session, user_id: int, data: dict[str, str]) -> None:
    """Sendet eine DATA-ONLY-FCM-Nachricht an alle Geräte des Users (best-effort).

    Data-only (kein notification-Block) → die App baut die Benachrichtigung in
    onMessageReceived selbst, mit STABILER ID, und kann sie damit auch wieder
    entfernen (Auto-Aufräumen). High-Priority, damit es auch im Hintergrund ankommt.
    """
    sa = _load_sa()
    if not enabled() or sa is None:
        return
    rows = list(session.exec(select(DeviceToken).where(DeviceToken.user_id == user_id)).all())
    if not rows:
        return
    try:
        access = _access_token(sa)
    except Exception:  # noqa: BLE001
        logger.warning("FCM: Access-Token holen fehlgeschlagen", exc_info=True)
        return

    url = f"https://fcm.googleapis.com/v1/projects/{sa['project_id']}/messages:send"
    headers = {"Authorization": f"Bearer {access}", "Content-Type": "application/json"}
    dead: list[DeviceToken] = []
    for row in rows:
        msg = {"message": {"token": row.token, "data": data, "android": {"priority": "high"}}}
        try:
            resp = httpx.post(url, headers=headers, json=msg, timeout=10.0)
            if resp.status_code == 404 or (resp.status_code == 400 and "UNREGISTERED" in resp.text):
                dead.append(row)
            elif resp.status_code >= 400:
                logger.warning("FCM send %s: %s", resp.status_code, resp.text[:200])
        except Exception:  # noqa: BLE001
            logger.warning("FCM send fehlgeschlagen (user_id=%s)", user_id, exc_info=True)
    for row in dead:
        session.delete(row)
    if dead:
        session.commit()


def notify(
    session: Session,
    user_id: int,
    title: str,
    body: str,
    account_id: int | None = None,
    folder: str | None = None,
) -> None:
    """Neue-Mail-Push (data-only)."""
    data: dict[str, str] = {"type": "mail", "title": title, "body": body}
    if account_id is not None:
        data["account_id"] = str(account_id)
    if folder:
        data["folder"] = folder
    _send_all(session, user_id, data)


def push_refresh(session: Session, user_id: int) -> None:
    """Stiller Hinweis an die Geräte: Benachrichtigungen auffrischen/aufräumen
    (z. B. nachdem Mails woanders gelesen wurden) — zeigt KEINE neue Notification."""
    _send_all(session, user_id, {"type": "refresh"})
