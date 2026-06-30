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

    # löschen
    r = client.delete(f"/api/v1/accounts/{aid}", headers=admin)
    assert r.status_code == 204
    r = client.get("/api/v1/accounts", headers=admin)
    assert all(a["id"] != aid for a in r.json())


def test_account_requires_auth(client):
    r = client.get("/api/v1/accounts")
    assert r.status_code in (401, 403)


def test_account_rejects_internal_host_ssrf(client, admin):
    """SSRF-Schutz: imap_host darf kein internes Ziel (Loopback/Metadata) sein.

    conftest neutralisiert den Validator global für die uebrigen CRUD-Tests;
    hier stellen wir die echte Prüfung gezielt wieder her und erwarten 400.
    Loopback (127.0.0.1) wird unabhängig von dav_block_private IMMER geblockt.
    """
    import app.api.accounts as accounts_mod
    from app.dav.client import validate_external_url as real_validate

    neutralized = accounts_mod.validate_external_url
    accounts_mod.validate_external_url = real_validate
    try:
        r = client.post("/api/v1/accounts", headers=admin, json={
            "label": "Bad", "email": "x@example.com",
            "imap_host": "127.0.0.1", "smtp_host": "smtp.example.com",
            "password": "pw",
        })
        assert r.status_code == 400, r.text
        assert "IMAP-Host" in r.json()["detail"]
    finally:
        accounts_mod.validate_external_url = neutralized
