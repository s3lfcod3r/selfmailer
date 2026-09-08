"""Bounded, content-free timings for selected, authenticated mail requests.

The mutable trace follows ContextVars into FastAPI's synchronous worker thread.
It is NOT propagated to our background sync/reaper threads. Never pass request
paths, folders, UIDs, credentials, exception strings or message data here.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import json
import logging
import math
import os
import re
from threading import Lock
from time import perf_counter
from uuid import uuid4

logger = logging.getLogger("selfmailer.mail_timing")

_PHASES = frozenset({
    "account_lookup", "cache_generation", "pool_wait", "connect", "login", "capability",
    "condstore", "select", "logout", "uidvalidity", "folder_list", "fetch_body",
    "parse_body", "move", "delete", "cache_list", "cache_read", "cache_header",
    "cache_write", "cache_hide", "sync_total", "folder_lock_wait", "folder_status",
    "uid_search", "notify",
})
_MARKS = {
    "source": {"cache", "imap", "cached_fallback", "header_fallback", "miss"},
    "result": {"moved", "deleted", "busy"},
    "move_mode": {"native", "copy_delete", "unknown"},
}
_OPERATIONS = frozenset({"list", "read", "delete", "sync"})
_MAIL_ROUTE = re.compile(r"/api/v1/mail/([0-9]{1,18})/(messages(?:/[^/]+)?|sync)")


def classify(method: str, path: str) -> tuple[int, str] | None:
    """Whitelist routes; discard path/UID immediately (never attach to trace)."""
    match = _MAIL_ROUTE.fullmatch(path)
    if not match:
        return None
    tail = match[2]
    operation = None
    if method == "GET":
        operation = "list" if tail == "messages" else "read" if tail.startswith("messages/") else None
    elif method == "DELETE" and tail.startswith("messages/"):
        operation = "delete"
    elif method == "POST" and tail == "sync":
        operation = "sync"
    return (int(match[1]), operation) if operation else None


def _threshold() -> float | None:
    if os.getenv("SELFMAILER_MAIL_TIMING", "1").strip().lower() in {"0", "false", "no", "off"}:
        return None
    try:
        value = float(os.getenv("SELFMAILER_MAIL_TIMING_SLOW_MS", "1000"))
        return value if math.isfinite(value) and value >= 0 else 1000.0
    except ValueError:
        return 1000.0


@dataclass
class _Trace:
    account_id: int
    operation: str
    threshold_ms: float
    started: float = field(default_factory=lambda: perf_counter())
    request_id: str = field(default_factory=lambda: uuid4().hex)
    authorized: bool = False
    status: int | None = None
    failed: bool = False
    closed: bool = False
    phases: dict = field(default_factory=dict)
    marks: dict = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)

    def finish(self) -> None:
        with self.lock:
            self.closed = True
            duration_ms = max(0.0, (perf_counter() - self.started) * 1000)
            failed = self.failed or self.status is None or self.status >= 400
            if not self.authorized or (duration_ms < self.threshold_ms and not failed):
                return
            payload = {
                "event": "mail_timing", "schema": 1,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request_id": self.request_id, "account_id": self.account_id,
                "operation": self.operation, "status": self.status,
                "duration_ms": round(duration_ms, 2), "failed": failed,
                "phases": {name: {**values, "ms": round(values["ms"], 2)} for name, values in self.phases.items()},
                **self.marks,
            }
        # Diagnostic failure must never make the client retry a successful DELETE.
        # WARNING is visible under the default Uvicorn logging configuration.
        try:
            logger.warning("mail_timing %s", json.dumps(payload, separators=(",", ":")))
        except Exception:
            pass


_current: ContextVar[_Trace | None] = ContextVar("mail_timing", default=None)


@contextmanager
def request_scope(account_id: int, operation: str):
    threshold = _threshold()
    allowed = type(account_id) is int and 0 <= account_id < 10**18 and operation in _OPERATIONS
    trace = _Trace(account_id, operation, threshold) if threshold is not None and allowed else None
    token = _current.set(trace)
    try:
        yield trace
    except BaseException:
        if trace is not None:
            trace.failed = True
        raise
    finally:
        _current.reset(token)
        if trace is not None:
            trace.finish()


def authorize(account_id: int) -> None:
    """Call ONLY after the existing account ownership check succeeds."""
    trace = _current.get()
    if trace is not None and trace.account_id == account_id:
        trace.authorized = True


def mark(name: str, value: str) -> None:
    trace = _current.get()
    if trace is not None and value in _MARKS.get(name, ()):
        with trace.lock:
            if not trace.closed:
                trace.marks[name] = value


@contextmanager
def phase(name: str):
    trace = _current.get()
    if trace is None or name not in _PHASES:
        yield
        return
    started = perf_counter()
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        elapsed = max(0.0, (perf_counter() - started) * 1000)
        with trace.lock:
            if not trace.closed:
                values = trace.phases.setdefault(name, {"ms": 0.0, "count": 0, "failed": 0})
                values["ms"] += elapsed
                values["count"] += 1
                values["failed"] += int(failed)
                trace.failed |= failed


def measured(name: str):
    """For synchronous helpers, NOT generators or FastAPI route functions."""
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with phase(name):
                return fn(*args, **kwargs)
        return wrapped
    return decorate
