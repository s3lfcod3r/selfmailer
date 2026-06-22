"""At-Rest-Verschluesselung fuer fremde Mailkonto-Zugangsdaten.

Der Fernet-Key wird aus SELFMAILER_SECRET abgeleitet, damit ein Neustart
dieselben Daten wieder entschluesseln kann. Klartext existiert nur transient im
Speicher waehrend einer IMAP/SMTP-Verbindung.

Schluesseltrennung (wichtig): Fernet-Key und JWT-Signaturschluessel werden per
HKDF mit unterschiedlichen ``info``-Tags aus demselben Master-Secret abgeleitet
und sind dadurch kryptografisch unabhaengig. Ein geleakter JWT-Schluessel gibt
damit NICHT automatisch die Fernet-verschluesselten Passwoerter frei (und
umgekehrt). Vor dieser Umstellung gespeicherte Tokens (nackter SHA-256-Key)
bleiben ueber MultiFernet als Legacy-Fallback entschluesselbar.
"""
from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import get_settings

# HKDF-Domain-Trennung: gleiche Master-Quelle, unabhaengige Unterschluessel.
_FERNET_INFO = b"selfmailer-fernet-v1"
_JWT_INFO = b"selfmailer-jwt-v1"


def _hkdf(info: bytes, length: int = 32) -> bytes:
    secret = get_settings().secret.encode("utf-8")
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(secret)


@lru_cache(maxsize=1)
def _fernet() -> MultiFernet:
    secret = get_settings().secret.encode("utf-8")
    # Primaer: HKDF-abgeleiteter, vom JWT-Schluessel unabhaengiger Key (neue Daten).
    primary = base64.urlsafe_b64encode(_hkdf(_FERNET_INFO))
    # Fallback: Legacy-Key (nackter SHA-256) — entschluesselt Altbestaende von vor
    # der HKDF-Umstellung. Wird nur fuer decrypt herangezogen, nie zum encrypt.
    legacy = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return MultiFernet([Fernet(primary), Fernet(legacy)])


@lru_cache(maxsize=1)
def jwt_key() -> bytes:
    """HMAC-Schluessel fuer die JWT-Signatur — per HKDF unabhaengig vom Fernet-Key."""
    return _hkdf(_JWT_INFO)


def encrypt(plaintext: str) -> str:
    """Verschluesselt einen String -> speicherbarer Token (str)."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """Entschluesselt einen Token zurueck zum Klartext.

    Raises ValueError, wenn der Token mit keinem (aktuellen oder Legacy-)Key mehr
    entschluesselbar ist, z. B. nach Secret-Wechsel.
    """
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:  # pragma: no cover - defensiv
        raise ValueError("Zugangsdaten nicht entschluesselbar (Secret geaendert?)") from exc
