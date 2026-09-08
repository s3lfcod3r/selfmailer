"""Real pool/parser/fetch code with synthetic clocks, sockets and mail data."""
from contextlib import ExitStack, nullcontext
from types import SimpleNamespace as NS
import time

from imap_tools import MailBox
import pytest

from app.mail import cache, imap, metadata
from app.models import MailAccount


def test_ui_folder_lock_obeys_timeout(monkeypatch):
    acc = MailAccount(id=98761, email="test@example.invalid")
    lock = cache._folder_lock(acc.id, "INBOX")
    monkeypatch.setattr(cache, "_sync_folder_unlocked", lambda *a, **kw: pytest.fail("must not enter a busy folder"))
    lock.acquire()
    try:
        started = time.monotonic()
        with pytest.raises(imap.ImapBusyError):
            cache.sync_folder(None, acc, "synthetic", "INBOX", lock_timeout=0.01, op="sync-ui")
        assert time.monotonic() - started < 0.5
    finally:
        lock.release()


def test_folder_wait_is_subtracted_from_connection_budget(monkeypatch):
    class Lock:
        def acquire(self, timeout):
            assert timeout == 3
            return True
        def release(self):
            pass
    ticks = iter([100.0, 101.0])
    monkeypatch.setattr(cache.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(cache, "_folder_lock", lambda *a: Lock())
    def core(*a, **kw):
        assert kw["lock_timeout"] == 2
        return {"ms": {"gesamt": 20, "warten": 10}}
    monkeypatch.setattr(cache, "_sync_folder_unlocked", core)
    result = cache.sync_folder(None, NS(id=1), "synthetic", "INBOX", lock_timeout=3)
    assert result["ms"] == {"gesamt": 1020, "warten": 1010, "ordnersperre": 1000}


def test_existing_extra_slots_remain_reserved(monkeypatch):
    monkeypatch.setattr(imap, "_POOL", {})
    key = "98761:test@example.invalid"
    all_slots = [imap._greife_zu(key, "INBOX", 5) for _ in range(5)]
    for slot in all_slots:
        slot.lock.release()
    background = [imap._greife_zu(key, "INBOX", 3) for _ in range(3)]
    assert all(background)
    assert imap._greife_zu(key, "INBOX", 3) is None
    foreground = imap._greife_zu(key, "INBOX", 5)
    assert foreground and foreground not in background
    for slot in [*background, foreground]:
        slot.lock.release()


def test_delete_can_start_while_background_pool_is_full(monkeypatch):
    acc = MailAccount(id=98761, email="test@example.invalid")
    monkeypatch.setattr(imap, "_POOL", {})
    monkeypatch.setattr(imap, "_POOL_SIZE", 3)
    monkeypatch.setattr(imap, "_POOL_EXTRA", 2)
    monkeypatch.setattr(imap, "_POOL_ENABLED", True)
    monkeypatch.setattr(imap, "_ensure_box", lambda *a: object())
    monkeypatch.setattr(imap, "_reap_idle", lambda: None)
    with ExitStack() as stack:
        for _ in range(3):
            stack.enter_context(imap._mailbox(acc, "synthetic", op="background"))
        with imap._mailbox(acc, "synthetic", read_fallback=True, lock_timeout=0, op="delete_message"):
            pass


class Wire:
    """A server that would supply a 20 MiB attachment on an erroneous full fetch."""
    def __init__(self):
        self.calls = []
        self.sizes = {"7": 20 * 1024 * 1024, "8": 512}
        self.payload_bytes = 0
    def uid(self, command, uids, parts):
        self.calls.append((command, uids, parts))
        assert command.upper() == "FETCH", "Known UID must not trigger another SEARCH"
        out = []
        for uid in uids.split(","):
            if parts == "(UID BODYSTRUCTURE)":
                disposition = b'("ATTACHMENT" ("FILENAME" "large.pdf"))' if uid == "7" else b'NIL'
                out.append(b'1 (UID ' + uid.encode() +
                           b' BODYSTRUCTURE ("APPLICATION" "PDF" NIL NIL NIL "BASE64" 20971520 NIL ' + disposition + b'))')
            else:
                if "BODY.PEEK[HEADER]" not in parts:
                    assert uid == "8", "Large attachment downloaded during prefetch/list"
                body = b"From: sender@example.invalid\r\nSubject: Test\r\n\r\n"
                self.payload_bytes += len(body)
                structure = (' BODYSTRUCTURE ("APPLICATION" "PDF" ("NAME" "large.pdf") NIL NIL "BASE64" 20971520)'
                             if uid == "7" else ' BODYSTRUCTURE ("TEXT" "PLAIN" NIL NIL NIL "7BIT" 512 5)')
                prefix = f'1 (UID {uid} FLAGS () RFC822.SIZE {self.sizes[uid]}{structure} BODY[HEADER] {{{len(body)}}}'.encode()
                out.extend([(prefix, body), b')'])
        return "OK", out


def wire_box():
    box = object.__new__(MailBox)
    box.client = Wire()
    box.folder = NS(status=lambda *a: {"UIDVALIDITY": 101})
    return box


def test_headers_do_not_download_attachments_and_keep_paperclip():
    box = wire_box()
    rows = list(imap.fetch_headers(box, ["7", "8"]))
    assert [m.uid for m in rows] == ["7", "8"]
    assert [imap.has_attachments(m) for m in rows] == [True, False]
    assert box.client.payload_bytes < 1024
    assert len(box.client.calls) == 1
    assert all(not m.text and not m.html for m in rows)


def test_prefetch_skips_large_mail_and_uses_no_ui_reserve(monkeypatch):
    box = wire_box()
    def mailbox(*a, **kw):
        assert not kw.get("read_fallback", False)
        assert kw["lock_timeout"] == 0
        return nullcontext(box)
    monkeypatch.setattr(imap, "_mailbox", mailbox)
    rows = imap.get_messages(NS(email="test@example.invalid", auth_user=""), "synthetic", ["7", "8"], uidvalidity=101)
    assert [row["uid"] for row in rows] == ["8"]
    assert len(box.client.calls) == 2


def test_header_fetch_budget_stops_between_chunks(monkeypatch):
    box = wire_box()
    box.client.sizes = {str(i): 512 for i in range(1, 101)}
    times = iter([0.0, 3.0])
    monkeypatch.setattr(imap.time, "monotonic", lambda: next(times))
    assert len(list(imap.fetch_headers(box, list(box.client.sizes), deadline_s=2))) == 50


def test_bodystructure_literals_and_quoted_uid_cannot_change_identity():
    raw = [(b'1 (BODYSTRUCTURE ("APPLICATION" "PDF" ("NAME" {14}', b'UID 999 ".pdf"'), b') NIL NIL "BASE64" 100) UID 7)']
    # Correct literal size is byte-counted, even with a misleading UID in the filename.
    raw[0] = (b'1 (BODYSTRUCTURE ("APPLICATION" "PDF" ("NAME" {' + str(len(raw[0][1])).encode() + b'}', raw[0][1])
    box = NS(client=NS(uid=lambda *a: ("OK", raw)))
    assert metadata.attachment_uids(box, ["7", "999"]) == {"7"}


@pytest.mark.parametrize("raw", [b"(", b'("unterminated)', b"({5}\r\nabc)", b")", b"(" * 42])
def test_bad_metadata_is_rejected(raw):
    with pytest.raises(ValueError):
        metadata.parse_imap(raw)


def test_busy_cold_list_does_not_trigger_second_live_fetch(client, admin, account, monkeypatch):
    from app.api import mail
    monkeypatch.setattr(cache, "has_cache", lambda *a: False)
    def busy(*a, **kw):
        raise imap.ImapBusyError()
    monkeypatch.setattr(cache, "sync_folder", busy)
    monkeypatch.setattr(imap, "list_messages", lambda *a, **kw: pytest.fail("busy is not a cache error"))
    response = client.get(f"/api/v1/mail/{account}/messages?folder=Cold", headers=admin)
    assert response.status_code == 200 and response.json() == []
    assert mail._UI_LOCK_TIMEOUT <= 3


def test_prefetch_deduplicates_same_folder(client, admin, account, monkeypatch):
    from app.api import mail
    monkeypatch.setattr(mail, "_PREFETCH_ACTIVE", {(account, "INBOX")})
    response = client.post(f"/api/v1/mail/{account}/messages/prefetch", headers=admin,
                           json={"folder": "INBOX", "uids": ["7"]})
    assert response.status_code == 200 and response.json()["busy"]


def test_default_sync_is_headers_only(monkeypatch):
    from app.core.db import engine
    from sqlmodel import Session, select
    from app.models import CachedMessage
    box = wire_box()
    box.folder = NS(status=lambda *a: {"UIDVALIDITY": 101, "MESSAGES": 2, "UNSEEN": 2})
    box.uids = lambda: ["7", "8"]
    monkeypatch.setattr(cache, "_mailbox", lambda *a, **kw: nullcontext(box))
    monkeypatch.setattr(imap, "read_modseq", lambda b: None)
    with Session(engine) as session:
        acc = MailAccount(email="synthetic@example.invalid", user_id=1, secret_enc="unused-synthetic")
        session.add(acc)
        session.commit()
        result = cache.sync_folder(session, acc, "synthetic", "INBOX")
        rows = session.exec(select(CachedMessage).where(CachedMessage.account_id == acc.id)).all()
        assert result["new"] == 2
        assert all(not row.detail_json for row in rows)
        assert any(row.uid == "7" and row.has_attachments for row in rows)
        assert box.client.payload_bytes < 1024


def test_header_order_does_not_depend_on_server_response_order():
    box = wire_box()
    fetch = box.client.uid
    box.client.uid = lambda command, uids, parts: fetch(command, ",".join(sorted(uids.split(","))), parts)
    assert [m.uid for m in imap.fetch_headers(box, ["7", "8"], reverse=True)] == ["8", "7"]


def test_metadata_network_errors_do_not_retry_on_a_broken_connection():
    box = wire_box()
    def offline(*a):
        raise TimeoutError("synthetic timeout")
    box.client.uid = offline
    with pytest.raises(TimeoutError):
        list(imap.fetch_headers(box, ["7"]))


def test_known_empty_folder_returns_cache_without_live_fallback(client, admin, account, monkeypatch):
    monkeypatch.setattr(cache, "has_cache", lambda *a: True)
    monkeypatch.setattr(cache, "known_empty", lambda *a: True)
    monkeypatch.setattr(cache, "read_messages", lambda *a, **kw: [])
    monkeypatch.setattr(cache, "sync_folder", lambda *a, **kw: pytest.fail("known empty is not a cache failure"))
    monkeypatch.setattr(imap, "list_messages", lambda *a, **kw: pytest.fail("no live retry"))
    response = client.get(f"/api/v1/mail/{account}/messages?folder=Empty", headers=admin)
    assert response.status_code == 200 and response.json() == []


def test_prefetch_guard_is_released_after_failure(client, admin, account, monkeypatch):
    from app.api import mail
    monkeypatch.setattr(mail, "_PREFETCH_ACTIVE", set())
    def error(*a):
        raise RuntimeError("synthetic error")
    monkeypatch.setattr(cache, "uncached_detail_uids", error)
    response = client.post(f"/api/v1/mail/{account}/messages/prefetch", headers=admin,
                           json={"folder": "INBOX", "uids": ["7"]})
    assert response.json()["ok"] is False
    assert not mail._PREFETCH_ACTIVE
