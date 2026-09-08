"""Security/performance regressions: synthetic SQLite; IMAP/Google/FCM are fakes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import datetime as dt
from email.message import EmailMessage
import json
import threading
from types import SimpleNamespace as NS
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import event, inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import auth, calendar, dav, mail as mail_api, push as push_api
from app.core import totp
from app.core.config import get_settings
from app.core.db import _UNIQUE_CM_INDEX_SQL
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.mail import cache, fcm, imap, migrate, push
from app.models import BackupCode, CachedMessage, CalendarEvent, DavAccount, DavKind, DeviceToken, FolderSync, MailAccount, User
from app.schemas import DeviceTokenIn


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'synthetic.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(_UNIQUE_CM_INDEX_SQL))
    with Session(engine) as s:
        s.add(User(id=1, username="synthetic", password_hash="unused"))
        s.add(User(id=2, username="next", password_hash="unused"))
        s.add(MailAccount(id=1, user_id=1, email="owner@example.test", secret_enc=encrypt("synthetic")))
        s.commit()
    yield engine
    engine.dispose()


def message(uid, mid, body="body"):
    obj = EmailMessage()
    obj["Message-ID"] = mid
    obj["From"] = "sender@example.test"
    obj["Subject"] = body
    obj.set_content(body)
    return NS(uid=str(uid), headers={"message-id": (mid,)}, flags=(), date=None, date_str="",
              obj=obj, subject=body, from_="sender@example.test", text=body, html="", attachments=[])


class Mailbox:
    def __init__(self, messages, generation=101):
        self.messages, self.current, self.generation = messages, next(iter(messages)), generation
        self.deleted, self.appended, self.fetches = [], [], []
        self.closed = False
        self.folder = NS(list=lambda: [NS(name=n) for n in self.messages], set=self.set_folder,
                         exists=lambda n: n in self.messages, create=lambda n: self.messages.setdefault(n, []),
                         status=lambda *a: {"UIDVALIDITY": self.generation, "MESSAGES": len(self.messages[self.current]),
                                            "UNSEEN": len(self.messages[self.current])})

    def set_folder(self, name):
        self.current = name

    def fetch(self, criteria, **kw):
        self.fetches.append(kw)
        rows = list(self.messages[self.current])
        if "uid" in criteria:
            wanted = criteria["uid"].split(",")
            rows = [m for m in rows if m.uid in wanted]
        return iter(rows[:kw.get("limit", len(rows))])

    def uids(self):
        return [m.uid for m in self.messages[self.current]]

    def append(self, raw, folder, **kw):
        from email import message_from_bytes
        obj = message_from_bytes(raw)
        self.appended.append(raw)
        self.messages[folder].append(message(len(self.messages[folder]) + 1, obj["Message-ID"]))

    def delete(self, uids):
        self.deleted.extend(uids)
        self.messages[self.current] = [m for m in self.messages[self.current] if m.uid not in uids]

    def logout(self):
        self.closed = True


@pytest.fixture
def boxes(monkeypatch):
    def setup(src, dst):
        monkeypatch.setattr(migrate, "_open", lambda a, *args: src if a.id == 1 else dst)
        monkeypatch.setattr(migrate, "_delimiter", lambda b: "/")
        monkeypatch.setattr(migrate, "AND", lambda **kw: kw)
    return setup


@pytest.mark.parametrize("headers,trusted,verdict", [
    (["attacker.test; dmarc=pass"], "receiver.test", "unknown"),
    (["receiver.test; dmarc=pass"], "", "unknown"),
    (["receiver.test; spf=pass; dkim=pass; dmarc=fail"], "receiver.test", "fail"),
    (["receiver.test; spf=pass; dkim=pass"], "receiver.test", "unknown"),
    (["receiver.test; dmarc=pass"], "receiver.test", "pass"),
    (["receiver.test; dmarc=fail", "attacker.test; dmarc=pass"], "receiver.test", "fail"),
    (["receiver.test; dmarc=fail", "receiver.test; dmarc=pass"], "receiver.test", "fail"),
])
def test_auth_only_trusted_aligned_results(monkeypatch, headers, trusted, verdict):
    monkeypatch.setattr(get_settings(), "trusted_authserv_ids", trusted)
    obj = EmailMessage()
    for value in headers:
        obj["Authentication-Results"] = value
    result = imap._analyze_auth(NS(obj=obj, from_="owner@example.test"), NS(email="owner@example.test", auth_user=""))
    assert result["verdict"] == verdict
    assert result["self_spoof"] == (verdict == "fail")


def test_uidvalidity_rebuilds_cache_and_rejects_old_delete(db, monkeypatch):
    box = Mailbox({"INBOX": [message(7, "<new@test>", "NEW BODY")]})
    monkeypatch.setattr(cache, "_mailbox", lambda *a, **kw: nullcontext(box))
    monkeypatch.setattr(cache, "AND", lambda **kw: kw)
    monkeypatch.setattr(cache, "_FLAG_SEARCH_ENABLED", False)
    monkeypatch.setattr(imap, "read_modseq", lambda b: 0)
    monkeypatch.setattr(cache, "_detail_dict", lambda m, a: {"text": m.text})
    with Session(db) as s:
        s.add(FolderSync(account_id=1, folder="INBOX", uidvalidity=100, highest_modseq=900))
        s.add(CachedMessage(account_id=1, folder="INBOX", uid="7", uidvalidity=100, hidden=True,
                            message_id="<old@test>", detail_json='{"text":"OLD BODY"}'))
        s.commit()
        acc = s.get(MailAccount, 1)
        result = cache.sync_folder(s, acc, "synthetic", "INBOX", store_bodies=True)
        assert result["new"] == 1
        rows = s.exec(select(CachedMessage)).all()
        assert len(rows) == 1 and rows[0].uidvalidity == 101 and not rows[0].hidden
        assert cache.read_detail(s, 1, "INBOX", "7")["text"] == "NEW BODY"
        with pytest.raises(HTTPException) as err:
            mail_api._generation_args(s, 1, "INBOX", 100, write=True)
        assert err.value.status_code == 409
        with pytest.raises(HTTPException):
            mail_api._generation_args(s, 1, "INBOX", None, write=True)
        monkeypatch.setattr(imap, "_mailbox", lambda *a, **kw: nullcontext(box))
        with pytest.raises(HTTPException) as err:
            imap.delete_message(acc, "synthetic", "7", uidvalidity=100)
        assert err.value.status_code == 409 and not box.deleted


def test_generation_check_fails_closed_on_status_error():
    box = NS(folder=NS(status=Mock(side_effect=OSError("offline"))))
    with pytest.raises(HTTPException) as err:
        imap.mailbox_generation(box, "INBOX", 101)
    assert err.value.status_code == 409


def test_old_cache_writes_cannot_touch_reused_uid(db):
    with Session(db) as s:
        s.add(CachedMessage(account_id=1, folder="INBOX", uid="7", uidvalidity=101, detail_json='{"text":"NEW"}'))
        s.commit()
        cache.write_detail(s, 1, "INBOX", "7", {"text": "OLD", "uidvalidity": 100})
        cache.update_flags(s, 1, "INBOX", "7", flagged=True, uidvalidity=100)
        cache.hide_uids(s, 1, "INBOX", ["7"], uidvalidity=100)
        cache.remove_uids(s, 1, "INBOX", ["7"], uidvalidity=100)
        row = s.exec(select(CachedMessage)).one()
        assert not row.hidden and not row.flagged and json.loads(row.detail_json)["text"] == "NEW"


def test_transfer_collision_keeps_original(boxes):
    src = Mailbox({"INBOX": [message(7, "<collision@test>", "ORIGINAL")]})
    dst = Mailbox({"Archive": [message(9, "<collision@test>", "DIFFERENT")]})
    boxes(src, dst)
    r = migrate.transfer_messages(NS(id=1), "pw", "INBOX", ["7"], NS(id=2), "pw", "Archive", move=True)
    assert r["skipped"] == 1 and r["copied"] == r["deleted"] == 0
    assert r["errors"] and not src.deleted and not dst.appended


def test_cache_read_rechecks_generation_after_folder_state_read(db, monkeypatch):
    with Session(db) as s:
        s.add(FolderSync(account_id=1, folder="INBOX", uidvalidity=100))
        s.commit()
        monkeypatch.setattr(cache, "read_detail", lambda *a: {"uidvalidity": 101, "auth": {"verdict": "unknown"}})
        with pytest.raises(HTTPException) as error:
            mail_api.message(1, "7", folder="INBOX", uidvalidity=100, user=s.get(User, 1), session=s)
        assert error.value.status_code == 409


def test_old_session_cannot_overwrite_new_generation_body(db):
    with Session(db) as s:
        s.add(CachedMessage(account_id=1, folder="INBOX", uid="7", uidvalidity=100, detail_json='{"text":"OLD"}'))
        s.commit()
    with Session(db) as old:
        held = old.exec(select(CachedMessage)).one()
        with Session(db) as fresh:
            row = fresh.exec(select(CachedMessage)).one()
            row.uidvalidity, row.detail_json = 101, '{"text":"NEW"}'
            fresh.add(row)
            fresh.commit()
        assert held.uidvalidity == 100  # deliberately stale ORM identity map
        cache.write_detail(old, 1, "INBOX", "7", {"text": "STALE", "uidvalidity": 100})
    with Session(db) as s:
        assert json.loads(s.exec(select(CachedMessage)).one().detail_json)["text"] == "NEW"


def test_cached_auth_is_invalidated_when_trust_policy_changes(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_authserv_ids", "receiver.test")
    with Session(db) as s:
        s.add(CachedMessage(account_id=1, folder="INBOX", uid="7", uidvalidity=100,
                            detail_json=json.dumps({"auth": {"verdict": "pass", "analysis_version": 2,
                                                            "analysis_policy": "receiver.test"}})))
        s.commit()
        assert cache.read_detail(s, 1, "INBOX", "7")["auth"]["verdict"] == "pass"
        monkeypatch.setattr(get_settings(), "trusted_authserv_ids", "")
        assert cache.read_detail(s, 1, "INBOX", "7")["auth"] is None


def test_failed_append_never_deletes_source(boxes):
    src, dst = Mailbox({"INBOX": [message(7, "<unique@test>")]}), Mailbox({"Archive": []})
    boxes(src, dst)
    dst.append = Mock(side_effect=OSError("synthetic failure"))
    result = migrate.transfer_messages(NS(id=1), "pw", "INBOX", ["7"], NS(id=2), "pw", "Archive", move=True)
    assert result["errors"] and result["deleted"] == 0 and not src.deleted


def test_transfer_empty_selection_never_connects(monkeypatch):
    connect = Mock(side_effect=AssertionError("empty selection must not connect"))
    monkeypatch.setattr(migrate, "_open", connect)
    r = migrate.transfer_messages(NS(id=1), "pw", "INBOX", [], NS(id=2), "pw", "Archive", move=True)
    assert r == {"copied": 0, "skipped": 0, "deleted": 0, "errors": []}
    connect.assert_not_called()


def test_transfer_deletes_only_confirmed_copy(boxes):
    src, dst = Mailbox({"INBOX": [message(7, "<unique@test>")]}), Mailbox({"Archive": []})
    boxes(src, dst)
    r = migrate.transfer_messages(NS(id=1), "pw", "INBOX", ["7"], NS(id=2), "pw", "Archive", move=True)
    assert r["copied"] == r["deleted"] == 1 and src.deleted == ["7"]


def test_transfer_generation_change_after_append_keeps_source(boxes):
    src, dst = Mailbox({"INBOX": [message(7, "<unique@test>")]}), Mailbox({"Archive": []})
    boxes(src, dst)
    append = dst.append
    def append_then_reset(*args, **kwargs):
        append(*args, **kwargs)
        src.generation += 1
    dst.append = append_then_reset
    result = migrate.transfer_messages(NS(id=1), "pw", "INBOX", ["7"], NS(id=2), "pw", "Archive", move=True)
    assert result["copied"] == 1 and result["deleted"] == 0 and result["errors"] and not src.deleted


def test_destination_connection_failure_closes_source(monkeypatch):
    src = Mailbox({"INBOX": []})
    def connect(account, *args):
        if account.id == 1:
            return src
        raise OSError("synthetic connect failure")
    monkeypatch.setattr(migrate, "_open", connect)
    with pytest.raises(OSError):
        migrate.transfer_messages(NS(id=1), "pw", "INBOX", ["7"], NS(id=2), "pw", "Archive")
    assert src.closed


def test_migration_repeated_limited_runs_make_progress(boxes):
    src = Mailbox({"INBOX": [message(i, f"<{i}@test>") for i in range(3)]})
    dst = Mailbox({"INBOX": []})
    boxes(src, dst)
    first = migrate.migrate_folders(NS(id=1), "pw", NS(id=2), "pw", dry_run=False, limit_per_folder=2)
    second = migrate.migrate_folders(NS(id=1), "pw", NS(id=2), "pw", dry_run=False, limit_per_folder=2)
    assert first["folders"][0]["copied"] == 2 and not first["complete"]
    assert second["folders"][0]["copied"] == 1 and second["complete"]
    assert len(dst.messages["INBOX"]) == 3


@pytest.mark.parametrize("target_result", ["error", "missing-id", "success", "delete-error"])
def test_calendar_move_creates_before_deleting(db, monkeypatch, target_result):
    with Session(db) as s:
        for aid in (1, 2):
            s.add(DavAccount(id=aid, user_id=1, kind=DavKind.gcal, label="Synthetic", url="", secret_enc=""))
        s.commit()
        ev = CalendarEvent(id=7, user_id=1, dav_account_id=1, external_uid="oldcal::event7",
                           title="Synthetic", start=dt.datetime(2026, 1, 1), end=dt.datetime(2026, 1, 1))
        calls = []
        def create(*args):
            calls.append("create")
            if target_result == "error":
                raise httpx.HTTPError("synthetic failure")
            return "" if target_result == "missing-id" else "new-id"
        def remove(*args):
            calls.append("delete")
            if target_result == "delete-error":
                raise httpx.HTTPError("synthetic timeout")
        monkeypatch.setattr(calendar, "gcal_token", lambda a: "synthetic")
        monkeypatch.setattr(calendar, "_cal_meta", lambda *a: ("Synthetic", ""))
        monkeypatch.setattr(calendar.google, "create_event", create)
        monkeypatch.setattr(calendar.google, "delete_event", remove)
        if target_result == "success":
            calendar._change_calendar(ev, 2, "target", s.get(User, 1), s)
            assert ev.external_uid == "target::new-id"
        else:
            with pytest.raises(HTTPException):
                calendar._change_calendar(ev, 2, "target", s.get(User, 1), s)
            assert ev.external_uid == "oldcal::event7"
        assert calls == (["create"] if target_result in {"error", "missing-id"} else ["create", "delete"])


@pytest.mark.parametrize("backup", [False, True])
def test_second_factor_is_consumed_once_under_concurrency(db, monkeypatch, backup):
    secret, instant = totp.generate_secret(), 1788864000
    code = "A1B2C3D4" if backup else totp._hotp(totp._b32decode(secret), instant // 30)
    with Session(db) as s:
        u = s.get(User, 1)
        u.totp_secret, u.totp_enabled, u.totp_last_step = encrypt(secret), True, 0
        s.add(u)
        if backup:
            s.add(BackupCode(user_id=1, code_hash=hash_password(code)))
        s.commit()
    barrier = threading.Barrier(2)
    monkeypatch.setattr(totp.time, "time", lambda: instant)
    if backup:
        real_verify = auth.verify_password
        def verify_together(*args):
            result = real_verify(*args)
            barrier.wait(timeout=10)
            return result
        monkeypatch.setattr(auth, "verify_password", verify_together)
    def attempt():
        with Session(db) as s:
            user = s.get(User, 1)
            if not backup:
                barrier.wait(timeout=10)
            return auth._verify_second_factor(s, user, code)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        assert sorted(f.result(timeout=15) for f in futures) == [False, True]
    with Session(db) as s:
        assert not auth._verify_second_factor(s, s.get(User, 1), code)


def test_fcm_registration_moves_ownership_and_binds_session(db, monkeypatch):
    with Session(db) as s:
        a, b = s.get(User, 1), s.get(User, 2)
        push_api.register_device(DeviceTokenIn(token="synthetic-device", session_id="old"), a, s)
        push_api.register_device(DeviceTokenIn(token="synthetic-device", session_id="new"), b, s)
        push_api.unregister_device(DeviceTokenIn(token="synthetic-device", session_id="old"), b, s)
        rows = s.exec(select(DeviceToken)).all()
        assert len(rows) == 1 and rows[0].user_id == b.id and rows[0].session_id == "new"
        s.add(DeviceToken(user_id=b.id, token="legacy-device", session_id=""))
        s.commit()
        post = Mock(return_value=NS(status_code=200, text=""))
        monkeypatch.setattr(fcm, "_load_sa", lambda: {"project_id": "synthetic"})
        monkeypatch.setattr(fcm, "enabled", lambda: True)
        monkeypatch.setattr(fcm, "_access_token", lambda _: "synthetic")
        monkeypatch.setattr(fcm.httpx, "post", post)
        fcm.notify(s, a.id, "Old", "Old")
        post.assert_not_called()
        fcm.notify(s, b.id, "New", "New")
        assert post.call_count == 1
        assert post.call_args.kwargs["json"]["message"]["data"]["session_id"] == "new"


def test_fcm_mail_deep_link_requires_generation(monkeypatch):
    send = Mock()
    monkeypatch.setattr(fcm, "_send_all", send)
    fcm.notify(None, 1, "SelfMailer", "Neue E-Mail", uid="7", uidvalidity=0)
    assert "uid" not in send.call_args.args[2]
    fcm.notify(None, 1, "SelfMailer", "Neue E-Mail", uid="7", uidvalidity=101)
    assert send.call_args.args[2]["uid"] == "7"
    assert send.call_args.args[2]["uidvalidity"] == "101"


def test_preview_loads_only_one_header_and_fcm_omits_private_text(db, monkeypatch):
    with Session(db) as s:
        for uid in range(1000):
            s.add(CachedMessage(account_id=1, folder="INBOX", uid=str(uid), subject="PRIVATE SUBJECT",
                                from_addr="private@example.test", detail_json="x" * 10000))
        s.commit()
    loaded = []
    def on_load(session, instance):
        if isinstance(instance, CachedMessage):
            loaded.append(inspect(instance).unloaded)
    event.listen(Session, "loaded_as_persistent", on_load)
    try:
        with Session(db) as s:
            notify = Mock()
            monkeypatch.setattr(fcm, "notify", notify)
            monkeypatch.setattr(push, "_push_ntfy", lambda *a: None)
            push.push_new_mail(s, s.get(MailAccount, 1), "INBOX", 1)
            assert len(loaded) == 1 and "detail_json" in loaded[0]
            encoded = repr(notify.call_args)
            assert "PRIVATE SUBJECT" not in encoded and "private@example.test" not in encoded
            assert "Neue E-Mail" in encoded
    finally:
        event.remove(Session, "loaded_as_persistent", on_load)


def test_unchanged_calendar_sync_has_zero_updates(db):
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    entries = [dict(uid=str(i), title="Unchanged", description="", location="", start=now, end=now, all_day=False) for i in range(1000)]
    with Session(db) as s:
        acc = DavAccount(id=1, user_id=1, kind=DavKind.ics, label="Synthetic", url="https://example.test/calendar", secret_enc="")
        s.add(acc)
        dav._upsert_events(acc, entries, s)
        s.commit()
    updates = []
    def before_sql(conn, cursor, statement, params, context, executemany):
        if statement.upper().startswith("UPDATE CALENDAREVENT"):
            updates.append(statement)
    event.listen(db, "before_cursor_execute", before_sql)
    try:
        with Session(db) as s:
            r = dav._upsert_events(s.get(DavAccount, 1), entries, s)
            s.commit()
            assert r.updated == 0 and not updates
            entries[0]["title"] = "Changed"
            r = dav._upsert_events(s.get(DavAccount, 1), entries, s)
            s.commit()
            assert r.updated == 1 and len(updates) == 1
    finally:
        event.remove(db, "before_cursor_execute", before_sql)
