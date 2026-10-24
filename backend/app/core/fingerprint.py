"""SHA-256-Zertifikats-Fingerprints (IMAP-Zertifikat-Pinning, siehe mail/imap.py).

Eigenes, abhaengigkeitsfreies Modul, damit schemas.py (Eingabepruefung) und
mail/imap.py (Verbindungsaufbau) dieselbe Normalisierung benutzen.
"""
from __future__ import annotations

import re

_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")
# Gaengige Vorsaetze, wenn jemand die Ausgabe von
# `openssl x509 -noout -fingerprint -sha256` komplett einfuegt.
_PREFIXES = ("sha256fingerprint=", "sha256=")


def normalize_fingerprint(value: str | None) -> str:
    """Vereinheitlichen: Kleinbuchstaben, ohne ':'/Leerzeichen.

    Leer bleibt leer (= Standardpruefung). Alles andere muss 64 Hex-Zeichen
    ergeben, sonst ValueError - ein Tippfehler darf nie still zu "irgendein
    Zertifikat" werden.
    """
    raw = re.sub(r"[\s:]", "", value or "").lower()
    for prefix in _PREFIXES:
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    if not raw:
        return ""
    if not _FINGERPRINT_RE.fullmatch(raw):
        raise ValueError("Fingerprint muss ein SHA-256-Wert sein (64 Hex-Zeichen)")
    return raw
