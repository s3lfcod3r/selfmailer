"""Mehrbenutzer-Trennung: User B kommt nicht an die Objekte von User A.

Der Code prüft Ownership konsequent über _owned()-Helfer (account_id/user_id),
aber bis hierher lief die GESAMTE Testsuite nur mit einem einzigen Admin-User —
die Trennung war unverifiziert. Ein Refactoring, das _owned() an einer Route
vergisst, wäre grün durchgelaufen. Dieser Test schließt die Lücke:

  - B sieht A's Notizen nicht und kann sie nicht ändern/löschen (404).
  - B kann A's Mailkonto nicht ändern/löschen/testen (404).
  - Ein Nicht-Admin wird von den Admin-Routen abgewiesen (403).
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def user_b(client, admin) -> dict:
    """Legt (einmalig) einen Nicht-Admin an und gibt dessen Bearer-Header."""
    client.post("/api/v1/admin/users", headers=admin, json={
        "username": "bob@self", "password": "bob-supersecret-123",
        "display_name": "Bob", "role": "user",
    })  # 201 oder 409 (schon vorhanden) — beides ok
    r = client.post("/api/v1/auth/login",
                    json={"username": "bob@self", "password": "bob-supersecret-123"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    client.cookies.clear()  # rein über Bearer testen (wie die admin-Fixture)
    return {"Authorization": f"Bearer {token}"}


def test_b_sieht_und_aendert_a_notizen_nicht(client, admin, user_b):
    # A legt eine Notiz an.
    r = client.post("/api/v1/notes", headers=admin,
                    json={"title": "Geheim A", "body": "nur fuer A"})
    assert r.status_code == 201, r.text
    note_id = r.json()["id"]

    # B sieht sie nicht in der eigenen Liste.
    r = client.get("/api/v1/notes", headers=user_b)
    assert r.status_code == 200
    assert all(n["id"] != note_id for n in r.json()), "B sieht A's Notiz in der Liste"
    summaries = client.get("/api/v1/notes/summaries", headers=user_b, params={"q": "nur fuer A"})
    assert summaries.status_code == 200
    assert all(n["id"] != note_id for n in summaries.json())
    assert client.get(f"/api/v1/notes/{note_id}", headers=user_b).status_code == 404
    assert client.get(f"/api/v1/notes/{note_id}", headers=admin).json()["body"] == "nur fuer A"

    # B kann sie weder ändern noch löschen (404 — Existenz wird nicht verraten).
    assert client.patch(f"/api/v1/notes/{note_id}", headers=user_b,
                        json={"title": "gekapert"}).status_code == 404
    assert client.delete(f"/api/v1/notes/{note_id}", headers=user_b).status_code == 404

    # Gegenprobe: A's Notiz ist unverändert vorhanden.
    r = client.get("/api/v1/notes", headers=admin)
    assert any(n["id"] == note_id and n["title"] == "Geheim A" for n in r.json())


def test_b_kann_a_mailkonto_nicht_anfassen(client, admin, account, user_b):
    # `account` gehört A (per admin-Fixture angelegt).
    assert client.patch(f"/api/v1/accounts/{account}", headers=user_b,
                        json={"label": "gekapert"}).status_code == 404
    assert client.post(f"/api/v1/accounts/{account}/test",
                       headers=user_b).status_code == 404
    assert client.delete(f"/api/v1/accounts/{account}",
                        headers=user_b).status_code == 404
    # B's eigene Kontoliste ist leer (A's Konto taucht nicht auf).
    r = client.get("/api/v1/accounts", headers=user_b)
    assert r.status_code == 200
    assert all(a["id"] != account for a in r.json())


def test_nicht_admin_wird_von_admin_routen_abgewiesen(client, user_b):
    assert client.get("/api/v1/admin/users", headers=user_b).status_code == 403
    assert client.post("/api/v1/admin/users", headers=user_b, json={
        "username": "x@self", "password": "x-supersecret-123", "role": "user",
    }).status_code == 403
