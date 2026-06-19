"""Passwort-Hashing (Argon2) und JWT-Erzeugung/-Pruefung."""
from __future__ import annotations

import datetime as dt
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from .config import get_settings

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
    return jwt.encode(payload, settings.secret, algorithm=settings.jwt_algorithm)


def create_mfa_token(subject: str) -> str:
    """Kurzlebiger Zwischen-Token nach Passwort-OK, vor 2FA-Code.

    Traegt claim stage=mfa und ist NUR fuer den /login/totp-Schritt gueltig –
    er erlaubt keinen Zugriff auf geschuetzte Endpunkte (get_current_user lehnt
    stage=mfa ab). Kurze Lebensdauer (5 Min).
    """
    now = dt.datetime.now(dt.timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "stage": "mfa",
        "iat": now,
        "exp": now + dt.timedelta(minutes=5),
    }
    return jwt.encode(payload, get_settings().secret, algorithm=get_settings().jwt_algorithm)


def decode_token(token: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
