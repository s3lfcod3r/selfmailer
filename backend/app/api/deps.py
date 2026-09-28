"""Gemeinsame Dependencies: aktuellen User aus JWT lesen, Adminrolle erzwingen."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, select

from ..core.config import get_settings
from ..core.db import get_session
from ..core.security import decode_token
from ..models import Role, User

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
) -> User:
    # Token aus Bearer-Header (APK) ODER httpOnly-Cookie (Web).
    token = creds.credentials if creds else request.cookies.get(get_settings().cookie_name)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token ungültig")
    # Der 2FA-Zwischen-Token (stage=mfa) gewährt KEINEN Vollzugriff.
    if payload.get("stage") == "mfa":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "2FA nicht abgeschlossen")
    user = session.exec(select(User).where(User.username == payload.get("sub"))).first()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Konto inaktiv/unbekannt")
    # Sitzungs-Widerruf (Befund 4): Passwortwechsel, 2FA-Abschalten und
    # "ueberall abmelden" erhoehen User.token_version. Ein Token mit einer
    # aelteren Version ist damit sofort ungueltig, auch wenn es formal noch
    # bis zu 7 Tage laufen wuerde.
    # Bestands-Token ohne den Claim: Version 0 == Default in der DB, sie
    # bleiben also gueltig - das Update meldet niemanden ab.
    try:
        claimed_version = int(payload.get("tv", 0))
    except (TypeError, ValueError):
        claimed_version = -1
    if claimed_version != int(user.token_version or 0):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sitzung wurde beendet")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Adminrechte erforderlich")
    return user
