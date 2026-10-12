"""Sicherheitsregressionen mit synthetischen DB-Daten und HTTP-Testtransport."""

import asyncio
import datetime as dt
import json
import time
import uuid

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select as sql_select, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel
from starlette.requests import Request

from app import models as m
from app.api import events
from app.core import cleanup, security, sessions, totp
from app.core.crypto import encrypt
from app.core.db import engine
from app.dav import client as dav
from app.events import EventBus
from app.mail import scheduler
from app.mail import imap
from app.core import jobs


@pytest.fixture(autouse=True)
def clear_cookie(client):
    client.cookies.clear()
    yield
    client.cookies.clear()


@pytest.fixture
def actor(client, admin):
    username = f"security-{uuid.uuid4().hex}"
    password = "synthetic-password-123"
    r = client.post("/api/v1/admin/users", headers=admin, json={"username": username, "password": password})
    assert r.status_code == 201, r.text
    user_id = r.json()["id"]

    def login():
        result = client.post("/api/v1/auth/login", json={"username": username, "password": password})
        client.cookies.clear()
        return result.json()["access_token"]

    yield user_id, username, password, login
    with Session(engine) as session:
        user = session.execute(sql_select(m.User).where(m.User.username == username)).scalar_one_or_none()
        if user is not None:
            cleanup.delete_user_data(session, user)
            session.commit()


def bearer(raw):
    return {"Authorization": f"Bearer {raw}"}


def stream_request(raw):
    return Request({"type": "http", "method": "GET", "path": "/api/v1/events/stream", "headers": [(b"authorization", f"Bearer {raw}".encode())]})


def seed(user_id):
    with Session(engine) as s:
        account = m.MailAccount(user_id=user_id, email="synthetic@example.test", secret_enc=encrypt("synthetic"))
        dav_account = m.DavAccount(user_id=user_id, url="https://dav.example.test/cal/", secret_enc=encrypt("synthetic"))
        s.add(account)
        s.add(dav_account)
        s.commit()
        s.refresh(account)
        s.refresh(dav_account)
        aid, did = account.id, dav_account.id
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        s.add_all(
            [
                m.BackupCode(user_id=user_id, code_hash="synthetic"),
                m.Note(user_id=user_id, body="synthetic"),
                m.MailTemplate(user_id=user_id),
                m.MailIdentity(user_id=user_id, account_id=aid),
                m.VacationSetting(user_id=user_id, account_id=aid),
                m.VacationReply(account_id=aid, sender="sender@example.test"),
                m.ScheduledMail(user_id=user_id, account_id=aid, send_at=now, payload_json=json.dumps({"to": ["test@example.test"]})),
                m.MailLabel(user_id=user_id),
                m.CalendarEvent(user_id=user_id, dav_account_id=did, start=now, end=now),
                m.Contact(user_id=user_id, dav_account_id=did),
                m.Task(user_id=user_id),
                m.MailRule(account_id=aid),
                m.CachedMessage(account_id=aid, folder="INBOX", uid="1"),
                m.FolderSync(account_id=aid, folder="INBOX"),
                m.CachedFolder(account_id=aid, folder="INBOX"),
                m.FolderNotify(account_id=aid, folder="INBOX"),
                m.PushConfig(user_id=user_id),
                m.DeviceToken(user_id=user_id, token=uuid.uuid4().hex),
                m.FeedToken(user_id=user_id, token=uuid.uuid4().hex),
                m.RevokedToken(user_id=user_id, token_hash=uuid.uuid4().hex, expires_at=int(time.time()) + 300),
            ]
        )
        s.commit()
        return aid, did


def test_logout_revokes_only_current_token_persistently(client, actor):
    uid, _, _, login = actor
    first, second = login(), login()
    assert first != second
    assert client.post("/api/v1/auth/logout", headers=bearer(first)).status_code == 200
    assert client.get("/api/v1/auth/me", headers=bearer(first)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=bearer(second)).status_code == 200
    with Session(engine) as s:
        assert s.get(m.RevokedToken, sessions.token_hash(first)).user_id == uid
        assert sessions.token_user(first, s) is None
    assert client.post("/api/v1/auth/logout", headers=bearer(first)).status_code == 200


def test_cookie_logout_revokes_cookie_and_bearer(client, actor):
    _, username, password, _ = actor
    raw = client.post("/api/v1/auth/login", json={"username": username, "password": password}).json()["access_token"]
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me", headers=bearer(raw)).status_code == 401
    assert not client.cookies.get("sm_session")


def test_admin_password_reset_revokes_rest_sse_and_mfa(client, admin, actor):
    uid, username, _, login = actor
    raw = login()
    with Session(engine) as s:
        challenge = security.create_mfa_token(username, s.get(m.User, uid).token_version)
    assert events._stream_user(stream_request(raw)).id == uid
    r = client.patch(f"/api/v1/admin/users/{uid}/password", headers=admin, json={"new_password": "different-password-123"})
    assert r.status_code == 200
    assert client.get("/api/v1/auth/me", headers=bearer(raw)).status_code == 401
    with pytest.raises(HTTPException):
        events._stream_user(stream_request(raw))
    with Session(engine) as s:
        assert sessions.token_user(challenge, s, mfa=True) is None


def test_disable_then_enable_does_not_restore_old_token(client, admin, actor):
    uid, _, _, login = actor
    raw = login()
    assert client.patch(f"/api/v1/admin/users/{uid}/active?active=false", headers=admin).status_code == 200
    assert client.patch(f"/api/v1/admin/users/{uid}/active?active=true", headers=admin).status_code == 200
    assert client.get("/api/v1/auth/me", headers=bearer(raw)).status_code == 401


def test_open_sse_stops_before_delivering_data_after_logout(actor, monkeypatch):
    uid, _, _, login = actor
    raw = login()
    request = stream_request(raw)
    async def connected():
        return False
    request.is_disconnected = connected
    bus = EventBus()
    monkeypatch.setattr(events, "bus", bus)
    async def exercise():
        response = await events.stream(request, events._stream_user(request))
        iterator = response.body_iterator
        assert await anext(iterator) == ": ok\n\n"
        with Session(engine) as session:
            sessions.revoke_session(raw, session.get(m.User, uid), session)
            session.commit()
        next(iter(bus._subs[uid])).put_nowait({"type": "mail", "secret": "must-not-deliver"})
        with pytest.raises(StopAsyncIteration):
            await anext(iterator)
        assert uid not in bus._subs
    asyncio.run(exercise())


def test_deleted_username_cannot_inherit_old_token(client, admin, actor):
    uid, username, password, login = actor
    raw = login()
    assert client.delete(f"/api/v1/admin/users/{uid}", headers=admin).status_code == 204
    created = client.post("/api/v1/admin/users", headers=admin, json={"username": username, "password": password})
    assert created.status_code == 201
    assert created.json()["id"] > uid
    assert client.get("/api/v1/auth/me", headers=bearer(raw)).status_code == 401


def test_mfa_is_version_bound_and_one_use(client, actor):
    uid, username, password, login = actor
    raw = login()
    secret = client.post("/api/v1/auth/totp/setup", headers=bearer(raw)).json()["secret"]
    step = int(time.time() // 30)
    code = totp._hotp(totp._b32decode(secret), step)
    enabled = client.post("/api/v1/auth/totp/enable", headers=bearer(raw), json={"code": code})
    assert enabled.status_code == 200
    fresh = enabled.json()["access_token"]
    client.cookies.clear()
    assert client.get("/api/v1/auth/me", headers=bearer(raw)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=bearer(fresh)).status_code == 200
    challenge = client.post("/api/v1/auth/login", json={"username": username, "password": password}).json()["mfa_token"]
    assert client.get("/api/v1/auth/me", headers=bearer(challenge)).status_code == 401
    backup = enabled.json()["backup_codes"]
    assert client.post("/api/v1/auth/login/totp", json={"mfa_token": challenge, "code": backup[0]}).status_code == 200
    assert client.post("/api/v1/auth/login/totp", json={"mfa_token": challenge, "code": backup[1]}).status_code == 401
    with Session(engine) as s:
        before = s.get(m.User, uid).token_version
        sessions.revoke_all(s.get(m.User, uid), s)
        s.commit()
        assert s.get(m.User, uid).token_version == before + 1


@pytest.mark.parametrize("admin_delete", [False, True])
def test_mail_account_cleanup_includes_all_children(client, admin, actor, admin_delete):
    uid, _, _, login = actor
    raw = login()
    aid, did = seed(uid)
    endpoint = f"/api/v1/admin/users/{uid}/accounts/{aid}" if admin_delete else f"/api/v1/accounts/{aid}"
    assert client.delete(endpoint, headers=admin if admin_delete else bearer(raw)).status_code == 204
    with Session(engine) as s:
        assert s.get(m.MailAccount, aid) is None
        for table in SQLModel.metadata.tables.values():
            if "account_id" in table.c:
                assert not s.execute(sql_select(table).where(table.c.account_id == aid)).all(), table.name
        assert s.get(m.DavAccount, did) is not None
        assert s.get(m.User, uid) is not None
        assert s.exec(sql_select(m.Note).where(m.Note.user_id == uid)).first() is not None


def test_user_cleanup_preserves_other_users_and_enforces_fk(client, admin, actor):
    uid, _, _, login = actor
    raw = login()
    aid, did = seed(uid)
    job_id = jobs.create_job(uid, "synthetic")
    admin_uid = client.get("/api/v1/auth/me", headers=admin).json()["id"]
    assert client.delete(f"/api/v1/admin/users/{uid}", headers=admin).status_code == 204
    assert client.get("/api/v1/auth/me", headers=bearer(raw)).status_code == 401
    assert jobs.get_job(job_id, uid) is None
    with Session(engine) as s:
        assert s.get(m.User, admin_uid) is not None
        for table in SQLModel.metadata.tables.values():
            for col, key in (("user_id", uid), ("account_id", aid), ("dav_account_id", did)):
                if col in table.c:
                    assert not s.execute(sql_select(table).where(table.c[col] == key)).all(), table.name
        assert s.exec(text("PRAGMA foreign_keys")).one()[0] == 1
        s.add(m.CachedMessage(account_id=aid, folder="INBOX", uid="late-sync"))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


def test_cleanup_rollback_is_atomic(actor):
    uid, _, _, _ = actor
    aid, _ = seed(uid)
    with Session(engine) as s:
        cleanup.delete_user_data(s, s.get(m.User, uid))
        s.rollback()
        assert s.get(m.User, uid) is not None
        assert s.get(m.MailAccount, aid) is not None
        assert s.execute(sql_select(m.ScheduledMail).where(m.ScheduledMail.account_id == aid)).first() is not None


def test_pool_diagnostics_require_admin(client, admin, actor):
    _, _, _, login = actor
    assert client.get("/api/v1/mail/pool-status", headers=bearer(login())).status_code == 403
    assert client.get("/api/v1/mail/pool-status", headers=admin).status_code == 200


@pytest.fixture
def dav_transport(monkeypatch):
    real = httpx.Client
    calls = []

    def install(handler):
        def record(request):
            calls.append(request)
            return handler(request)

        monkeypatch.setattr(dav.httpx, "Client", lambda *a, **k: real(*a, **k, transport=httpx.MockTransport(record)))
        monkeypatch.setattr(dav, "_resolve_validated", lambda url, **k: ("203.0.113.10", httpx.URL(url).host, httpx.URL(url).port or 443))
        return calls

    return install


@pytest.mark.parametrize("token", [None, "synthetic-bearer"])
@pytest.mark.parametrize(
    "href",
    ["https://other.example.test/a.ics", "http://dav.example.test/a.ics", "https://dav.example.test:444/a.ics", "//other.example.test/a.ics"],
)
def test_dav_rejects_cross_origin_hrefs_without_credentials_leak(dav_transport, token, href):
    calls = dav_transport(
        lambda r: httpx.Response(
            207,
            text=(f'<d:multistatus xmlns:d="DAV:"><d:response><d:href>{href}</d:href>'
                  '<d:propstat><d:prop><d:getcontenttype>text/calendar</d:getcontenttype></d:prop>'
                  '</d:propstat></d:response></d:multistatus>'),
        )
    )
    with pytest.raises(dav.DavUrlError):
        dav.fetch_collection("https://dav.example.test/cal/", "synthetic", "synthetic", token=token)
    assert len(calls) == 1
    assert calls[0].headers["host"] == "dav.example.test"


@pytest.mark.parametrize("kind", ["ics", "discovery"])
def test_authenticated_redirect_cannot_leave_origin(dav_transport, kind):
    calls = dav_transport(lambda r: httpx.Response(302, headers={"Location": "https://other.example.test/cal/"}))
    with pytest.raises(dav.DavUrlError):
        if kind == "ics":
            dav.fetch_ics("https://dav.example.test/feed.ics", "synthetic", "synthetic")
        else:
            dav.discover_collections("https://dav.example.test/", "synthetic", "synthetic", want_contacts=False)
    assert len(calls) == 1


def test_dav_same_origin_relative_hrefs_keep_auth_and_pinning(dav_transport):
    def handler(request):
        if request.method == "PROPFIND":
            return httpx.Response(
                207,
                text=('<d:multistatus xmlns:d="DAV:"><d:response><d:href>a.ics</d:href>'
                      '<d:propstat><d:prop><d:getcontenttype>text/calendar</d:getcontenttype></d:prop>'
                      '</d:propstat></d:response></d:multistatus>'),
            )
        return httpx.Response(200, text="BEGIN:VCALENDAR\nEND:VCALENDAR")

    calls = dav_transport(handler)
    assert len(dav.fetch_collection("https://dav.example.test/cal/", "synthetic", "synthetic")) == 1
    assert len(calls) == 2
    assert all(r.headers["host"] == "dav.example.test" and r.url.host == "203.0.113.10" for r in calls)
    assert calls[0].headers["authorization"] == calls[1].headers["authorization"]


def test_anonymous_ics_redirect_can_change_origin(dav_transport):
    def handler(r):
        return (
            httpx.Response(302, headers={"Location": "https://other.example.test/feed.ics"})
            if r.headers["host"] == "dav.example.test"
            else httpx.Response(200, text="feed")
        )

    calls = dav_transport(handler)
    assert dav.fetch_ics("https://dav.example.test/feed.ics") == "feed"
    assert len(calls) == 2 and all("authorization" not in r.headers for r in calls)


def test_event_bus_drops_full_queue_in_callback():
    async def exercise():
        bus = EventBus()
        bus.set_loop(asyncio.get_running_loop())
        q = bus.subscribe(1)
        for _ in range(100):
            q.put_nowait({})
        errors = []
        loop = asyncio.get_running_loop()
        old = loop.get_exception_handler()
        loop.set_exception_handler(lambda _, info: errors.append(info))
        try:
            bus.publish(1, {"type": "mail"})
            await asyncio.sleep(0)
            assert q.qsize() == 100 and not errors
        finally:
            loop.set_exception_handler(old)

    asyncio.run(exercise())


def test_scheduled_claim_is_durable_and_not_retried(actor, monkeypatch):
    uid, _, _, _ = actor
    aid, _ = seed(uid)
    calls = []

    async def fake_send(*args, **kwargs):
        with Session(engine) as s:
            assert s.execute(sql_select(m.ScheduledMail.status).where(m.ScheduledMail.account_id == aid)).scalar_one() == "sending"
        calls.append(1)
        raise TimeoutError("synthetic ambiguous SMTP result")

    monkeypatch.setattr(scheduler.smtp_mod, "send_message", fake_send)
    scheduler._send_due_scheduled()
    scheduler._send_due_scheduled()
    assert calls == [1]
    with Session(engine) as s:
        assert s.execute(sql_select(m.ScheduledMail.status).where(m.ScheduledMail.account_id == aid)).scalar_one() == "uncertain"


def test_restart_recovers_sending_as_uncertain(actor):
    uid, _, _, _ = actor
    aid, _ = seed(uid)
    with Session(engine) as s:
        row = s.execute(sql_select(m.ScheduledMail).where(m.ScheduledMail.account_id == aid)).scalar_one()
        row.status = "sending"
        s.add(row)
        s.commit()
    scheduler.recover_interrupted_sends()
    with Session(engine) as s:
        assert s.execute(sql_select(m.ScheduledMail.status).where(m.ScheduledMail.account_id == aid)).scalar_one() == "uncertain"


def test_scheduled_running_cannot_be_cancelled(client, actor):
    uid, _, _, login = actor
    raw = login()
    aid, _ = seed(uid)
    with Session(engine) as s:
        row = s.execute(sql_select(m.ScheduledMail).where(m.ScheduledMail.account_id == aid)).scalar_one()
        row.status = "sending"
        s.add(row)
        s.commit()
        row_id = row.id
    assert client.delete(f"/api/v1/schedule/{row_id}", headers=bearer(raw)).status_code == 409


def test_unsupported_pop3_cannot_be_created(client, actor):
    _, _, _, login = actor
    response = client.post(
        "/api/v1/accounts", headers=bearer(login()), json={"email": "synthetic@example.com", "protocol": "pop3", "password": "synthetic"}
    )
    assert response.status_code == 422
    assert any("protocol" in item["loc"] for item in response.json()["detail"])


def test_expired_revocations_are_pruned_without_removing_live_ones(actor):
    uid, _, _, _ = actor
    with Session(engine) as session:
        session.add(m.RevokedToken(user_id=uid, token_hash="expired-synthetic", expires_at=int(time.time()) - 1))
        session.add(m.RevokedToken(user_id=uid, token_hash="live-synthetic", expires_at=int(time.time()) + 300))
        session.commit()
        sessions.prune_expired(session)
        session.commit()
        assert session.get(m.RevokedToken, "expired-synthetic") is None
        assert session.get(m.RevokedToken, "live-synthetic") is not None


def test_pool_reauthenticates_after_credential_change(monkeypatch):
    from types import SimpleNamespace
    account = m.MailAccount(id=1, user_id=1, email="synthetic@example.test", secret_enc="new-encrypted-secret")
    old_box, new_box = SimpleNamespace(), SimpleNamespace()
    conn = imap._Conn()
    conn.box = old_box
    conn.last_used = time.monotonic()
    conn.folder = "INBOX"
    conn.binding = ("old-encrypted-secret", True)
    closed = []
    monkeypatch.setattr(imap, "_close", closed.append)
    monkeypatch.setattr(imap, "_connect", lambda *a: new_box)
    assert imap._ensure_box(conn, account, account.email, "synthetic", "INBOX") is new_box
    assert closed == [old_box]
    assert conn.binding == (account.secret_enc, True)


def test_account_ids_are_not_reused_after_delete(client, actor):
    _, _, _, login = actor
    headers = bearer(login())
    data = {"email": "sequence@example.com", "password": "synthetic"}
    created = client.post("/api/v1/accounts", headers=headers, json=data)
    assert created.status_code == 201, created.text
    first = created.json()["id"]
    assert client.delete(f"/api/v1/accounts/{first}", headers=headers).status_code == 204
    second = client.post("/api/v1/accounts", headers=headers, json=data).json()["id"]
    assert second > first


def test_sequence_bootstrap_preserves_existing_high_water(tmp_path):
    from sqlmodel import create_engine
    from app.core.ids import allocate_id, initialize_sequences
    test_engine = create_engine(f"sqlite:///{tmp_path / 'existing.db'}")
    try:
        SQLModel.metadata.create_all(test_engine)
        with Session(test_engine) as session:
            user = m.User(id=9000, username="existing", password_hash="synthetic")
            session.add(user)
            session.commit()
            initialize_sequences(session)
            session.commit()
            session.delete(user)
            session.commit()
            assert allocate_id(session, m.User) == 9001
            session.commit()
            assert allocate_id(session, m.User) == 9002
    finally:
        test_engine.dispose()


def test_sequence_bootstrap_reserves_legacy_orphan_owner_ids(tmp_path):
    from sqlmodel import create_engine
    from app.core.ids import allocate_id, initialize_sequences
    test_engine = create_engine(f"sqlite:///{tmp_path / 'orphans.db'}")
    try:
        SQLModel.metadata.create_all(test_engine)
        with Session(test_engine) as session:
            # Alte DB ohne FK-Enforcement: zugehöriger User ist bereits weg.
            session.add(m.MailAccount(id=12, user_id=5000, email="orphan@example.test", secret_enc="synthetic"))
            session.commit()
            initialize_sequences(session)
            session.commit()
            assert allocate_id(session, m.User) == 5001
    finally:
        test_engine.dispose()
