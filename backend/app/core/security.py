"""Passwort-Hashing (Argon2) und JWT-Erzeugung/-Prüfung."""
from __future__ import annotations

import datetime as dt
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from fastapi import Response

from .config import get_settings
from .crypto import jwt_key

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_access_token(subject: str, role: str) -> str:
    settings = get_settings()
    now = dt.datetime.now(dt.timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": now + dt.timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, jwt_key(), algorithm=settings.jwt_algorithm)


def create_mfa_token(subject: str) -> str:
    """Kurzlebiger Zwischen-Token nach Passwort-OK, vor 2FA-Code.

    Trägt claim stage=mfa und ist NUR für den /login/totp-Schritt gültig –
    er erlaubt keinen Zugriff auf geschützte Endpunkte (get_current_user lehnt
    stage=mfa ab). Kurze Lebensdauer (5 Min).
    """
    now = dt.datetime.now(dt.timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "stage": "mfa",
        "iat": now,
        "exp": now + dt.timedelta(minutes=5),
    }
    return jwt.encode(payload, jwt_key(), algorithm=get_settings().jwt_algorithm)


def decode_token(token: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        return jwt.decode(token, jwt_key(), algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None


def set_session_cookie(response: Response, token: str) -> None:
    """Setzt das Login-Token als httpOnly-Cookie (Web). SameSite=Lax begrenzt
    CSRF: der Browser sendet das Cookie NICHT bei cross-site fetch/POST."""
    settings = get_settings()
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.jwt_expire_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(key=settings.cookie_name, path="/")
