"""Verschluesselte Vorschau im FCM-Push (1.98.2)."""
import base64
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from sqlmodel import Session, select

from app.core.db import engine
from app.mail import fcm, push, push_preview
from app.models import CachedMessage, DeviceToken, MailAccount


def _key(bits: int = 2048):
    priv = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    der = priv.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return priv, base64.b64encode(der).decode()


def test_roundtrip_and_no_plaintext():
    priv, pub = _key()
    payload = push_preview.build_payload("Web.de", "Kleinanzeigen", "Anfrage zu deiner Anzeige", 1, None)
    blob = push_preview.encrypt(pub, payload)
    assert blob.startswith("v1.") and blob.count(".") == 3
    assert "Kleinanzeigen" not in blob and "Anfrage" not in blob
    assert push_preview.decrypt(priv, blob) == {"a": "Web.de", "n": 1, "f": "Kleinanzeigen", "s": "Anfrage zu deiner Anzeige"}
    # Jeder Push bekommt einen neuen Schluessel/Nonce: gleicher Inhalt -> anderer Text.
    assert push_preview.encrypt(pub, payload) != blob


def test_url_safe_key_from_android_is_accepted():
    _, pub = _key()
    url_safe = pub.replace("+", "-").replace("/", "_").rstrip("=")
    assert push_preview.normalize_public_key(url_safe) == pub


def test_bad_keys_are_rejected():
    _, small = _key(1024)
    ec_der = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    for bad in ("", "abc", "!!!", small, base64.b64encode(ec_der).decode(), "A" * 2000):
        assert push_preview.normalize_public_key(bad) == ""
        assert push_preview.encrypt(bad, {"a": "x", "n": 1}) is None


def test_long_unicode_stays_under_fcm_limit():
    priv, pub = _key(4096)
    payload = push_preview.build_payload("😀" * 500, "𝔄" * 500, "Ü" * 5000, 3, "Ordner" * 50)
    assert len(payload["s"]) == push_preview.MAX_SUBJECT and payload["s"].endswith("…")
    blob = push_preview.encrypt(pub, payload)
    assert len(blob.encode()) < 3000          # FCM: 4 KB fuer alle Datenfelder zusammen
    assert push_preview.decrypt(priv, blob)["n"] == 3


def test_register_device_stores_only_valid_key(client, admin):
    _, pub = _key()
    r = client.post("/api/v1/push/device", headers=admin,
                    json={"token": "tok-mit-key", "session_id": "s1", "preview_key": pub})
    assert r.status_code == 204
    r = client.post("/api/v1/push/device", headers=admin,
                    json={"token": "tok-kaputt", "session_id": "s1", "preview_key": "kein-schluessel"})
    assert r.status_code == 204
    with Session(engine) as s:
        rows = {d.token: d.preview_key for d in s.exec(select(DeviceToken).where(
            DeviceToken.token.in_(["tok-mit-key", "tok-kaputt"]))).all()}
    assert rows == {"tok-mit-key": pub, "tok-kaputt": ""}
    for t in rows:
        client.request("DELETE", "/api/v1/push/device", headers=admin, json={"token": t})


def test_device_data_only_encrypts_for_devices_with_key():
    priv, pub = _key()
    base = {"type": "mail", "title": "SelfMailer", "body": "Neue E-Mail"}
    vorschau = {"a": "Web.de", "n": 1, "f": "Absender", "s": "Betreff"}
    mit = fcm._device_data(DeviceToken(user_id=1, token="t", session_id="s", preview_key=pub), base, vorschau)
    ohne = fcm._device_data(DeviceToken(user_id=1, token="t", session_id="s"), base, vorschau)
    assert ohne == {**base, "session_id": "s"}
    assert push_preview.decrypt(priv, mit.pop("preview")) == vorschau
    assert mit == {**base, "session_id": "s"}
    assert "Betreff" not in json.dumps(mit) and "Absender" not in json.dumps(mit)
    # Refresh-Push ohne Vorschau bleibt unveraendert.
    assert fcm._device_data(DeviceToken(user_id=1, token="t", session_id="s", preview_key=pub),
                            {"type": "refresh"}, None) == {"type": "refresh", "session_id": "s"}


def test_push_new_mail_passes_preview(client, admin, account, monkeypatch):
    gesendet = {}
    monkeypatch.setattr(fcm, "_send_all", lambda session, user_id, data, preview=None:
                        gesendet.update(data=data, preview=preview))
    with Session(engine) as s:
        acc = s.get(MailAccount, account)
        s.add(CachedMessage(account_id=account, folder="INBOX", uid="77", uidvalidity=5,
                            subject="Anfrage zu deiner Anzeige",
                            from_addr='"Kleinanzeigen" <noreply@mail.kleinanzeigen.de>', seen=False))
        s.commit()
        push.push_new_mail(s, acc, "INBOX", 1)
    assert gesendet["data"]["body"] == "Neue E-Mail"            # Klartext bleibt leer
    assert gesendet["data"]["uid"] == "77"
    assert gesendet["preview"] == {"a": "Test", "n": 1, "f": "Kleinanzeigen", "s": "Anfrage zu deiner Anzeige"}
