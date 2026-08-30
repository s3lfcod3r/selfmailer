"""Der Sync legt den Volltext gleich mit ab — kein zweiter Abruf derselben Mails.

Hintergrund: ``sync_folder`` lädt jede neue Mail KOMPLETT (Snippet und
Anhang-Flag brauchen den Body). Früher wurde der Body danach verworfen, und das
Frontend holte über ``/messages/prefetch`` dieselben Mails ein zweites Mal — bei
großen Postfächern (Gmail) der Hauptgrund für die lange erste Ladezeit.

Getestet wird ohne echten IMAP-Server: ``_mailbox`` wird durch eine Attrappe
ersetzt, die sich wie imap_tools verhält.
"""
from contextlib import contextmanager
from types import SimpleNamespace

from sqlmodel import Session, select

import app.mail.cache as cache_mod
from app.core.db import engine
from app.models import CachedMessage, MailAccount


class _FakeObj:
    """Minimales E-Mail-Objekt, wie es imap_tools unter ``msg.obj`` liefert."""

    def __init__(self, message_id: str):
        self._mid = message_id

    def get(self, key, default=""):
        return {"Message-ID": self._mid, "Disposition-Notification-To": ""}.get(key, default)

    def get_all(self, _key):
        return []  # keine Authentication-Results -> verdict "unknown"


def _fake_msg(uid: str, *, html: str = "<p>Hallo Welt</p>", text: str = "Hallo Welt"):
    return SimpleNamespace(
        uid=uid,
        subject=f"Betreff {uid}",
        from_="Absender <von@example.com>",
        to=("ich@example.com",),
        date_str="Sat, 30 Aug 2026 10:00:00 +0200",
        date=None,
        flags=(),
        text=text,
        html=html,
        attachments=[],
        headers={"message-id": (f"<{uid}@example.com>",), "in-reply-to": (), "references": ()},
        obj=_FakeObj(f"<{uid}@example.com>"),
    )


class _FakeBox:
    def __init__(self, messages):
        self._msgs = messages
        self.folder = SimpleNamespace(
            status=lambda *_a, **_k: {"UIDVALIDITY": 1, "MESSAGES": len(messages), "UNSEEN": 0}
        )

    def uids(self, *_a, **_k):
        return [m.uid for m in self._msgs]

    def fetch(self, *_a, **kwargs):
        # headers_only wird für den Flag-Abgleich genutzt; hier egal, wir liefern alles.
        return list(self._msgs)


def _patch_mailbox(monkeypatch, messages):
    @contextmanager
    def fake_mailbox(*_a, **_k):
        yield _FakeBox(messages)

    monkeypatch.setattr(cache_mod, "_mailbox", fake_mailbox)


def _account(session: Session) -> MailAccount:
    acc = MailAccount(user_id=1, email="test@example.com", secret_enc="x", label="Test")
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


def test_sync_speichert_volltext_mit(monkeypatch):
    """Nach dem Sync liegt der Body im Cache — /prefetch hat nichts mehr zu tun."""
    _patch_mailbox(monkeypatch, [_fake_msg("101"), _fake_msg("102")])
    with Session(engine) as s:
        acc = _account(s)
        res = cache_mod.sync_folder(s, acc, "pw", "INBOX")
        assert res["new"] == 2

        rows = s.exec(
            select(CachedMessage).where(CachedMessage.account_id == acc.id)
        ).all()
        assert len(rows) == 2
        assert all(r.detail_json for r in rows), "Volltext wurde nicht mitgespeichert"

        # Der entscheidende Punkt: das Vorwärmen findet nichts mehr zu holen,
        # also entfällt die zweite IMAP-Runde über dieselben Mails komplett.
        offen = cache_mod.uncached_detail_uids(s, acc.id, "INBOX", ["101", "102"])
        assert offen == []

        # Und der Body ist ohne IMAP lesbar.
        detail = cache_mod.read_detail(s, acc.id, "INBOX", "101")
        assert detail is not None
        assert detail["html"] == "<p>Hallo Welt</p>"


def test_riesige_mail_wird_nicht_gecacht(monkeypatch):
    """Ausreißer (bildlastige Newsletter) bleiben draußen, damit die DB nicht wächst."""
    riesig = "<p>" + ("x" * (cache_mod._DETAIL_CACHE_MAX + 1000)) + "</p>"
    _patch_mailbox(monkeypatch, [_fake_msg("201", html=riesig)])
    with Session(engine) as s:
        acc = _account(s)
        cache_mod.sync_folder(s, acc, "pw", "INBOX")
        row = s.exec(
            select(CachedMessage).where(CachedMessage.account_id == acc.id)
        ).first()
        assert row is not None
        assert row.detail_json == "", "zu große Mail haette nicht gecacht werden duerfen"
        # Kopfzeile ist trotzdem da — nur der Body wird beim Öffnen live geholt.
        assert row.subject == "Betreff 201"
        assert cache_mod.uncached_detail_uids(s, acc.id, "INBOX", ["201"]) == ["201"]


def test_store_bodies_abschaltbar(monkeypatch):
    """Mit store_bodies=False bleibt es beim reinen Kopfzeilen-Cache."""
    _patch_mailbox(monkeypatch, [_fake_msg("301")])
    with Session(engine) as s:
        acc = _account(s)
        cache_mod.sync_folder(s, acc, "pw", "INBOX", store_bodies=False)
        row = s.exec(
            select(CachedMessage).where(CachedMessage.account_id == acc.id)
        ).first()
        assert row is not None and row.detail_json == ""
