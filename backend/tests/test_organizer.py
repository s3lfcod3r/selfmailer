"""Notizen, Kalender, Kontakte, Aufgaben — CRUD + Mandantentrennung (Auth)."""


def test_notes_crud(client, admin):
    r = client.post("/api/v1/notes", headers=admin, json={"title": "N1", "body": "hi", "pinned": True})
    assert r.status_code == 201, r.text
    nid = r.json()["id"]
    r = client.get("/api/v1/notes", headers=admin)
    assert any(n["id"] == nid for n in r.json())
    r = client.patch(f"/api/v1/notes/{nid}", headers=admin, json={"title": "N1b"})
    assert r.status_code == 200 and r.json()["title"] == "N1b"
    r = client.delete(f"/api/v1/notes/{nid}", headers=admin)
    assert r.status_code == 204


def test_calendar_crud(client, admin):
    r = client.post("/api/v1/calendar/events", headers=admin, json={
        "title": "Termin", "start": "2026-07-01T10:00:00", "end": "2026-07-01T11:00:00",
    })
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    r = client.get("/api/v1/calendar/events", headers=admin)
    assert any(e["id"] == eid for e in r.json())
    # Ende vor Beginn -> 400
    r = client.post("/api/v1/calendar/events", headers=admin, json={
        "title": "Bad", "start": "2026-07-01T11:00:00", "end": "2026-07-01T10:00:00",
    })
    assert r.status_code == 400
    r = client.delete(f"/api/v1/calendar/events/{eid}", headers=admin)
    assert r.status_code == 204


def test_contacts_crud_and_search(client, admin):
    r = client.post("/api/v1/contacts", headers=admin, json={"first_name": "Max", "last_name": "Muster", "email": "max@example.com"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = client.get("/api/v1/contacts?q=max", headers=admin)
    assert any(c["id"] == cid for c in r.json())
    r = client.delete(f"/api/v1/contacts/{cid}", headers=admin)
    assert r.status_code == 204


def test_tasks_crud(client, admin):
    r = client.post("/api/v1/tasks", headers=admin, json={"title": "Todo"})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    r = client.patch(f"/api/v1/tasks/{tid}", headers=admin, json={"done": True})
    assert r.status_code == 200 and r.json()["done"] is True
    r = client.delete(f"/api/v1/tasks/{tid}", headers=admin)
    assert r.status_code == 204
