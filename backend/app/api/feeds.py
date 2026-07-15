"""Abonnierbare Export-Feeds: Token-Verwaltung.

Ein Handy-Kalender/-Adressbuch abonniert eine URL und kann dabei keinen
Bearer-Header setzen. Deshalb authentifiziert ein geheimer Token in der URL
(``?token=...``). Pro User genau ein Token, jederzeit rotierbar; eine Rotation
macht alte Abo-Links sofort ungültig.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlmodel import Session, select

from ..core.config import get_settings
from ..core.db import get_session
from ..models import FeedToken, User
from ..schemas import FeedTokenOut, WriteTokenOut
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
    """Löst einen Feed-Token (Lese- ODER Schreib-Token) zu einem aktiven User auf.

    Lesen ist mit beiden Tokens erlaubt; der Schreib-Token ist der privilegiertere.
    """
    if not token:
        return None
    ft = session.exec(
        select(FeedToken).where(
            (FeedToken.token == token) | (FeedToken.write_token == token)
        )
    ).first()
    if ft is None:
        return None
    user = session.get(User, ft.user_id)
    if user is None or not user.is_active:
        return None
    return user


def user_for_write_token(token: str, session: Session) -> User | None:
    """Löst NUR einen gültigen SCHREIB-Token zu einem aktiven User auf (sonst None)."""
    if not token:
        return None
    ft = session.exec(select(FeedToken).where(FeedToken.write_token == token)).first()
    if ft is None or not ft.write_token:
        return None
    user = session.get(User, ft.user_id)
    if user is None or not user.is_active:
        return None
    return user


def feed_or_bearer_user(
    request: Request,
    token: str = Query(default=""),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
) -> User:
    """Auth für Export-Endpoints: erst ``?token=``, sonst Login (Cookie/Bearer).

    So funktioniert derselbe Endpoint als abonnierbarer Feed (Token in der URL)
    und als direkter Aufruf aus der WebUI (httpOnly-Cookie) oder der APK (Bearer).
    """
    if token:
        user = user_for_feed_token(token, session)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Feed-Token ungültig")
        return user
    # get_current_user erwartet (request, creds, session) — request für den Cookie-Fallback.
    return get_current_user(request, creds, session)


def feed_write_or_login(
    request: Request,
    token: str = Query(default=""),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
) -> User:
    """Auth für SCHREIBENDE Endpunkte (Termin anlegen/ändern/löschen): NUR der
    Schreib-Token ODER ein echter Login. Der leck-anfällige Lese-Token (steckt in
    Abo-/Export-URLs) wird hier bewusst NICHT akzeptiert — so kann ein vertrauens-
    würdiger Client (Dashboard-Widget) schreiben, ein geleakter Abo-Link aber nicht.
    """
    if token:
        user = user_for_write_token(token, session)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Schreib-Token ungültig")
        return user
    return get_current_user(request, creds, session)


def get_or_create_write_token(user: User, session: Session) -> FeedToken:
    """Wie get_or_create_token, erzeugt zusätzlich bei Bedarf den Schreib-Token."""
    ft = get_or_create_token(user, session)
    if not ft.write_token:
        ft.write_token = _new_token()
        session.add(ft)
        session.commit()
        session.refresh(ft)
    return ft


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
    """Liefert (und erzeugt bei Bedarf) den persönlichen Feed-Token + URLs."""
    return _payload(get_or_create_token(user, session).token)


@router.post("/token/rotate", response_model=FeedTokenOut)
def rotate_token(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FeedTokenOut:
    """Setzt einen neuen Token; alte Abo-Links werden dadurch ungültig."""
    ft = get_or_create_token(user, session)
    ft.token = _new_token()
    session.add(ft)
    session.commit()
    session.refresh(ft)
    return _payload(ft.token)


@router.get("/write-token", response_model=WriteTokenOut)
def show_write_token(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> WriteTokenOut:
    """Liefert (und erzeugt bei Bedarf) den SCHREIB-Token fürs Dashboard/
    Kalender-Widget. Nur für vertrauenswürdige Clients — NICHT in Abo-URLs nutzen."""
    return WriteTokenOut(write_token=get_or_create_write_token(user, session).write_token or "")


@router.post("/write-token/rotate", response_model=WriteTokenOut)
def rotate_write_token(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> WriteTokenOut:
    """Setzt einen neuen Schreib-Token; schreibende Clients müssen neu konfiguriert
    werden. Der Lese-Token (Abos) bleibt davon unberührt."""
    ft = get_or_create_token(user, session)
    ft.write_token = _new_token()
    session.add(ft)
    session.commit()
    session.refresh(ft)
    return WriteTokenOut(write_token=ft.write_token or "")
