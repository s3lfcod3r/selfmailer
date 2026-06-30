"""TOTP (RFC 6238) + Backup-Codes — abhängigkeitsfrei.

Implementiert wie im SelfDashboard: Base32-Secret, HMAC-SHA1-HOTP, 6 Ziffern,
30s-Schritt, Prüf-Fenster +/-1 Schritt. Replay-Schutz über den zuletzt
konsumierten Zeitschritt (Aufrufer speichert ihn am User). Das Secret wird vom
Aufrufer Fernet-verschlüsselt at-rest abgelegt ([[crypto]]).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import time

_BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_STEP_SEC = 30
_DIGITS = 6
BACKUP_CODE_COUNT = 8
_ISSUER = "SelfMailer"


def _b32encode(data: bytes) -> str:
    """RFC 4648 Base32 ohne Padding (Authenticator-Apps mögen kein '=')."""
    return base64.b32encode(data).decode("ascii").rstrip("=")


def _b32decode(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    s = "".join(ch for ch in s if ch in _BASE32)
    pad = (-len(s)) % 8
    return base64.b32decode(s + "=" * pad)


def generate_secret() -> str:
    """Neues zufälliges Base32-Secret (20 Byte = 160 Bit, wie RFC-Empfehlung)."""
    return _b32encode(secrets.token_bytes(20))


def build_otpauth_uri(account_label: str, secret: str) -> str:
    """otpauth://-URI für QR-Code / manuelle Eingabe in der Authenticator-App."""
    from urllib.parse import quote

    label = quote(f"{_ISSUER}:{account_label}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={_ISSUER}"
        f"&algorithm=SHA1&digits={_DIGITS}&period={_STEP_SEC}"
    )


def _hotp(secret: bytes, counter: int) -> str:
    msg = counter.to_bytes(8, "big")
    digest = hmac.new(secret, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(code % (10 ** _DIGITS)).zfill(_DIGITS)


def verify_code_step(secret_b32: str, token: str, window: int = 1) -> int | None:
    """Prüft einen TOTP-Code und gibt den passenden Zeitschritt zurück.

    Rückgabe = Schrittzähler (für Replay-Schutz) oder None, wenn kein Schritt
    im Fenster passt. Vergleich konstant-zeitig.
    """
    code = token.strip().replace(" ", "")
    if not (code.isdigit() and len(code) == _DIGITS):
        return None
    try:
        secret = _b32decode(secret_b32)
    except (ValueError, binascii.Error):
        return None
    if len(secret) < 10:
        return None
    now_step = int(time.time()) // _STEP_SEC
    for offset in range(-window, window + 1):
        step = now_step + offset
        if hmac.compare_digest(_hotp(secret, step), code):
            return step
    return None


def generate_backup_codes() -> list[str]:
    """Liste leserlicher Einmal-Codes im Format XXXX-XXXX (Klartext, nur einmal)."""
    codes: list[str] = []
    for _ in range(BACKUP_CODE_COUNT):
        raw = secrets.token_hex(4).upper()  # 8 Hex-Zeichen
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


def normalize_backup_code(raw: str) -> str:
    """Vereinheitlicht Eingabe (Bindestriche/Leerzeichen weg, Großschreibung)."""
    return raw.replace("-", "").replace(" ", "").upper()
