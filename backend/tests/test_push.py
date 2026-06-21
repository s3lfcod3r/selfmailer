"""Push: ntfy-Konfig, Ordner-Auswahl je Konto, FCM-Geräte-Tokens, Test-Push."""


def test_push_config(client, admin):
    r = client.get("/api/v1/push", headers=admin)
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    r = client.put("/api/v1/push", headers=admin, json={
        "ntfy_url": "http://ntfy.example.com", "topic": "secret-topic", "enabled": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["topic"] == "secret-topic"


def test_notify_folders(client, admin, account):
    r = client.get(f"/api/v1/push/folders?account_id={account}", headers=admin)
    assert r.status_code == 200 and r.json() == []
    r = client.put("/api/v1/push/folders", headers=admin, json={"account_id": account, "folders": ["INBOX", "Archive"]})
    assert r.status_code == 200
    assert sorted(r.json()) == ["Archive", "INBOX"]
    r = client.get(f"/api/v1/push/folders?account_id={account}", headers=admin)
    assert sorted(r.json()) == ["Archive", "INBOX"]
    # Auswahl ersetzen
    r = client.put("/api/v1/push/folders", headers=admin, json={"account_id": account, "folders": ["INBOX"]})
    assert r.json() == ["INBOX"]


def test_notify_folders_foreign_account(client, admin):
    r = client.get("/api/v1/push/folders?account_id=999999", headers=admin)
    assert r.status_code == 404


def test_device_and_test_push(client, admin):
    r = client.post("/api/v1/push/device", headers=admin, json={"token": "fake-device-token"})
    assert r.status_code == 204
    r = client.post("/api/v1/push/test", headers=admin)
    assert r.status_code == 200
    body = r.json()
    # Diagnose: FCM ohne Service-Account aus, aber Token ist registriert.
    assert body["fcm_enabled"] is False
    assert body["device_tokens"] >= 1
    r = client.request("DELETE", "/api/v1/push/device", headers=admin, json={"token": "fake-device-token"})
    assert r.status_code == 204


def test_dashboard_summary(client, admin):
    r = client.get("/api/v1/dashboard/summary", headers=admin)
    assert r.status_code == 200
    body = r.json()
    assert "total_unseen" in body and "accounts" in body
