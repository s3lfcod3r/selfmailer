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


def test_note_summaries_search_body_without_returning_it(client, admin):
    body = "synthetic-private-body needle_%\\end"
    created = client.post("/api/v1/notes", headers=admin, json={"title": "Summary test", "body": body}).json()
    nid = created["id"]
    summaries = client.get("/api/v1/notes/summaries", headers=admin, params={"q": "needle_%\\end"})
    assert summaries.status_code == 200, summaries.text
    assert [n["id"] for n in summaries.json()] == [nid]
    assert all("body" not in n for n in summaries.json())
    assert body not in summaries.text
    # Explicit detail is owned; old Android full-list clients remain compatible.
    assert client.get(f"/api/v1/notes/{nid}", headers=admin).json()["body"] == body
    assert any(n["id"] == nid and n["body"] == body for n in client.get("/api/v1/notes", headers=admin).json())
    assert client.get("/api/v1/notes/summaries", headers=admin, params={"q": "needle_Xend"}).json() == []
    assert client.get("/api/v1/notes/summaries", headers=admin, params={"q": "x" * 201}).status_code == 422


def test_note_summary_wildcards_are_literal_and_title_search_is_case_insensitive(client, admin):
    nid = client.post("/api/v1/notes", headers=admin, json={"title": "UniqueSummaryMixedCASE", "body": "without wildcard"}).json()["id"]
    assert all(n["id"] != nid for n in client.get("/api/v1/notes/summaries", headers=admin, params={"q": "%"}).json())
    assert all(n["id"] != nid for n in client.get("/api/v1/notes/summaries", headers=admin, params={"q": "_"}).json())
    rows = client.get("/api/v1/notes/summaries", headers=admin, params={"q": "uniquesummarymixedcase"}).json()
    assert [n["id"] for n in rows] == [nid]


def test_note_summary_and_detail_require_authentication(client):
    client.cookies.clear()
    assert client.get("/api/v1/notes/summaries").status_code == 401
    assert client.get("/api/v1/notes/1").status_code == 401


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
