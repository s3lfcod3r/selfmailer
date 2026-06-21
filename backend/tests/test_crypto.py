"""At-Rest-Verschlüsselung (Fernet) — Roundtrip + Ciphertext ≠ Klartext."""
from app.core.crypto import decrypt, encrypt


def test_encrypt_decrypt_roundtrip():
    plain = "geheimes-postfach-passwort!äöü"
    token = encrypt(plain)
    assert token != plain
    assert decrypt(token) == plain


def test_ciphertext_is_not_deterministic():
    # Fernet nutzt einen Zufalls-IV -> zwei Verschlüsselungen unterscheiden sich.
    a = encrypt("same")
    b = encrypt("same")
    assert a != b
    assert decrypt(a) == decrypt(b) == "same"
