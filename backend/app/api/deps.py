"""Gemeinsame Dependencies: aktuellen User aus JWT lesen, Adminrolle erzwingen."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from ..core.config import get_settings
from ..core.db import get_session
from ..core.sessions import token_user
from ..models import Role, User

_bearer = HTTPBearer(auto_error=False)


def request_token(request: Request, creds: HTTPAuthorizationCredentials | None = None) -> str:
    if creds:
        return creds.credentials
    auth = request.headers.get("Authorization", "")
    scheme, _, raw = auth.partition(" ")
    if scheme.lower() == "bearer":
        return raw.strip()
    return request.cookies.get(get_settings().cookie_name, "")


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
) -> User:
    # Token aus Bearer-Header (APK) ODER httpOnly-Cookie (Web).
    token = request_token(request, creds)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
    user = token_user(token, session)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Konto inaktiv/unbekannt")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Adminrechte erforderlich")
    return user
