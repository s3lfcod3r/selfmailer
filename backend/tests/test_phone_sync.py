from app.api.phone_sync import FIELDS
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


def create(client, header, **extra):
    response = client.post('/api/v1/contacts', headers=header, json={'first_name': 'Anna', 'email': 'anna@example.org', **extra})
    assert response.status_code == 201, response.text
    return response.json()


def read(client, header, cid):
    return client.get(f'/api/v1/contacts/phone/sync/{cid}', headers=header)


def write(client, header, snapshot, **changes):
    return client.post(f"/api/v1/contacts/phone/sync/{snapshot['id']}", headers=header, json={
        'expected_created_at': snapshot['created_at'], 'expected_updated_at': snapshot['updated_at'],
        'values': {**snapshot['values'], **changes},
    })


def test_snapshot_is_light_and_owned(client, phone_owner):
    c = create(client, phone_owner, photo='private-photo', notes='notes', birthday='1990-01-02')
    snapshot = read(client, phone_owner, c['id']).json()
    assert set(snapshot['values']) == set(FIELDS)
    assert 'photo' not in snapshot['values'] and snapshot['values']['notes'] == 'notes'
    assert snapshot['values']['birthday'] == '1990-01-02' and snapshot['created_at']


def test_sync_preserves_photo_and_updates_all_supported_fields(client, phone_owner):
    c = create(client, phone_owner, photo='private-photo')
    snapshot = read(client, phone_owner, c['id']).json()
    result = write(client, phone_owner, snapshot, title='Engineer', street='Street', postal_code='12345', city='Town',
                   country='DE', website='https://example.org', notes='New notes', birthday='1990-01-02',
                   extra_emails=['extra@example.org'], extra_phones=['+49123456789'])
    assert result.status_code == 200, result.text
    current = client.get(f"/api/v1/contacts/{c['id']}", headers=phone_owner).json()
    assert current['photo'] == 'private-photo' and current['birthday'] == '1990-01-02' and current['street'] == 'Street'


def test_stale_server_edits_are_not_overwritten(client, phone_owner):
    c = create(client, phone_owner)
    old = read(client, phone_owner, c['id']).json()
    assert client.patch(f"/api/v1/contacts/{c['id']}", headers=phone_owner, json={'notes': 'manual'}).status_code == 200
    assert write(client, phone_owner, old, notes='phone').status_code == 409
    assert read(client, phone_owner, c['id']).json()['values']['notes'] == 'manual'


def test_wrong_creation_identity_is_blocked(client, phone_owner):
    c = create(client, phone_owner)
    snapshot = read(client, phone_owner, c['id']).json()
    snapshot['created_at'] = '2000-01-01T00:00:00'
    assert write(client, phone_owner, snapshot, notes='replacement').status_code == 409


def test_deleted_server_contact_is_not_recreated(client, phone_owner):
    c = create(client, phone_owner)
    snapshot = read(client, phone_owner, c['id']).json()
    assert client.delete(f"/api/v1/contacts/{c['id']}", headers=phone_owner).status_code == 204
    assert write(client, phone_owner, snapshot, notes='new').status_code == 404
    assert read(client, phone_owner, c['id']).status_code == 404


def test_duplicate_email_or_number_blocks_sync(client, phone_owner):
    c = create(client, phone_owner)
    create(client, phone_owner, first_name='Other', email='other@example.org', mobile='0049 123456789')
    snapshot = read(client, phone_owner, c['id']).json()
    assert write(client, phone_owner, snapshot, email='OTHER@example.org').status_code == 409
    assert write(client, phone_owner, snapshot, extra_phones=['+49 (123)456789']).status_code == 409
    assert read(client, phone_owner, c['id']).json()['values']['email'] == 'anna@example.org'


def test_same_name_collision_is_reported(client, phone_owner):
    c = create(client, phone_owner)
    create(client, phone_owner, first_name='Ben', email='ben@example.org')
    snapshot = read(client, phone_owner, c['id']).json()
    assert write(client, phone_owner, snapshot, first_name='Ben').status_code == 409


def test_current_duplicate_is_visible_in_snapshot(client, phone_owner):
    c = create(client, phone_owner)
    create(client, phone_owner, first_name='Other', email='ANNA@example.org')
    assert read(client, phone_owner, c['id']).json()['duplicate'] is True


def test_other_users_cannot_read_or_write(client, phone_owner, admin):
    # Admin is a separate owner here, not an implicit cross-owner sync identity.
    c = create(client, phone_owner)
    snapshot = read(client, phone_owner, c['id']).json()
    assert read(client, admin, c['id']).status_code == 404
    assert write(client, admin, snapshot, notes='wrong owner').status_code == 404


def test_missing_login_and_unknown_payload_fields_are_rejected(client, phone_owner):
    c = create(client, phone_owner)
    snapshot = read(client, phone_owner, c['id']).json()
    client.cookies.clear()
    assert client.get(f"/api/v1/contacts/phone/sync/{c['id']}").status_code == 401
    assert write(client, phone_owner, snapshot, photo='unmanaged').status_code == 422
    assert write(client, phone_owner, snapshot, user_id=99).status_code == 422


def test_field_clears_and_repeat_are_safe(client, phone_owner):
    c = create(client, phone_owner, birthday='1990-01-01', notes='old')
    snapshot = read(client, phone_owner, c['id']).json()
    first = write(client, phone_owner, snapshot, birthday='', notes='')
    assert first.status_code == 200, first.text
    second = write(client, phone_owner, first.json())
    assert second.status_code == 200 and second.json()['updated_at'] == first.json()['updated_at']
    assert second.json()['values']['birthday'] == '' and second.json()['values']['notes'] == ''


def test_payload_limits_are_enforced(client, phone_owner):
    c = create(client, phone_owner)
    snapshot = read(client, phone_owner, c['id']).json()
    assert write(client, phone_owner, snapshot, notes='x' * 80001).status_code == 422
    assert write(client, phone_owner, snapshot, extra_emails=['a@b'] * 201).status_code == 422
    assert write(client, phone_owner, snapshot, extra_phones=['x' * 255]).status_code == 422


def test_read_only_dav_contact_is_not_changed(client, phone_owner):
    from sqlmodel import Session
    from app.core.db import engine
    from app.models import Contact, DavAccount, DavKind
    c = create(client, phone_owner)
    uid = client.get('/api/v1/auth/me', headers=phone_owner).json()['id']
    with Session(engine) as session:
        account = DavAccount(user_id=uid, kind=DavKind.carddav, url='https://example.invalid/dav', secret_enc='')
        session.add(account)
        session.flush()
        contact = session.get(Contact, c['id'])
        contact.dav_account_id = account.id
        session.add(contact)
        session.commit()
    snapshot = read(client, phone_owner, c['id']).json()
    assert snapshot['read_only'] is True
    assert write(client, phone_owner, snapshot, notes='phone').status_code == 409
    assert read(client, phone_owner, c['id']).json()['values']['notes'] == ''


def test_capability_gate_is_authenticated_and_unambiguous(client, phone_owner):
    response = client.get('/api/v1/contacts/phone/sync/capabilities', headers=phone_owner)
    assert response.status_code == 200 and response.json()['protocol'] == 1
    assert response.json()['contact_deletions'] is False
    client.cookies.clear()
    assert client.get('/api/v1/contacts/phone/sync/capabilities').status_code == 401
