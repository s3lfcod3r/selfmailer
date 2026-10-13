import uuid
import pytest


@pytest.fixture
def phone_owner(client, admin):
    username = "phone-" + uuid.uuid4().hex
    created = client.post("/api/v1/admin/users", headers=admin, json={"username": username, "password": "test-phone-password"})
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    login = client.post("/api/v1/auth/login", json={"username": username, "password": "test-phone-password"})
    client.cookies.clear()
    header = {"Authorization": "Bearer " + login.json()["access_token"]}
    yield header
    assert client.delete(f"/api/v1/admin/users/{user_id}", headers=admin).status_code == 204


def request(client, header, *, contacts=None, device="test-device-1", preview=False, linked_only=False):
    return client.post("/api/v1/contacts/phone/" + ("preview" if preview else "import"), headers=header,
                       json={"device_id": device, "contacts": contacts or [entry()], "linked_only": linked_only})


def entry(**changes):
    return {"source_key": "lookup-1", "first_name": "Anna", "last_name": "Example", "email": "anna@example.org",
            "mobile": "+49 123456789", **changes}


def test_preview_writes_nothing_and_repeated_import_is_idempotent(client, phone_owner):
    header = phone_owner
    r = request(client, header, preview=True)
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["status"] == "new"
    assert client.get("/api/v1/contacts", headers=header).json() == []
    payload = entry(expected_digest=r.json()["items"][0]["expected_digest"])
    first = request(client, header, contacts=[payload])
    assert first.status_code == 200, first.text
    second = request(client, header)
    assert second.json()["items"][0]["status"] == "unchanged"
    assert len(client.get("/api/v1/contacts", headers=header).json()) == 1


def test_changed_phone_updates_linked_contact_not_a_new_contact(client, phone_owner):
    request(client, phone_owner)
    r = request(client, phone_owner, contacts=[entry(mobile="+49123456780")], linked_only=True)
    assert r.json()["changed"] == 1, r.text
    rows = client.get("/api/v1/contacts", headers=phone_owner).json()
    assert len(rows) == 1 and rows[0]["mobile"] == "+49123456780"


def test_server_edit_survives_unchanged_sync_then_conflicting_source_change(client, phone_owner):
    request(client, phone_owner)
    contact = client.get("/api/v1/contacts", headers=phone_owner).json()[0]
    client.patch(f"/api/v1/contacts/{contact['id']}", headers=phone_owner, json={"mobile": "server-edit"})
    assert request(client, phone_owner).json()["items"][0]["status"] == "unchanged"
    r = request(client, phone_owner, contacts=[entry(mobile="source-edit")], linked_only=True)
    assert r.json()["items"][0]["status"] == "conflict"
    assert client.get("/api/v1/contacts", headers=phone_owner).json()[0]["mobile"] == "server-edit"


def test_duplicate_email_case_and_phone_format_match_existing_contact(client, phone_owner):
    ct = client.post("/api/v1/contacts", headers=phone_owner, json={"email": "ANNA@example.org", "mobile": "0049 123456789"}).json()
    r = request(client, phone_owner)
    assert r.json()["items"][0]["status"] == "match", r.text
    assert r.json()["items"][0]["contact_id"] == ct["id"]
    assert len(client.get("/api/v1/contacts", headers=phone_owner).json()) == 1


def test_ambiguous_identifiers_are_not_merged(client, phone_owner):
    client.post("/api/v1/contacts", headers=phone_owner, json={"email": "anna@example.org"})
    client.post("/api/v1/contacts", headers=phone_owner, json={"mobile": "+49123456789"})
    r = request(client, phone_owner)
    assert r.json()["items"][0]["status"] == "ambiguous", r.text
    assert r.json()["changed"] == 0


def test_name_only_match_requires_manual_review(client, phone_owner):
    client.post("/api/v1/contacts", headers=phone_owner, json={"first_name": "Anna", "last_name": "Example"})
    assert request(client, phone_owner).json()["items"][0]["status"] == "possible_duplicate"


def test_deleted_server_contact_is_not_recreated(client, phone_owner):
    request(client, phone_owner)
    contact = client.get("/api/v1/contacts", headers=phone_owner).json()[0]
    assert client.delete(f"/api/v1/contacts/{contact['id']}", headers=phone_owner).status_code == 204
    r = request(client, phone_owner, linked_only=True)
    assert r.json()["items"][0]["status"] == "server_deleted", r.text
    assert client.get("/api/v1/contacts", headers=phone_owner).json() == []


def test_automatic_sync_never_imports_unlinked_contacts(client, phone_owner):
    r = request(client, phone_owner, linked_only=True)
    assert r.json()["items"][0]["status"] == "not_linked"
    assert client.get("/api/v1/contacts", headers=phone_owner).json() == []


def test_all_additional_addresses_and_numbers_are_preserved(client, phone_owner):
    values = entry(extra_emails=["other@example.org"], extra_phones=["+498888888888"])
    assert request(client, phone_owner, contacts=[values]).status_code == 200
    row = client.get("/api/v1/contacts", headers=phone_owner).json()[0]
    assert row["extra_emails"] == values["extra_emails"] and row["extra_phones"] == values["extra_phones"]


def test_initial_merge_preserves_existing_extra_values_on_later_sync(client, phone_owner):
    client.post("/api/v1/contacts", headers=phone_owner, json={"email": "anna@example.org", "extra_emails": ["manual@example.org"]})
    assert request(client, phone_owner, contacts=[entry(extra_emails=["phone@example.org"])]).status_code == 200
    row = client.get("/api/v1/contacts", headers=phone_owner).json()[0]
    assert set(row["extra_emails"]) == {"manual@example.org", "phone@example.org"}
    r = request(client, phone_owner, contacts=[entry(extra_emails=["new@example.org"])], linked_only=True)
    assert r.json()["items"][0]["status"] == "conflict"
    assert client.get("/api/v1/contacts", headers=phone_owner).json()[0]["extra_emails"] == row["extra_emails"]


def test_stale_preview_rolls_back_whole_batch(client, phone_owner):
    ct = client.post("/api/v1/contacts", headers=phone_owner, json={"email": "anna@example.org"}).json()
    data = request(client, phone_owner, preview=True).json()["items"][0]
    client.patch(f"/api/v1/contacts/{ct['id']}", headers=phone_owner, json={"first_name": "Changed"})
    r = request(client, phone_owner, contacts=[entry(source_key="new", email="new@example.org", mobile="", first_name="New"),
        entry(expected_digest=data["expected_digest"])])
    assert r.status_code == 409, r.text
    assert len(client.get("/api/v1/contacts", headers=phone_owner).json()) == 1


def test_manual_editor_rejects_stale_version(client, phone_owner):
    row = client.post("/api/v1/contacts", headers=phone_owner, json={"first_name": "Old"}).json()
    client.patch(f"/api/v1/contacts/{row['id']}", headers=phone_owner, json={"first_name": "New"})
    assert client.patch(f"/api/v1/contacts/{row['id']}", headers=phone_owner,
                        json={"first_name": "Stale", "expected_updated_at": row["updated_at"]}).status_code == 409


def test_private_matching_does_not_use_other_users_contacts(client, phone_owner, admin):
    client.post("/api/v1/contacts", headers=admin, json={"email": "anna@example.org", "mobile": "+49123456789"})
    assert request(client, phone_owner, preview=True).json()["items"][0]["status"] == "new"


def test_duplicate_selection_and_oversized_batch_rejected(client, phone_owner):
    assert request(client, phone_owner, contacts=[entry(), entry()]).status_code == 422
    assert request(client, phone_owner, contacts=[entry(source_key=str(i)) for i in range(201)]).status_code == 422
    assert request(client, {}).status_code == 401


def test_fill_missing_number_does_not_repeat_it_as_extra(client, phone_owner):
    client.post("/api/v1/contacts", headers=phone_owner, json={"email": "anna@example.org"})
    request(client, phone_owner)
    row = client.get("/api/v1/contacts", headers=phone_owner).json()[0]
    assert row["mobile"] == "+49 123456789" and row["extra_phones"] == []


def test_summary_cursor_reaches_all_contacts_without_notes_or_photos(client, phone_owner):
    for i in range(3):
        client.post("/api/v1/contacts", headers=phone_owner, json={"first_name": str(i), "notes": "private body", "photo": "private image"})
    first = client.get("/api/v1/contacts/summaries?limit=2", headers=phone_owner).json()
    assert len(first["items"]) == 2 and first["next_after"]
    assert all("notes" not in r and "photo" not in r for r in first["items"])
    second = client.get(f"/api/v1/contacts/summaries?after={first['next_after']}&limit=2", headers=phone_owner).json()
    assert len(second["items"]) == 1 and second["next_after"] is None
    detail = client.get(f"/api/v1/contacts/{second['items'][0]['id']}", headers=phone_owner)
    assert detail.json()["notes"] == "private body"


def test_calendar_agenda_includes_ongoing_events_and_specific_reminders(client, phone_owner):
    ids = []
    for start, end in [("2026-10-12T10:00:00Z", "2026-10-13T11:00:00Z"), ("2026-10-01T10:00:00Z", "2026-10-01T11:00:00Z")]:
        created = client.post("/api/v1/calendar/events", headers=phone_owner, json={"title": "Agenda", "start": start, "end": end})
        assert created.status_code == 201, created.text
        ids.append(created.json()["id"])
    result = client.get("/api/v1/calendar/events?end_from=2026-10-13T10:00:00%2B02:00", headers=phone_owner).json()
    assert [row["id"] for row in result] == [ids[0]]
    selected = client.get(f"/api/v1/calendar/events?event_ids={ids[1]}", headers=phone_owner).json()
    assert selected[0]["id"] == ids[1] and selected[0]["created_at"]


def test_secondary_email_also_matches_existing_contact(client, phone_owner):
    row = client.post("/api/v1/contacts", headers=phone_owner,
                      json={"email": "other@example.org", "extra_emails": ["anna@example.org"]}).json()
    result = request(client, phone_owner).json()["items"][0]
    assert result["status"] == "match" and result["contact_id"] == row["id"]


def test_same_identifier_in_input_is_imported_once(client, phone_owner):
    result = request(client, phone_owner, contacts=[entry(), entry(source_key="lookup-2")])
    assert result.json()["counts"] == {"new": 1, "duplicate_input": 1}
    assert len(client.get("/api/v1/contacts", headers=phone_owner).json()) == 1


def test_existing_different_name_is_not_replaced_by_future_phone_update(client, phone_owner):
    client.post("/api/v1/contacts", headers=phone_owner, json={"email": "anna@example.org", "first_name": "Server"})
    request(client, phone_owner)
    result = request(client, phone_owner, contacts=[entry(first_name="Handy geändert")]).json()
    assert result["items"][0]["status"] == "conflict"
    assert client.get("/api/v1/contacts", headers=phone_owner).json()[0]["first_name"] == "Server"


def test_phone_links_and_extra_columns_upgrade_existing_database(tmp_path, monkeypatch):
    from sqlalchemy import text
    from sqlmodel import SQLModel, Session, create_engine
    from app.core import db
    from app.models import Contact, User
    engine = create_engine("sqlite:///" + str(tmp_path / "legacy.db"))
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id=1, username="legacy", password_hash="unused"))
        session.commit()
        session.add(Contact(id=1, user_id=1, first_name="Preserved", email="legacy@example.org"))
        session.commit()
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE phonecontactlink"))
        conn.execute(text("ALTER TABLE contact DROP COLUMN extra_emails"))
        conn.execute(text("ALTER TABLE contact DROP COLUMN extra_phones"))
    monkeypatch.setattr(db, "engine", engine)
    db.init_db()
    db.init_db()
    with Session(engine) as session:
        ct = session.get(Contact, 1)
        assert ct.first_name == "Preserved" and ct.extra_emails == [] and ct.extra_phones == []
        assert session.exec(text("SELECT count(*) FROM phonecontactlink")).one()[0] == 0
    engine.dispose()


def test_reminder_summaries_have_same_identity_without_description(client, phone_owner):
    row = client.post("/api/v1/calendar/events", headers=phone_owner, json={"title": "Reminder", "description": "private notes",
        "start": "2026-10-13T10:00:00Z", "end": "2026-10-13T11:00:00Z"}).json()
    result = client.get(f"/api/v1/calendar/reminder-summaries?event_ids={row['id']}", headers=phone_owner)
    assert result.status_code == 200, result.text
    summary = result.json()[0]
    assert summary["created_at"] == row["created_at"] and summary["start"] == row["start"]
    assert "description" not in summary and "location" not in summary
    assert client.get("/api/v1/calendar/reminder-summaries", headers=phone_owner).status_code == 422
