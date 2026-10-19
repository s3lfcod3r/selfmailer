"""Cleanup HTTP flow with real search/move logic and synthetic IMAP transport."""
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest

from app.mail import imap


@pytest.fixture
def cleanup_box(monkeypatch):
    box = NS(generation=321, moves=[], fail=False)
    box.messages = {str(n): NS(uid=str(n), subject="Invoice", from_="shop@example.org",
        date_str="2026-01-01", date=None, flags=(imap.SEEN,), text="", html="",
        has_attachments_hint=False) for n in range(1, 251)}
    box.folder = NS(status=lambda *a: {"UIDVALIDITY": box.generation})
    box.uids = lambda criteria: list(box.messages)

    def move(uids, dest):
        if box.fail:
            raise OSError("synthetic move failure")
        box.moves.append((list(uids), dest))
        for uid in uids:
            box.messages.pop(uid, None)

    box.move = move

    @contextmanager
    def mailbox(*args, **kwargs):
        yield box

    def headers(connection, uids, **kwargs):
        yield from (box.messages[uid] for uid in uids if uid in box.messages)

    monkeypatch.setattr(imap, "_mailbox", mailbox)
    monkeypatch.setattr(imap, "fetch_headers", headers)
    return box


def search(client, admin, account, offset=0):
    r = client.get(f"/api/v1/mail/{account}/search", headers=admin,
        params={"folder": "INBOX", "from": "shop@example.org", "flagged": "false", "limit": 100, "offset": offset})
    assert r.status_code == 200, r.text
    return r.json()


def move(client, admin, account, rows, **overrides):
    return client.post(f"/api/v1/mail/{account}/messages/batch-move", headers=admin,
        json={"folder": "INBOX", "dest": "Archive", "uids": [r["uid"] for r in rows],
              "uidvalidity": rows[0]["uidvalidity"], **overrides})


def test_preview_then_move_only_current_page_and_refresh(client, admin, account, cleanup_box):
    first = search(client, admin, account)
    assert first["complete"] and first["truncated"] and first["total_count"] == 250
    assert len(first["items"]) == 100 and cleanup_box.moves == []
    second = search(client, admin, account, 100)
    assert second["items"][0]["uid"] == "150"
    r = move(client, admin, account, first["items"])
    assert r.status_code == 200 and r.json()["result"]["count"] == 100
    assert cleanup_box.moves == [([str(n) for n in range(250, 150, -1)], "Archive")]
    remaining = search(client, admin, account)
    assert remaining["total_count"] == 150 and remaining["items"][0]["uid"] == "150"
    assert "1" in cleanup_box.messages  # older matches are not silently removed


def test_mailbox_replacement_between_preview_and_move_is_rejected(client, admin, account, cleanup_box):
    rows = search(client, admin, account)["items"]
    cleanup_box.generation += 1
    r = move(client, admin, account, rows)
    assert r.status_code == 409, r.text
    assert cleanup_box.moves == [] and len(cleanup_box.messages) == 250


def test_failed_move_reports_error_and_preserves_synthetic_messages(client, admin, account, cleanup_box):
    rows = search(client, admin, account)["items"]
    cleanup_box.fail = True
    assert move(client, admin, account, rows).status_code == 502
    assert cleanup_box.moves == [] and len(cleanup_box.messages) == 250


def test_move_requires_target_and_valid_uid_generation(client, admin, account, cleanup_box):
    rows = search(client, admin, account)["items"]
    assert move(client, admin, account, rows, dest="").status_code == 400
    assert move(client, admin, account, rows, uidvalidity=0).status_code == 422
    assert cleanup_box.moves == []


def test_cleanup_endpoints_require_login(client, admin, account, cleanup_box):
    assert client.get(f"/api/v1/mail/{account}/search?from=shop@example.org").status_code == 401
    r = client.post(f"/api/v1/mail/{account}/messages/batch-move",
        json={"folder": "INBOX", "dest": "Archive", "uids": ["1"], "uidvalidity": 321})
    assert r.status_code == 401 and cleanup_box.moves == []


def test_unverifiable_mailbox_identity_blocks_move(client, admin, account, cleanup_box):
    rows = search(client, admin, account)["items"]
    cleanup_box.generation = 0
    assert move(client, admin, account, rows).status_code == 409
    assert cleanup_box.moves == [] and len(cleanup_box.messages) == 250
