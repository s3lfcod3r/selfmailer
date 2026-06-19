"""Gemeinsame Dependencies: aktuellen User aus JWT lesen, Adminrolle erzwingen."""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, select

from ..core.db import get_session
from ..core.security import decode_token
from ..models import Role, User

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
    payload = decode_token(creds.credentials)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token ungueltig")
    # Der 2FA-Zwischen-Token (stage=mfa) gewaehrt KEINEN Vollzugriff.
    if payload.get("stage") == "mfa":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "2FA nicht abgeschlossen")
    user = session.exec(select(User).where(User.username == payload.get("sub"))).first()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Konto inaktiv/unbekannt")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Adminrechte erforderlich")
    return user
