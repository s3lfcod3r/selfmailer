"""Mailkonten: anlegen, listen, patchen, löschen — secret_enc nie ausgegeben."""


def test_account_crud(client, admin):
    r = client.post("/api/v1/accounts", headers=admin, json={
        "label": "Web", "email": "me@example.com", "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com", "password": "geheim",
    })
    assert r.status_code == 201, r.text
    acc = r.json()
    aid = acc["id"]
    # Response gibt NIE das Passwort / secret_enc aus
    assert "secret_enc" not in acc
    assert "password" not in acc
    assert acc["email"] == "me@example.com"

    # listen
    r = client.get("/api/v1/accounts", headers=admin)
    assert r.status_code == 200
    assert any(a["id"] == aid for a in r.json())

    # patchen
    r = client.patch(f"/api/v1/accounts/{aid}", headers=admin, json={"label": "Neu"})
    assert r.status_code == 200
    assert r.json()["label"] == "Neu"

    # loeschen
    r = client.delete(f"/api/v1/accounts/{aid}", headers=admin)
    assert r.status_code == 204
    r = client.get("/api/v1/accounts", headers=admin)
    assert all(a["id"] != aid for a in r.json())


def test_account_requires_auth(client):
    r = client.get("/api/v1/accounts")
    assert r.status_code in (401, 403)
