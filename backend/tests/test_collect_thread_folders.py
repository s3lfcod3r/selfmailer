"""C1 — Pool-Ordner-Desync in collect_thread.

Früher schaltete collect_thread per box.folder.set() nacheinander durch mehrere
Ordner DERSELBEN Pool-Verbindung. Danach landete die Verbindung im Pool mit einem
conn.folder, das nicht dem real selektierten Ordner entsprach — ein nachfolgender
Zugriff bekam sie als "passend" ohne SELECT und arbeitete still im falschen
Ordner (auch Löschen/Verschieben/Flags). Der Fix: je Ordner eine EIGENE kurze
_mailbox-Session, kein folder.set() mehr auf einer geteilten Verbindung.

Der Test hält beides fest:
  1. collect_thread ruft box.folder.set() NIE mehr auf (die Fake-Box würde sonst
     werfen).
  2. Jeder beteiligte Ordner wird über eine EIGENE _mailbox-Session geöffnet
     (folder=-Argument), nicht mehrere Ordner in einer.
"""
from __future__ import annotations

from contextlib import contextmanager

from app.mail import imap as imap_mod
from app.models import MailAccount


class _FakeFolder:
    def set(self, *_a, **_kw):  # noqa: D401
        raise AssertionError(
            "collect_thread darf den Ordner NICHT mehr auf einer geteilten "
            "Verbindung umschalten (box.folder.set) — genau das war der Bug"
        )


class _Msg:
    def __init__(self, uid, subject):
        self.uid = uid
        self.subject = subject
        self.from_ = "a@b.de"
        self.date_str = "Mon, 07 Sep 2026 10:00:00 +0000"
        self.flags = ()
        self.text = "hallo"
        self.html = ""
        self.attachments = ()


# Ordnerinhalte für den Test: Ausgangsordner "Projekte" enthält die Zielmail.
_INHALT = {
    "Projekte": [_Msg("5", "Re: Angebot"), _Msg("6", "Re: Angebot")],
    "Gesendet": [_Msg("40", "Angebot")],
    "INBOX": [_Msg("7", "Angebot")],
}


class _FakeBox:
    def __init__(self, folder):
        self.folder = _FakeFolder()
        self._folder_name = folder
        self._msgs = _INHALT.get(folder, [])

    def fetch(self, _crit=None, *, mark_seen=False, limit=None, bulk=False, **_kw):
        if limit == 1:  # Ziel-Fetch im Ausgangsordner
            return list(self._msgs[:1])
        return list(self._msgs)

    def uids(self, _crit=None):
        return [m.uid for m in self._msgs]


def _patch(monkeypatch):
    geoeffnet: list[str] = []

    @contextmanager
    def fake_mailbox(account, password, folder="INBOX", *, read_fallback=False,
                     lock_timeout=None, op="?"):
        geoeffnet.append(folder)
        yield _FakeBox(folder)

    monkeypatch.setattr(imap_mod, "_mailbox", fake_mailbox)
    monkeypatch.setattr(imap_mod, "_sent_folder", lambda box: "Gesendet")
    monkeypatch.setattr(imap_mod, "thread_headers",
                        lambda m: {"message_id": "", "in_reply_to": "", "references": ""})
    monkeypatch.setattr(imap_mod, "keywords_of", lambda m: [])
    monkeypatch.setattr(imap_mod, "_snippet", lambda t, h: "")
    return geoeffnet


def _konto():
    return MailAccount(id=9, email="sven@example.org", imap_host="imap.example.org")


def test_je_ordner_eine_eigene_session(monkeypatch):
    geoeffnet = _patch(monkeypatch)
    out = imap_mod.collect_thread(_konto(), "geheim", folder="Projekte", uid="5")

    # Genau drei Sessions, je Ordner eine, in dieser Reihenfolge.
    assert geoeffnet == ["Projekte", "Gesendet", "INBOX"], geoeffnet
    # Treffer aus allen drei Ordnern gesammelt.
    gefunden = {e["folder"] for e in out}
    assert gefunden == {"Projekte", "Gesendet", "INBOX"}, gefunden


def test_ausgangsordner_inbox_wird_nicht_doppelt_geoeffnet(monkeypatch):
    geoeffnet = _patch(monkeypatch)
    imap_mod.collect_thread(_konto(), "geheim", folder="INBOX", uid="7")

    # INBOX ist Ausgangs- UND Standardordner — darf trotzdem nur EINE Session sein.
    assert geoeffnet.count("INBOX") == 1, geoeffnet
    assert geoeffnet[0] == "INBOX"
    assert "Gesendet" in geoeffnet
