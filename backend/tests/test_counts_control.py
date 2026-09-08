from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.mail.counts_control import CountsGate
from app.api import mail
from app.mail import cache, imap


def test_nonblocking_gate_allows_one_job_per_account_and_cools_down():
    now = [100.0]
    gate = CountsGate(now=lambda: now[0])
    assert gate.claim(9)
    assert not gate.claim(9)
    assert gate.claim(10)
    gate.finish(9)
    now[0] += 29.99
    assert not gate.claim(9)
    now[0] += 0.01
    assert gate.claim(9)
    assert not gate.claim(10), "active job is never expired"


def test_parallel_admission_is_atomic():
    gate, ready = CountsGate(), Barrier(8)
    def claim(_):
        ready.wait(timeout=2)
        return gate.claim(9)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(claim, range(8))) == 1


def test_gate_is_bounded_and_does_not_evict_running_work():
    now = [0.0]
    gate = CountsGate(max_entries=2, now=lambda: now[0])
    assert gate.claim(1) and gate.claim(2)
    assert not gate.claim(3)
    gate.finish(1)
    now[0] = 31
    assert gate.claim(3)
    assert not gate.claim(2)


@pytest.fixture
def isolated_gate(monkeypatch):
    gate = CountsGate()
    monkeypatch.setattr(mail, "counts_gate", gate)
    return gate


@pytest.mark.parametrize("live", [False, True])
def test_busy_counts_return_existing_cache_without_live_work(client, admin, account, monkeypatch, isolated_gate, live):
    rows = [{"name": "INBOX", "total": 1, "unseen": 0}]
    monkeypatch.setattr(cache, "read_folder_counts", lambda *a: rows)
    monkeypatch.setattr(mail, "_override_unseen_from_cache", lambda *a: a[-1])
    monkeypatch.setattr(imap, "folder_counts", lambda *a: pytest.fail("must use cache"))
    isolated_gate.claim(account)
    response = client.get(f"/api/v1/mail/{account}/folders/counts?live={int(live)}", headers=admin)
    assert response.status_code == 200 and response.json() == rows


def test_busy_cold_counts_retry_later_instead_of_duplicate_work(client, admin, account, monkeypatch, isolated_gate):
    monkeypatch.setattr(cache, "read_folder_counts", lambda *a: [])
    monkeypatch.setattr(imap, "folder_counts", lambda *a: pytest.fail("no duplicate work"))
    isolated_gate.claim(account)
    response = client.get(f"/api/v1/mail/{account}/folders/counts?live=1", headers=admin)
    assert response.status_code == 503 and response.headers["retry-after"] == "30"


def test_count_error_releases_active_job_and_sets_cooldown(client, admin, account, monkeypatch, isolated_gate):
    monkeypatch.setattr(cache, "read_folder_counts", lambda *a: [])
    def busy(*a):
        raise imap.ImapBusyError()
    monkeypatch.setattr(imap, "folder_counts", busy)
    response = client.get(f"/api/v1/mail/{account}/folders/counts?live=1", headers=admin)
    assert response.status_code == 503
    assert isolated_gate.entries[account] is not None


def test_counts_still_require_account_ownership(client, admin, monkeypatch, isolated_gate):
    monkeypatch.setattr(cache, "read_folder_counts", lambda *a: pytest.fail("ownership first"))
    response = client.get("/api/v1/mail/987654321/folders/counts?live=1", headers=admin)
    assert response.status_code == 404 and not isolated_gate.entries
