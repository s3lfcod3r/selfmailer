"""Whole-folder cleanup previews must never silently lose older matches."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from app.mail import imap
from app.models import MailAccount


def preview(monkeypatch, count=250, *, missing=0, broken=False, **kwargs):
    seen = {}

    @contextmanager
    def mailbox(*args, folder, **options):
        if broken and folder == "Broken":
            raise OSError("unavailable")

        def uids(criteria):
            seen["criteria"] = str(criteria)
            return [str(i) for i in range(1, count + 1)]
        yield SimpleNamespace(uids=uids)

    def headers(box, uids, **options):
        seen["uids"] = uids
        for uid in uids[missing:]:
            yield SimpleNamespace(uid=uid, subject="Invoice", from_="store@example.org", date_str="2026-10-01",
                                  flags=(imap.SEEN, "project"), text="", html="", date=None, has_attachments_hint=False)

    monkeypatch.setattr(imap, "_mailbox", mailbox)
    monkeypatch.setattr(imap, "mailbox_generation", lambda box, folder: 123)
    monkeypatch.setattr(imap, "fetch_headers", headers)
    result = imap.search_messages(MailAccount(id=1), "fake", "invoice", kwargs.pop("folders", ["INBOX"]), **kwargs)
    return result, seen


def test_default_search_reports_per_folder_truncation(monkeypatch):
    r, _ = preview(monkeypatch)
    assert len(r["items"]) == 50
    assert r["total_count"] == 250
    assert r["truncated"] and r["complete"]


def test_paging_reaches_headers_beyond_first_50(monkeypatch):
    r, seen = preview(monkeypatch, per_folder=100, total_limit=100, offset=100)
    assert seen["uids"] == [str(i) for i in range(150, 50, -1)]
    assert r["items"][0]["uidvalidity"] == 123
    assert r["items"][0]["labels"] == ["project"]
    assert r["offset"] == 100 and r["truncated"]


def test_last_page_has_no_more_matches(monkeypatch):
    r, _ = preview(monkeypatch, per_folder=100, total_limit=100, offset=200)
    assert len(r["items"]) == 50
    assert not r["truncated"]


def test_cleanup_protections_are_sent_to_imap(monkeypatch):
    _, seen = preview(monkeypatch, seen=True, flagged=False, keyword="project", before="2026-01-01")
    for criterion in ("SEEN", "UNFLAGGED", "KEYWORD", "BEFORE"):
        assert criterion in seen["criteria"]


def test_missing_headers_make_preview_incomplete(monkeypatch):
    r, _ = preview(monkeypatch, missing=1)
    assert not r["complete"] and r["timed_out"]


def test_folder_failure_is_not_presented_as_complete(monkeypatch):
    r, _ = preview(monkeypatch, folders=["INBOX", "Broken"], broken=True)
    assert r["errors"] == 1 and not r["complete"]
    assert r["folders_searched"] == 1


@pytest.mark.parametrize("params", ["before=2026-99-01", "keyword=bad%20label", "all_folders=true&offset=100"])
def test_invalid_search_parameters(client, admin, params):
    # Valid account owned by the test admin, no real mail server is needed.
    r = client.post("/api/v1/accounts", headers=admin, json={"label": "search test", "email": "search@example.org",
        "imap_host": "imap.example.org", "smtp_host": "smtp.example.org", "auth_user": "search@example.org", "password": "test"})
    assert r.status_code == 201, r.text
    account = r.json()["id"]
    try:
        r = client.get(f"/api/v1/mail/{account}/search?q=invoice&{params}", headers=admin)
        assert r.status_code == (400 if params.startswith("keyword") else 422), r.text
    finally:
        client.delete(f"/api/v1/accounts/{account}", headers=admin)


def test_search_endpoint_forwards_cleanup_filters(client, admin, monkeypatch):
    r = client.post("/api/v1/accounts", headers=admin, json={"email": "query@example.org", "imap_host": "imap.example.org",
        "smtp_host": "smtp.example.org", "password": "test"})
    assert r.status_code == 201
    account = r.json()["id"]
    captured = {}

    def search(*args, **kwargs):
        captured.update(kwargs)
        return {"items": [], "complete": True, "total_count": 0, "truncated": False}

    monkeypatch.setattr(imap, "search_messages", search)
    try:
        r = client.get(f"/api/v1/mail/{account}/search?from=shop@example.org&seen=true&flagged=false"
                       "&keyword=project&before=2026-01-01&offset=200&limit=100",
                       headers=admin)
        assert r.status_code == 200, r.text
        assert captured["seen"] is True and captured["flagged"] is False
        assert captured["keyword"] == "project" and captured["offset"] == 200 and captured["per_folder"] == 100
        assert captured["before"] == "2026-01-01" and captured["from_addr"] == "shop@example.org"
        assert client.get(f"/api/v1/mail/{account}/search?q=invoice").status_code == 401
    finally:
        client.delete(f"/api/v1/accounts/{account}", headers=admin)
