"""Content-free tracing, real API/threadpool propagation and IMAP safeguards."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from contextvars import copy_context
import json
from threading import Barrier, Thread
from types import SimpleNamespace as NS

from fastapi import HTTPException
import pytest

from app.api import mail
from app.mail import cache, imap, timing
from app.models import MailAccount


@pytest.fixture(autouse=True)
def record_all(monkeypatch):
    monkeypatch.setenv("SELFMAILER_MAIL_TIMING", "1")
    monkeypatch.setenv("SELFMAILER_MAIL_TIMING_SLOW_MS", "0")


def records(caplog):
    return [json.loads(r.getMessage().removeprefix("mail_timing "))
            for r in caplog.records if r.name == timing.logger.name]


@pytest.mark.parametrize(("method", "tail", "operation"), [
    ("GET", "messages", "list"), ("GET", "messages/opaque-uid", "read"),
    ("DELETE", "messages/7", "delete"), ("POST", "sync", "sync"),
    ("POST", "messages/prefetch", None), ("GET", "messages/7/raw", None),
    ("GET", "messages/7/attachment/0", None), ("POST", "messages/7/move", None),
    ("GET", "sync", None), ("DELETE", "messages", None),
])
def test_only_whitelisted_routes(method, tail, operation):
    assert timing.classify(method, f"/api/v1/mail/12/{tail}") == ((12, operation) if operation else None)
    assert timing.classify(method, f"/api/v1/mail/not-an-id/{tail}") is None


def test_phase_counts_and_inclusive_duration(monkeypatch, caplog):
    clock = NS(now=0.0)
    monkeypatch.setattr(timing, "perf_counter", lambda: clock.now)
    with timing.request_scope(1, "read") as trace:
        timing.authorize(1)
        trace.status = 200
        with timing.phase("cache_read"):
            clock.now += 0.1
        with timing.phase("cache_read"):
            clock.now += 0.2
        with timing.phase("fetch_body"):
            clock.now += 0.4
            with timing.phase("parse_body"):
                clock.now += 0.05
    event, = records(caplog)
    assert event["duration_ms"] == 750
    assert event["phases"]["cache_read"] == {"ms": 300, "count": 2, "failed": 0}
    assert event["phases"]["fetch_body"]["ms"] == 450
    assert event["phases"]["parse_body"]["ms"] == 50
    assert not event["failed"]
    assert timing._current.get() is None


def test_threshold_errors_privacy_and_closed_context(monkeypatch, caplog):
    monkeypatch.setenv("SELFMAILER_MAIL_TIMING_SLOW_MS", "100000")
    private = "sensitive@example.invalid/password/UID-938643/Subject\nFORGED"
    with timing.request_scope(2, "delete") as trace:
        timing.authorize(2)
        trace.status = 200
        stale = copy_context()
        timing.mark("source", private)
        timing.mark(private, "cache")
        with timing.phase(private):
            pass
        with pytest.raises(RuntimeError, match="sensitive"):
            with timing.phase("move"):
                raise RuntimeError(private)
    event, = records(caplog)
    assert event["failed"] and event["status"] == 200
    assert event["phases"]["move"]["failed"] == 1
    assert private not in json.dumps(event)
    assert set(event["phases"]) == {"move"}
    assert "source" not in event
    assert all(r.exc_info is None for r in caplog.records if r.name == timing.logger.name)
    stale.run(timing.mark, "source", "cache")
    stale.run(lambda: timing.measured("cache_read")(lambda: None)())
    assert "source" not in trace.marks and "cache_read" not in trace.phases


@pytest.mark.parametrize("value", ["bad", "nan", "inf", "-1", ""])
def test_invalid_threshold_uses_safe_default(monkeypatch, value):
    monkeypatch.setenv("SELFMAILER_MAIL_TIMING_SLOW_MS", value)
    assert timing._threshold() == 1000


@pytest.mark.parametrize("value", ["0", "false", "OFF"])
def test_disabled_tracing_is_noop(monkeypatch, caplog, value):
    monkeypatch.setenv("SELFMAILER_MAIL_TIMING", value)
    with timing.request_scope(1, "delete") as trace:
        assert trace is None
        timing.authorize(1)
        timing.mark("result", "moved")
        with timing.phase("move"):
            pass
    assert not records(caplog)


@pytest.mark.parametrize(("elapsed", "status", "emitted"), [(0.2, 200, False), (1.5, 200, True), (0.2, 409, True)])
def test_threshold_and_http_error(monkeypatch, caplog, elapsed, status, emitted):
    monkeypatch.setenv("SELFMAILER_MAIL_TIMING_SLOW_MS", "1000")
    clock = NS(now=0.0)
    monkeypatch.setattr(timing, "perf_counter", lambda: clock.now)
    with timing.request_scope(1, "read") as trace:
        timing.authorize(1)
        trace.status = status
        clock.now = elapsed
    assert bool(records(caplog)) is emitted


def test_unhandled_error_resets_context_without_exception_text(caplog):
    original = RuntimeError("PRIVATE_EXCEPTION")
    with pytest.raises(RuntimeError) as caught:
        with timing.request_scope(1, "read"):
            timing.authorize(1)
            raise original
    assert caught.value is original
    event, = records(caplog)
    assert event["status"] is None and event["failed"]
    assert "PRIVATE_EXCEPTION" not in json.dumps(event)
    assert timing._current.get() is None


def test_foreign_account_check_never_authorizes_trace(caplog):
    with timing.request_scope(1, "delete") as trace:
        with pytest.raises(HTTPException) as caught:
            mail._account(1, NS(id=5), NS(get=lambda *a: NS(id=1, user_id=6)))
        assert caught.value.status_code == 404
        timing.authorize(2)  # different account must not authorize the request either
        trace.status = 404
    assert not records(caplog)


def test_api_auth_and_missing_account_do_not_emit(client, admin, caplog):
    assert client.delete("/api/v1/mail/9999999/messages/7").status_code == 401
    assert client.delete("/api/v1/mail/9999999/messages/7", headers=admin).status_code == 404
    assert not records(caplog)


def test_delete_api_has_one_write_and_unchanged_response(client, admin, account, monkeypatch, caplog):
    calls = []
    def remove(*args, **kwargs):
        calls.append((args, kwargs))
        with timing.phase("move"):
            return "moved"
    monkeypatch.setattr(imap, "delete_message", remove)
    response = client.delete(f"/api/v1/mail/{account}/messages/PRIVATE_UID_938643",
                             params={"folder": "PRIVATE_FOLDER", "uidvalidity": 123},
                             headers={**admin, "X-Request-ID": "PRIVATE_REQUEST_ID"})
    assert response.status_code == 200 and response.json() == {"ok": True, "result": "moved"}
    assert len(calls) == 1 and calls[0][1] == {"folder": "PRIVATE_FOLDER", "uidvalidity": 123}
    event, = records(caplog)
    assert event["account_id"] == account and event["operation"] == "delete"
    assert event["result"] == "moved" and event["status"] == 200
    assert {"account_lookup", "cache_generation", "move", "cache_hide", "notify"} <= event["phases"].keys()
    for secret in ("PRIVATE_FOLDER", "PRIVATE_UID_938643", "PRIVATE_REQUEST_ID", admin["Authorization"]):
        assert secret not in json.dumps(event)


def test_unbounded_root_fields_are_rejected(caplog):
    for account_id, operation in [(1, "private@example.invalid"), ("private@example.invalid", "read"), (-1, "read")]:
        with timing.request_scope(account_id, operation) as trace:
            assert trace is None
    assert not records(caplog)


def test_logging_failure_does_not_retry_or_fail_delete(client, admin, account, monkeypatch):
    calls = []
    monkeypatch.setattr(imap, "delete_message", lambda *a, **kw: calls.append(1) or "moved")
    def broken_logger(*a, **kw):
        raise OSError("logger unavailable")
    monkeypatch.setattr(timing.logger, "warning", broken_logger)
    response = client.delete(f"/api/v1/mail/{account}/messages/7", headers=admin)
    assert response.status_code == 200 and response.json() == {"ok": True, "result": "moved"}
    assert calls == [1]


def test_parallel_api_workers_keep_separate_traces(client, admin, account, monkeypatch, caplog):
    barrier = Barrier(2)
    def remove(acc, password, uid, **kwargs):
        with timing.phase("move" if uid == "7" else "delete"):
            barrier.wait(timeout=5)
            return "moved" if uid == "7" else "deleted"
    monkeypatch.setattr(imap, "delete_message", remove)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda uid: client.delete(f"/api/v1/mail/{account}/messages/{uid}", headers=admin), ["7", "8"]))
    assert all(r.status_code == 200 for r in responses)
    events = records(caplog)
    assert len(events) == 2 and len({e["request_id"] for e in events}) == 2
    assert {e["result"] for e in events} == {"moved", "deleted"}
    for event in events:
        assert ("move" in event["phases"]) != ("delete" in event["phases"])
    assert timing._current.get() is None


def test_background_thread_is_not_attached_to_request(caplog):
    with timing.request_scope(1, "read") as trace:
        timing.authorize(1)
        trace.status = 200
        worker = Thread(target=lambda: timing.measured("logout")(lambda: None)())
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert records(caplog)[0]["phases"] == {}


@pytest.mark.parametrize("source", ["cache", "imap", "cached_fallback", "header_fallback", "miss"])
def test_read_source_and_contract(client, admin, account, monkeypatch, caplog, source):
    detail = {"uid": "7", "subject": "PRIVATE_SUBJECT", "from": "private@example.invalid", "date": "",
              "seen": False, "flagged": False, "auth": {"verdict": "unknown"}, "text": "PRIVATE_BODY"}
    cached = detail if source == "cache" else {**detail, "auth": None} if source == "cached_fallback" else None
    monkeypatch.setattr(cache, "read_detail", timing.measured("cache_read")(lambda *a: cached))
    monkeypatch.setattr(cache, "read_header", lambda *a: detail if source == "header_fallback" else None)
    def fetch(*a, **kw):
        assert source != "cache", "warm cache must not trigger IMAP"
        return detail if source == "imap" else None
    monkeypatch.setattr(imap, "get_message", fetch)
    response = client.get(f"/api/v1/mail/{account}/messages/7", headers=admin)
    assert response.status_code == (404 if source == "miss" else 200)
    event, = records(caplog)
    assert event["source"] == source and event["phases"]["cache_read"]["count"] >= 1
    assert "PRIVATE" not in json.dumps(event) and "private@example.invalid" not in json.dumps(event)


def test_busy_sync_records_folder_wait_without_calling_imap(client, admin, account, monkeypatch, caplog):
    lock = cache._folder_lock(account, "PRIVATE_FOLDER")
    monkeypatch.setattr(cache, "_sync_folder_unlocked", lambda *a, **kw: pytest.fail("busy folder must not sync"))
    with lock:
        response = client.post(f"/api/v1/mail/{account}/sync", params={"folder": "PRIVATE_FOLDER"}, headers=admin)
    assert response.status_code == 200 and response.json() == {"ok": True, "busy": True, "new": 0}
    event, = records(caplog)
    assert event["result"] == "busy" and event["failed"]
    assert event["phases"]["folder_lock_wait"]["failed"] == 1
    assert "pool_wait" not in event["phases"]


def test_busy_pool_records_wait_without_connect(monkeypatch, caplog):
    monkeypatch.setattr(imap, "_POOL_ENABLED", True)
    monkeypatch.setattr(imap, "_greife_zu", lambda *a: None)
    monkeypatch.setattr(imap, "_connect", lambda *a: pytest.fail("no slot, no connect"))
    with timing.request_scope(987, "read") as trace:
        timing.authorize(987)
        with pytest.raises(imap.ImapBusyError):
            with imap._mailbox(MailAccount(id=987, email="private@example.invalid"), "PRIVATE_PASSWORD", lock_timeout=0):
                pytest.fail("busy")
        trace.status = 503
    event, = records(caplog)
    assert event["phases"]["pool_wait"]["failed"] == 1
    assert "connect" not in event["phases"]


@pytest.mark.parametrize("caps, expected", [(('MOVE',), "native"), ((), "copy_delete"), (None, "unknown")])
def test_delete_phases_and_uidvalidity_guard(monkeypatch, caplog, caps, expected):
    calls = []
    box = NS(client=NS(capabilities=caps),
             folder=NS(status=lambda *a: {"UIDVALIDITY": 123},
                       list=lambda: [NS(name="PRIVATE_TRASH", flags=(r"\Trash",))]),
             move=lambda *a: calls.append(a), delete=lambda *a: pytest.fail("must move"))
    monkeypatch.setattr(imap, "_mailbox", lambda *a, **kw: nullcontext(box))
    with timing.request_scope(1, "delete") as trace:
        timing.authorize(1)
        assert imap.delete_message(NS(id=1), "PRIVATE_PASSWORD", "7", uidvalidity=123) == "moved"
        trace.status = 200
    event, = records(caplog)
    assert event["move_mode"] == expected
    assert {"uidvalidity", "folder_list", "move"} == event["phases"].keys()
    assert calls == [("7", "PRIVATE_TRASH")]
    caplog.clear()
    with timing.request_scope(1, "delete") as trace:
        timing.authorize(1)
        with pytest.raises(HTTPException) as caught:
            imap.delete_message(NS(id=1), "PRIVATE_PASSWORD", "7", uidvalidity=456)
        assert caught.value.status_code == 409
        trace.status = 409
    assert calls == [("7", "PRIVATE_TRASH")], "stale UIDVALIDITY must not write"
    event, = records(caplog)
    assert event["phases"]["uidvalidity"]["failed"] == 1 and "move" not in event["phases"]


def test_connect_select_and_swallowed_logout_failure(monkeypatch, caplog):
    calls = []
    def fail_logout():
        raise OSError("PRIVATE_SERVER_ERROR")
    box = NS(client=NS(sock=NS(settimeout=lambda *a: None), untagged_responses={}),
             folder=NS(set=lambda f: calls.append("select")),
             login=lambda *a, **kw: calls.append("login"), logout=fail_logout)
    monkeypatch.setattr(imap, "MailBox", lambda *a, **kw: box)
    monkeypatch.setattr(imap, "supports_condstore", lambda b: False)
    with timing.request_scope(1, "read") as trace:
        timing.authorize(1)
        assert imap._connect(NS(imap_host="private.invalid", imap_port=993), "PRIVATE_LOGIN", "PRIVATE_PASSWORD", "INBOX") is box
        imap._close(box)
        trace.status = 200
    event, = records(caplog)
    assert calls == ["login", "select"]
    assert {"connect", "login", "select", "logout"} == event["phases"].keys()
    assert event["phases"]["logout"]["failed"] == 1 and event["failed"]
    assert "PRIVATE" not in json.dumps(event)
