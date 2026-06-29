"""Filterregeln inkl. Lösch-Aktion, Absender blockieren und Spam-Auto-Purge.

IMAP wird hier NICHT angesprochen (Fake-Konto). Daher: Regel-CRUD auf API/DB-
Ebene, die Konto-Einstellung spam_purge_days, und block-sender mit
delete_existing=False (legt nur die Regel an, ohne IMAP-Zugriff).
"""


def test_rule_crud_with_delete_action(client, admin, account):
    # Lösch-Regel anlegen
    r = client.post(f"/api/v1/mail/{account}/rules", headers=admin, json={
        "field": "from", "value": "spam@bad.example", "delete_msg": True,
    })
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["delete_msg"] is True
    assert rule["field"] == "from"
    assert rule["value"] == "spam@bad.example"
    rid = rule["id"]

    # Auslesen liefert das Feld zurück
    r = client.get(f"/api/v1/mail/{account}/rules", headers=admin)
    assert r.status_code == 200
    assert any(x["id"] == rid and x["delete_msg"] is True for x in r.json())

    # Lösch-Aktion per PATCH wieder abschalten
    r = client.patch(f"/api/v1/mail/{account}/rules/{rid}", headers=admin, json={"delete_msg": False})
    assert r.status_code == 200
    assert r.json()["delete_msg"] is False

    # Aufräumen
    assert client.delete(f"/api/v1/mail/{account}/rules/{rid}", headers=admin).status_code == 204


def test_account_spam_purge_days_roundtrip(client, admin, account):
    # Default ist -1 (aus)
    accs = client.get("/api/v1/accounts", headers=admin).json()
    me = next(a for a in accs if a["id"] == account)
    assert me["spam_purge_days"] == -1

    # Setzen + zurücklesen
    r = client.patch(f"/api/v1/accounts/{account}", headers=admin, json={"spam_purge_days": 7})
    assert r.status_code == 200, r.text
    assert r.json()["spam_purge_days"] == 7

    # Ungültige Werte (zu klein) werden abgelehnt
    r = client.patch(f"/api/v1/accounts/{account}", headers=admin, json={"spam_purge_days": -5})
    assert r.status_code == 422


def test_block_sender_creates_delete_rule_idempotent(client, admin, account):
    # delete_existing=False -> kein IMAP-Zugriff, nur Regel anlegen
    r = client.post(f"/api/v1/mail/{account}/block-sender", headers=admin, json={
        "sender": "nervig@werbung.example", "delete_existing": False,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 0
    assert body["rule"]["delete_msg"] is True
    assert body["rule"]["field"] == "from"
    assert body["rule"]["value"] == "nervig@werbung.example"

    # Nochmal denselben Absender blockieren -> keine zweite Regel (idempotent)
    r2 = client.post(f"/api/v1/mail/{account}/block-sender", headers=admin, json={
        "sender": "nervig@werbung.example", "delete_existing": False,
    })
    assert r2.status_code == 200
    assert r2.json()["rule"]["id"] == body["rule"]["id"]

    rules = client.get(f"/api/v1/mail/{account}/rules", headers=admin).json()
    matching = [x for x in rules if x["value"] == "nervig@werbung.example"]
    assert len(matching) == 1


def test_account_trash_purge_days_roundtrip(client, admin, account):
    accs = client.get("/api/v1/accounts", headers=admin).json()
    me = next(a for a in accs if a["id"] == account)
    assert me["trash_purge_days"] == -1

    r = client.patch(f"/api/v1/accounts/{account}", headers=admin, json={"trash_purge_days": 30})
    assert r.status_code == 200, r.text
    assert r.json()["trash_purge_days"] == 30

    r = client.patch(f"/api/v1/accounts/{account}", headers=admin, json={"trash_purge_days": -5})
    assert r.status_code == 422


def test_block_sender_sets_trash_reue_window(client, admin, account):
    # Frisches Konto: Papierkorb-Auto-Purge aus (-1)
    accs = client.get("/api/v1/accounts", headers=admin).json()
    assert next(a for a in accs if a["id"] == account)["trash_purge_days"] == -1

    # Blockieren (ohne IMAP-Zugriff) setzt das 7-Tage-Reue-Fenster
    r = client.post(f"/api/v1/mail/{account}/block-sender", headers=admin, json={
        "sender": "x@y.example", "delete_existing": False,
    })
    assert r.status_code == 200, r.text

    accs = client.get("/api/v1/accounts", headers=admin).json()
    assert next(a for a in accs if a["id"] == account)["trash_purge_days"] == 7


def test_block_sender_keeps_existing_trash_setting(client, admin, account):
    # Hat der Nutzer bereits einen Wert gesetzt (z. B. 30), überschreibt Blockieren ihn NICHT
    client.patch(f"/api/v1/accounts/{account}", headers=admin, json={"trash_purge_days": 30})
    client.post(f"/api/v1/mail/{account}/block-sender", headers=admin, json={
        "sender": "z@y.example", "delete_existing": False,
    })
    accs = client.get("/api/v1/accounts", headers=admin).json()
    assert next(a for a in accs if a["id"] == account)["trash_purge_days"] == 30


def test_block_sender_by_domain(client, admin, account):
    r = client.post(f"/api/v1/mail/{account}/block-sender", headers=admin, json={
        "sender": "spammer.example", "by_domain": True, "delete_existing": False,
    })
    assert r.status_code == 200, r.text
    assert r.json()["rule"]["field"] == "from_domain"


def test_block_sender_rejects_empty(client, admin, account):
    r = client.post(f"/api/v1/mail/{account}/block-sender", headers=admin, json={
        "sender": "   ", "delete_existing": False,
    })
    assert r.status_code == 422
