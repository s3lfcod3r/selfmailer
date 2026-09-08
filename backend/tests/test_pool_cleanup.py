"""Idle cleanup must neither delay requests nor lose concurrent pool entries."""
import threading
import time
from types import SimpleNamespace

import pytest

from app.mail import imap
from app.models import MailAccount


KEY = "991:synthetic@imap.example.invalid:993"


@pytest.fixture
def isolated_pool(monkeypatch):
    pool = {}
    monkeypatch.setattr(imap, "_POOL", pool)
    monkeypatch.setattr(imap, "_POOL_ENABLED", True)
    return pool


@pytest.fixture
def real_reaper(monkeypatch, no_background_pool_cleanup):
    monkeypatch.setattr(imap, "_schedule_reap_idle", no_background_pool_cleanup)
    monkeypatch.setattr(imap, "_REAPER_THREAD", None)
    monkeypatch.setattr(imap, "_REAPER_NEXT", 0.0)
    yield no_background_pool_cleanup
    worker = imap._REAPER_THREAD
    if worker is not None:
        worker.join(2)
        assert not worker.is_alive(), "cleanup worker leaked across test boundary"


def _idle(box):
    conn = imap._Conn()
    conn.box = box
    conn.last_used = time.monotonic() - imap._IDLE_TTL - 10
    return conn


def test_cleanup_preserves_connection_added_during_logout(isolated_pool):
    created = []

    def concurrent_acquire():
        # Scheduling point: the old connection is locked, but the global lock
        # must be free while LOGOUT waits on the network.
        new = imap._greife_zu(KEY, "INBOX", limit=3)
        assert new is not None
        new.box = object()
        new.holder = ("in-flight", "INBOX", time.monotonic())
        created.append(new)

    old = _idle(SimpleNamespace(logout=concurrent_acquire))
    isolated_pool[KEY] = [old]
    try:
        imap._reap_idle()
        assert created[0] in isolated_pool[KEY], "live connection lost through stale snapshot overwrite"
        assert created[0].box is not None and created[0].lock.locked()
    finally:
        for conn in created:
            conn.lock.release()


def test_new_connection_survives_actual_concurrent_sweep(isolated_pool):
    started, release = threading.Event(), threading.Event()

    def logout():
        started.set()
        release.wait(3)

    old = _idle(SimpleNamespace(logout=logout))
    isolated_pool[KEY] = [old]
    sweep = threading.Thread(target=imap._reap_idle)
    sweep.start()
    new = None
    try:
        assert started.wait(1)
        new = imap._greife_zu(KEY, "INBOX", limit=2)
        assert new is not None and new is not old
        new.box = object()
        new.holder = ("in-flight", "INBOX", time.monotonic())
        release.set()
        sweep.join(2)
        assert not sweep.is_alive()
        assert isolated_pool[KEY] == [new] and new.lock.locked()
    finally:
        release.set()
        sweep.join(2)
        if new is not None:
            new.lock.release()


def test_finished_request_does_not_wait_for_unrelated_logout(isolated_pool, monkeypatch, real_reaper):
    logout_started, release_logout, logout_done = (threading.Event() for _ in range(3))
    returned = threading.Event()
    failures = []

    def slow_logout():
        logout_started.set()
        release_logout.wait(3)
        logout_done.set()

    isolated_pool["other-account"] = [_idle(SimpleNamespace(logout=slow_logout))]
    account = MailAccount(id=992, email="synthetic@example.invalid", imap_host="imap.example.invalid")
    monkeypatch.setattr(imap, "_ensure_box", lambda *a: object())

    def request():
        try:
            with imap._mailbox(account, "synthetic", op="test-only"):
                pass
            returned.set()
        except Exception as exc:
            failures.append(exc)

    thread = threading.Thread(target=request)
    thread.start()
    try:
        assert logout_started.wait(1), "cleanup was not exercised"
        assert returned.wait(0.5), "finished request waited on another account's LOGOUT"
        assert not logout_done.is_set()
    finally:
        release_logout.set()
        thread.join(2)
        assert logout_done.wait(2)
    assert not failures


def test_cleanup_keeps_busy_and_fresh_connections(isolated_pool):
    closed = []
    expired = _idle(SimpleNamespace(logout=lambda: closed.append("expired")))
    busy = _idle(SimpleNamespace(logout=lambda: closed.append("busy")))
    fresh = _idle(SimpleNamespace(logout=lambda: closed.append("fresh")))
    fresh.last_used = time.monotonic()
    busy.lock.acquire()
    isolated_pool[KEY] = [expired, busy, fresh]
    try:
        imap._reap_idle()
        assert closed == ["expired"]
        assert isolated_pool[KEY] == [busy, fresh]
        assert busy.lock.locked() and not fresh.lock.locked()
    finally:
        busy.lock.release()


def test_cleanup_rechecks_age_after_acquiring_connection(isolated_pool):
    closed = []
    conn = _idle(SimpleNamespace(logout=lambda: closed.append(True)))

    class RefreshedBeforeLock:
        def acquire(self, blocking):
            assert blocking is False
            conn.last_used = time.monotonic()
            return True

        def release(self):
            pass

    conn.lock = RefreshedBeforeLock()
    isolated_pool[KEY] = [conn]
    imap._reap_idle()
    assert closed == []
    assert isolated_pool[KEY] == [conn]


def test_closing_socket_counts_toward_limit_and_does_not_hold_global_lock(isolated_pool):
    observed = []

    def logout():
        # A closing socket must not be replaced before it is actually closed.
        observed.append(imap._greife_zu(KEY, "INBOX", limit=1))
        other = imap._greife_zu("unrelated", "INBOX", limit=1)
        observed.append(other)
        other.last_used = time.monotonic()
        other.lock.release()

    isolated_pool[KEY] = [_idle(SimpleNamespace(logout=logout))]
    imap._reap_idle()
    assert observed[0] is None and observed[1] is not None
    assert KEY not in isolated_pool
    assert isolated_pool["unrelated"] == [observed[1]]


def test_logout_failure_releases_slot_and_removes_empty_account(isolated_pool):
    def fails():
        raise OSError("synthetic failure")

    conn = _idle(SimpleNamespace(logout=fails))
    isolated_pool[KEY] = [conn]
    imap._reap_idle()
    assert KEY not in isolated_pool
    assert conn.box is None and conn.folder is None and not conn.lock.locked()


def test_simultaneous_requests_start_only_one_reaper(monkeypatch, real_reaper):
    started, release = threading.Event(), threading.Event()
    calls = []

    def sweep():
        calls.append(True)
        started.set()
        release.wait(3)

    monkeypatch.setattr(imap, "_reap_idle", sweep)
    # An elapsed throttle interval must still never overlap a running sweep.
    monkeypatch.setattr(imap, "_REAPER_INTERVAL", 0)
    callers = [threading.Thread(target=real_reaper) for _ in range(12)]
    try:
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join(1)
            assert not caller.is_alive()
        assert started.wait(1)
        assert calls == [True]
        assert imap._REAPER_THREAD.daemon
    finally:
        release.set()
        for caller in callers:
            if caller.ident is not None:
                caller.join(1)


def test_finished_sweep_is_throttled_and_later_restarts(monkeypatch, real_reaper):
    clock, calls = [100.0], []
    monkeypatch.setattr(imap, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(imap, "_reap_idle", lambda: calls.append(True))
    real_reaper()
    first = imap._REAPER_THREAD
    first.join(1)
    assert not first.is_alive()
    real_reaper()
    assert imap._REAPER_THREAD is first and calls == [True]
    clock[0] += imap._REAPER_INTERVAL
    real_reaper()
    imap._REAPER_THREAD.join(1)
    assert imap._REAPER_THREAD is not first and calls == [True, True]


def test_thread_start_failure_does_not_fail_finished_request(monkeypatch, real_reaper, caplog):
    class NoThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("synthetic-sensitive-detail")

    monkeypatch.setattr(imap, "threading", SimpleNamespace(Thread=NoThread))
    real_reaper()
    assert imap._REAPER_THREAD is None
    assert "synthetic-sensitive-detail" not in caplog.text
    assert "konnte nicht gestartet" in caplog.text
    assert imap._REAPER_LOCK.acquire(blocking=False)
    imap._REAPER_LOCK.release()


def test_scheduling_never_waits_for_another_scheduling_request(real_reaper):
    returned = threading.Event()

    def schedule():
        real_reaper()
        returned.set()

    imap._REAPER_LOCK.acquire()
    caller = threading.Thread(target=schedule)
    caller.start()
    try:
        assert returned.wait(0.5), "request blocked on the cleanup scheduling lock"
        assert imap._REAPER_THREAD is None
    finally:
        imap._REAPER_LOCK.release()
        caller.join(2)


def test_worker_failure_is_sanitized_and_does_not_prevent_next_sweep(monkeypatch, real_reaper, caplog):
    def fails():
        raise ValueError("synthetic-sensitive-detail")

    monkeypatch.setattr(imap, "_reap_idle", fails)
    real_reaper()
    imap._REAPER_THREAD.join(1)
    assert not imap._REAPER_THREAD.is_alive()
    assert "synthetic-sensitive-detail" not in caplog.text
    assert "Aufraeumen fehlgeschlagen" in caplog.text
    calls = []
    monkeypatch.setattr(imap, "_REAPER_NEXT", 0)
    monkeypatch.setattr(imap, "_reap_idle", lambda: calls.append(True))
    real_reaper()
    imap._REAPER_THREAD.join(1)
    assert calls == [True]
