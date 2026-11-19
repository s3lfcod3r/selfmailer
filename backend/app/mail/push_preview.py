"""Verschluesselte Vorschau im FCM-Push (seit 1.98.2).

Bis 1.98.1 war der Google-Push absichtlich leer ("Neue E-Mail"), und die App
holte Absender/Betreff danach selbst vom Server. Das klappt nur, wenn das Handy
den Server in dem Moment erreicht - unterwegs im Mobilfunk oft nicht, dann blieb
es bei "Neue E-Mail".

Jetzt legt jedes Geraet bei der Anmeldung den oeffentlichen Teil eines eigenen
RSA-Schluessels ab (der private Teil liegt nicht exportierbar im Android-
Keystore). Der Server verschluesselt die Vorschau je Geraet damit und legt sie
in den Push. Google transportiert nur unlesbaren Text, die App entschluesselt
lokal und braucht keine Verbindung zum Server.

Format (alles base64url ohne Padding, mit Punkten getrennt):
    v1.<RSA-OAEP(AES-Schluessel)>.<Nonce>.<AES-256-GCM(JSON)>
RSA-OAEP mit SHA-256 und MGF1-SHA-1: genau diese Kombination kann der
Android-Keystore auf allen Versionen ab Android 8.
"""
from __future__ import annotations

import base64
import binascii
import json
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = "v1"
AAD = b"selfmailer-push-preview-v1"
MIN_KEY_BITS = 2048
MAX_KEY_BITS = 4096
# Laengen in Zeichen: haelt den Push sicher unter der FCM-Grenze von 4 KB.
MAX_LABEL = 60
MAX_SENDER = 80
MAX_SUBJECT = 160

_OAEP = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA1()), algorithm=hashes.SHA256(), label=None)


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def load_public_key(key_b64: str) -> rsa.RSAPublicKey | None:
    """Oeffentlichen Geraete-Schluessel pruefen (X.509 DER, base64). None = unbrauchbar."""
    s = (key_b64 or "").strip()
    if not s or len(s) > 1024:
        return None
    try:
        raw = base64.b64decode(s + "=" * (-len(s) % 4), altchars=b"-_", validate=False)
        key = serialization.load_der_public_key(raw)
    except (ValueError, TypeError, binascii.Error):
        return None
    if not isinstance(key, rsa.RSAPublicKey) or not MIN_KEY_BITS <= key.key_size <= MAX_KEY_BITS:
        return None
    return key


def normalize_public_key(key_b64: str) -> str:
    """Gueltigen Schluessel in einheitlicher Form zurueckgeben, sonst ''."""
    key = load_public_key(key_b64)
    if key is None:
        return ""
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode("ascii")


def _clip(text: str | None, limit: int) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def build_payload(label: str, sender: str | None, subject: str | None, count: int, folder_leaf: str | None) -> dict:
    data: dict = {"a": _clip(label, MAX_LABEL), "n": max(1, int(count))}
    if sender:
        data["f"] = _clip(sender, MAX_SENDER)
    if subject:
        data["s"] = _clip(subject, MAX_SUBJECT)
    if folder_leaf:
        data["o"] = _clip(folder_leaf, MAX_LABEL)
    return data


def encrypt(key_b64: str, payload: dict) -> str | None:
    """Vorschau fuer genau ein Geraet verschluesseln. None, wenn der Schluessel nicht taugt."""
    key = load_public_key(key_b64)
    if key is None:
        return None
    aes_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    plain = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ct = AESGCM(aes_key).encrypt(nonce, plain, AAD)
    wrapped = key.encrypt(aes_key, _OAEP)
    return ".".join((VERSION, _b64e(wrapped), _b64e(nonce), _b64e(ct)))


def decrypt(private_key: rsa.RSAPrivateKey, blob: str) -> dict:
    """Gegenstueck fuer Tests (die App macht dasselbe in Kotlin)."""
    version, wrapped, nonce, ct = blob.split(".")
    if version != VERSION:
        raise ValueError("Unbekannte Version")
    aes_key = private_key.decrypt(_b64d(wrapped), _OAEP)
    return json.loads(AESGCM(aes_key).decrypt(_b64d(nonce), _b64d(ct), AAD))
