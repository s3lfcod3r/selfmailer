"""2FA / TOTP: Status, Einrichtung, Aktivieren, zweistufiger Login."""
import time

from app.core import totp as totp_lib


def _current_code(secret: str, offset: int = 0) -> str:
    counter = int(time.time() // 30) + offset
    return totp_lib._hotp(totp_lib._b32decode(secret), counter)


def test_totp_status_default(client, admin):
    r = client.get("/api/v1/auth/totp/status", headers=admin)
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_totp_setup_and_wrong_code(client, admin):
    r = client.post("/api/v1/auth/totp/setup", headers=admin)
    assert r.status_code == 200
    body = r.json()
    assert "secret" in body and body["otpauth_uri"].startswith("otpauth://")
    # Falscher Code -> 400
    r = client.post("/api/v1/auth/totp/enable", headers=admin, json={"code": "000000"})
    assert r.status_code == 400


def test_totp_full_flow_and_two_step_login(client, admin):
    """Eigener Nutzer, damit der Admin-Login der anderen Tests nicht 2FA-gegated wird."""
    client.post("/api/v1/admin/users", headers=admin, json={
        "username": "totp@self", "password": "totp-pass-123", "role": "user",
    })
    # einloggen
    r = client.post("/api/v1/auth/login", json={"username": "totp@self", "password": "totp-pass-123"})
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # 2FA einrichten + aktivieren
    secret = client.post("/api/v1/auth/totp/setup", headers=h).json()["secret"]
    r = client.post("/api/v1/auth/totp/enable", headers=h, json={"code": _current_code(secret)})
    assert r.status_code == 200
    assert len(r.json()["backup_codes"]) > 0

    # Login ist jetzt zweistufig
    r = client.post("/api/v1/auth/login", json={"username": "totp@self", "password": "totp-pass-123"})
    assert r.status_code == 200
    j = r.json()
    assert j["needs_totp"] is True and j["mfa_token"]
    # zweiter Schritt: Code aus dem NÄCHSTEN Zeitfenster (der Aktivierungs-Code
    # ist durch den Replay-Schutz verbraucht). Window ±1 akzeptiert counter+1.
    r = client.post("/api/v1/auth/login/totp", json={"mfa_token": j["mfa_token"], "code": _current_code(secret, 1)})
    assert r.status_code == 200 and r.json()["access_token"]
