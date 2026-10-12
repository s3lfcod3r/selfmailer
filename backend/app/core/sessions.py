"""Gemeinsame Sitzungsprüfung für REST, SSE und den zweiten Login-Schritt."""
from __future__ import annotations

import hashlib
import time

from sqlalchemy import delete, func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from .security import decode_token
from ..models import DeviceToken, RevokedToken, User


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def token_user(raw: str, session: Session, *, mfa: bool = False) -> User | None:
    payload = decode_token(raw)
    if payload is None or payload.get("stage") != ("mfa" if mfa else None):
        return None
    user = session.exec(select(User).where(User.username == payload.get("sub"))).first()
    if user is None or not user.is_active:
        return None
    version = payload.get("tv", 0)
    if type(version) is not int or version != int(user.token_version or 0):
        return None
    if session.get(RevokedToken, token_hash(raw)) is not None:
        return None
    return user


def revoke_session(raw: str, user: User, session: Session) -> None:
    """Legacy-JWTs ohne jti werden über ihren Hash ebenfalls widerrufen."""
    payload = decode_token(raw)
    if payload is None:
        return
    prune_expired(session)
    session.merge(RevokedToken(token_hash=token_hash(raw), user_id=user.id, expires_at=int(payload["exp"])))


def prune_expired(session: Session) -> None:
    session.execute(delete(RevokedToken).where(RevokedToken.expires_at <= int(time.time())))


def claim_mfa(raw: str, user: User, session: Session) -> bool:
    """Die Einmal-Challenge und der zweite Faktor teilen eine Schreibtransaktion."""
    payload = decode_token(raw)
    if payload is None:
        return False
    session.add(RevokedToken(token_hash=token_hash(raw), user_id=user.id, expires_at=int(payload["exp"])))
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return False
    return True


def revoke_all(user: User, session: Session) -> None:
    """Atomar erhöhen: zwei parallele Widerrufe dürfen keinen Stand verlieren."""
    session.flush()
    session.execute(update(User).where(User.id == user.id).values(token_version=func.coalesce(User.token_version, 0) + 1))
    session.execute(delete(DeviceToken).where(DeviceToken.user_id == user.id))
    session.refresh(user)
