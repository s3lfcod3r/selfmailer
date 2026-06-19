"""At-Rest-Verschluesselung fuer fremde Mailkonto-Zugangsdaten.

Der Fernet-Key wird deterministisch aus SELFMAILER_SECRET abgeleitet, damit ein
Neustart dieselben Daten wieder entschluesseln kann. Klartext existiert nur
transient im Speicher waehrend einer IMAP/SMTP-Verbindung.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


def _fernet() -> Fernet:
    secret = get_settings().secret.encode("utf-8")
    # 32-Byte-Key aus SHA-256 des Secrets, urlsafe-base64 fuer Fernet.
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    """Verschluesselt einen String -> speicherbarer Token (str)."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """Entschluesselt einen Token zurueck zum Klartext.

    Raises ValueError, wenn der Token nicht (mehr) entschluesselbar ist,
    z. B. nach Secret-Wechsel.
    """
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:  # pragma: no cover - defensiv
        raise ValueError("Zugangsdaten nicht entschluesselbar (Secret geaendert?)") from exc
