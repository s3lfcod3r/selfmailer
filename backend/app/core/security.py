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

# Aussteller/Zielgruppe der eigenen JWTs (Durchsicht 2026-09-27, Befund 18).
# Feste Strings, bewusst NICHT aus der Konfiguration: sie sind Teil des
# Token-Formats, nicht eine Betriebseinstellung. Wuerde man sie umstellen,
# waeren alle ausgegebenen Token ungueltig.
JWT_ISSUER = "selfmailer"
JWT_AUDIENCE = "selfmailer-api"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_access_token(subject: str, role: str, token_version: int = 0) -> str:
    """Zugriffs-Token.

    "tv" ist die Sitzungs-Version des Users (User.token_version, Befund 4):
    erhoeht der User sie (Passwortwechsel, 2FA aus, "ueberall abmelden"),
    werden alle aelteren Token ungueltig. Bestands-Token ohne "tv" gelten als
    Version 0 - siehe api/deps.get_current_user.
    """
    settings = get_settings()
    now = dt.datetime.now(dt.timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "tv": int(token_version),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
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
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + dt.timedelta(minutes=5),
    }
    return jwt.encode(payload, jwt_key(), algorithm=get_settings().jwt_algorithm)


def decode_token(token: str) -> dict[str, Any] | None:
    """Prueft Signatur/Ablauf und - falls vorhanden - iss/aud.

    Bewusst NICHT ueber PyJWTs audience=/issuer=-Parameter: die wuerden ein
    Token OHNE die Claims mit MissingRequiredClaim ablehnen. Bestands-Token
    (bis 1.95.5 ohne iss/aud) sollen bis zu ihrem Ablauf weiter gelten
    (Uebergang, Befund 18). Sind die Claims da, muessen sie stimmen -
    ein Token eines anderen Dienstes mit demselben Secret faellt damit durch.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            jwt_key(),
            algorithms=[settings.jwt_algorithm],
            options={"verify_aud": False},
        )
    except jwt.PyJWTError:
        return None
    iss = payload.get("iss")
    if iss is not None and iss != JWT_ISSUER:
        return None
    aud = payload.get("aud")
    if aud is not None:
        allowed = aud if isinstance(aud, (list, tuple)) else [aud]
        if JWT_AUDIENCE not in allowed:
            return None
    return payload


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
