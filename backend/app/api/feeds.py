"""Abonnierbare Export-Feeds: Token-Verwaltung.

Ein Handy-Kalender/-Adressbuch abonniert eine URL und kann dabei keinen
Bearer-Header setzen. Deshalb authentifiziert ein geheimer Token in der URL
(``?token=...``). Pro User genau ein Token, jederzeit rotierbar; eine Rotation
macht alte Abo-Links sofort ungueltig.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlmodel import Session, select

from ..core.config import get_settings
from ..core.db import get_session
from ..models import FeedToken, User
from ..schemas import FeedTokenOut
from .deps import _bearer, get_current_user

router = APIRouter(prefix="/api/v1/feeds", tags=["feeds"])


def _new_token() -> str:
    return secrets.token_urlsafe(24)


def get_or_create_token(user: User, session: Session) -> FeedToken:
    ft = session.exec(select(FeedToken).where(FeedToken.user_id == user.id)).first()
    if ft is None:
        ft = FeedToken(user_id=user.id, token=_new_token())
        session.add(ft)
        session.commit()
        session.refresh(ft)
    return ft


def user_for_feed_token(token: str, session: Session) -> User | None:
    """Loest einen Feed-Token zu einem aktiven User auf (oder None)."""
    if not token:
        return None
    ft = session.exec(select(FeedToken).where(FeedToken.token == token)).first()
    if ft is None:
        return None
    user = session.get(User, ft.user_id)
    if user is None or not user.is_active:
        return None
    return user


def feed_or_bearer_user(
    token: str = Query(default=""),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
) -> User:
    """Auth fuer Export-Endpoints: erst ``?token=``, sonst Bearer-Login.

    So funktioniert derselbe Endpoint als abonnierbarer Feed (Token in der URL)
    und als direkter Download aus der WebUI (Bearer-Header).
    """
    if token:
        user = user_for_feed_token(token, session)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Feed-Token ungueltig")
        return user
    return get_current_user(creds, session)


def _payload(token: str) -> FeedTokenOut:
    base = get_settings().base_url.rstrip("/")
    cal = f"{base}/api/v1/calendar/export.ics?token={token}"
    con = f"{base}/api/v1/contacts/export.vcf?token={token}"
    dash = f"{base}/api/v1/dashboard/summary?token={token}"
    return FeedTokenOut(token=token, calendar_url=cal, contacts_url=con, dashboard_url=dash)


@router.get("/token", response_model=FeedTokenOut)
def show_token(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FeedTokenOut:
    """Liefert (und erzeugt bei Bedarf) den persoenlichen Feed-Token + URLs."""
    return _payload(get_or_create_token(user, session).token)


@router.post("/token/rotate", response_model=FeedTokenOut)
def rotate_token(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FeedTokenOut:
    """Setzt einen neuen Token; alte Abo-Links werden dadurch ungueltig."""
    ft = get_or_create_token(user, session)
    ft.token = _new_token()
    session.add(ft)
    session.commit()
    session.refresh(ft)
    return _payload(ft.token)
