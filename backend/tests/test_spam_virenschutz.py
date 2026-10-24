"""Spam- & Virenschutz (1.97.3): Schalter, Sperrliste, Abmelden, ClamAV, rspamd.

Alles ist pro Nutzer abschaltbar und standardmaessig AUS - jeder Test, der einen
Schalter einschaltet, schaltet ihn ueber die Fixture wieder aus.
"""
from __future__ import annotations

import socket
import struct
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from app.api import mail as mail_api
from app.core.config import get_settings
from app.core.db import engine
from app.dav import client as dav_client
from app.dav.client import DavUrlError
from app.mail import imap as imap_mod
from app.mail import scan as scan_mod
from app.mail import scheduler
from app.mail import security as security_mod
from app.models import AttachmentScan, MailAccount, MailRule

FLAGS = ("sec_block_all_accounts", "sec_unsubscribe", "sec_virus_scan", "sec_spam_filter")
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


@pytest.fixture()
def schalter(client, admin):
    """Schalter setzen; am Ende alles wieder AUS."""
    def setzen(**werte):
        r = client.put("/api/v1/settings/ui", headers=admin, json=werte)
        assert r.status_code == 200, r.text
        return r.json()
    yield setzen
    client.put("/api/v1/settings/ui", headers=admin, json={k: False for k in FLAGS})


def _admin_id() -> int:
    with Session(engine) as s:
        return s.exec(text("SELECT user_id FROM mailaccount ORDER BY id LIMIT 1")).scalar_one()


def _zweites_konto(client, admin) -> int:
    r = client.post("/api/v1/accounts", headers=admin, json={
        "label": "Zweit", "email": "zweit@example.com", "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com", "password": "pw",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _konto(account_id: int) -> MailAccount:
    with Session(engine) as s:
        acc = s.get(MailAccount, account_id)
        s.expunge(acc)
        return acc


# --- Schalter ----------------------------------------------------------------------

def test_schalter_sind_standardmaessig_aus_und_umschaltbar(client, admin, schalter):
    s = client.get("/api/v1/settings/ui", headers=admin).json()
    assert all(s[k] is False for k in FLAGS)
    assert schalter(sec_virus_scan=True)["sec_virus_scan"] is True
    assert schalter(sec_virus_scan=False)["sec_virus_scan"] is False
    assert client.put("/api/v1/settings/ui", headers=admin, json={"sec_virus_scan": "ja"}).status_code == 422


def test_sicherheitsstatus_ohne_dienste(client, admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "clamav", "")
    monkeypatch.setattr(get_settings(), "rspamd_url", "")
    s = client.get("/api/v1/security/status", headers=admin).json()
    assert s["clamav"] == {"configured": False, "reachable": False, "version": ""}
    assert s["rspamd"]["configured"] is False


# --- Sperrliste --------------------------------------------------------------------

def test_sperren_fuer_alle_konten(client, admin, account, schalter):
    zweit = _zweites_konto(client, admin)
    r = client.post(f"/api/v1/mail/{zweit}/block-sender", headers=admin,
                    json={"sender": "spam@werbung.example", "delete_existing": False})
    assert r.status_code == 200, r.text
    acc = _konto(account)
    eigene = [MailRule(id=None, account_id=account, field="from", value="freund@example.com", delete_msg=True)]
    assert security_mod.global_block_rules(acc, eigene) == []          # Schalter aus
    schalter(sec_block_all_accounts=True)
    fremd = security_mod.global_block_rules(acc, eigene)
    # (Andere Tests legen ebenfalls Sperren an - hier zaehlt nur die eigene.)
    assert ("from", "spam@werbung.example") in [(r.field, r.value) for r in fremd]
    assert all(r.delete_msg and r.account_id != account for r in fremd)
    # Gleiche Bedingung schon im eigenen Konto -> nicht doppelt.
    eigene.append(MailRule(account_id=account, field="from", value="SPAM@werbung.example", delete_msg=True))
    assert ("from", "spam@werbung.example") not in [(r.field, r.value) for r in security_mod.global_block_rules(acc, eigene)]


def test_neue_merkmale_reply_to_und_list_id():
    msg = SimpleNamespace(uid="1", from_="a@x.de", from_values=None, to=(), subject="",
                          reply_to=("antwort@spam.example",), headers={"list-id": ("Angebote <news.spam.example>",)})
    regeln = [SimpleNamespace(enabled=True, field="list_id", value="news.spam.example"),
              SimpleNamespace(enabled=True, field="reply_to", value="antwort@spam")]
    assert imap_mod._first_match(msg, regeln[:1]) is regeln[0]
    assert imap_mod._first_match(msg, regeln[1:]) is regeln[1]
    assert imap_mod._first_match(SimpleNamespace(uid="2", headers={}, reply_to=()), regeln) is None


def test_regel_mit_neuem_merkmal_anlegen(client, admin, account):
    r = client.post(f"/api/v1/mail/{account}/rules", headers=admin, json={"field": "list_id", "value": "news.x", "delete_msg": True})
    assert r.status_code == 201, r.text
    assert r.json()["hits"] == 0 and r.json()["last_hit_at"] is None


def test_treffer_werden_gezaehlt(client, admin, account, monkeypatch):
    regel = client.post(f"/api/v1/mail/{account}/rules", headers=admin,
                        json={"field": "subject", "value": "werbung", "delete_msg": True}).json()

    class _Box:
        def fetch(self, *a, **k):
            return [SimpleNamespace(uid=u, subject="Werbung!", from_="a@x.de", from_values=None, to=(), headers={}, reply_to=())
                    for u in ("5", "6")]

        def flag(self, *a, **k):
            pass

        def move(self, *a, **k):
            pass

    monkeypatch.setattr(imap_mod, "_mailbox", lambda *a, **kw: nullcontext(_Box()))
    monkeypatch.setattr(imap_mod, "_soft_delete", lambda *a, **k: None)
    r = client.post(f"/api/v1/mail/{account}/rules/apply", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["hits"] == {str(regel["id"]): 2} or r.json()["hits"] == {regel["id"]: 2}
    gespeichert = next(x for x in client.get(f"/api/v1/mail/{account}/rules", headers=admin).json() if x["id"] == regel["id"])
    assert gespeichert["hits"] == 2 and gespeichert["last_hit_at"]


# --- Ein-Klick-Abmeldung -------------------------------------------------------------

DETAIL_NEWSLETTER = {
    "uid": "9", "folder": "INBOX", "message_id": "<n1@shop.example>",
    "list_unsubscribe": "<mailto:off@shop.example>, <https://shop.example/u/abc123>",
    "list_unsubscribe_post": "List-Unsubscribe=One-Click",
    "auth": {"verdict": "pass"},
}


def test_abmelde_adresse_nur_mit_rfc8058():
    assert security_mod.unsubscribe_url(DETAIL_NEWSLETTER) == "https://shop.example/u/abc123"
    ohne_post = {**DETAIL_NEWSLETTER, "list_unsubscribe_post": ""}
    assert security_mod.unsubscribe_url(ohne_post) is None                       # nur Webseite, kein Ein-Klick
    nur_http = {**DETAIL_NEWSLETTER, "list_unsubscribe": "<http://shop.example/u>"}
    assert security_mod.unsubscribe_url(nur_http) is None                        # nie unverschluesselt
    info = security_mod.unsubscribe_info(DETAIL_NEWSLETTER)
    assert info == {"available": True, "verified": True, "host": "shop.example"}
    assert security_mod.unsubscribe_info({**DETAIL_NEWSLETTER, "auth": {"verdict": "unknown"}})["verified"] is False


def test_abmelden_nur_mit_schalter(client, admin, account, schalter, monkeypatch):
    monkeypatch.setattr(mail_api, "_message_detail", lambda *a, **k: dict(DETAIL_NEWSLETTER))
    gesendet = []
    monkeypatch.setattr(dav_client, "post_pinned_form", lambda url, data, **kw: gesendet.append((url, data)) or httpx.Response(200))
    url = f"/api/v1/mail/{account}/messages/9/unsubscribe?folder=INBOX"
    assert client.post(url, headers=admin).status_code == 403                     # Schalter aus
    schalter(sec_unsubscribe=True)
    r = client.post(url, headers=admin)
    assert r.status_code == 200 and r.json() == {"ok": True, "status": 200}
    assert gesendet == [("https://shop.example/u/abc123", {"List-Unsubscribe": "One-Click"})]


def test_mail_ansicht_zeigt_abmelden_nur_mit_schalter(client, admin, account, schalter, monkeypatch):
    monkeypatch.setattr(mail_api, "_message_detail", lambda *a, **k: {**DETAIL_NEWSLETTER, "subject": "N", "from": "s@shop.example",
                                                                      "date": "", "seen": False, "flagged": False,
                                                                      "to": [], "text": "", "html": "", "attachments": []})
    url = f"/api/v1/mail/{account}/messages/9?folder=INBOX"
    assert client.get(url, headers=admin).json()["unsubscribe"] is None
    schalter(sec_unsubscribe=True)
    assert client.get(url, headers=admin).json()["unsubscribe"]["host"] == "shop.example"


def test_abmelden_nie_ins_heimnetz(monkeypatch):
    monkeypatch.setattr(dav_client, "_resolve_validated", lambda url, **k: ("192.168.1.20", "router.example", 443))
    with pytest.raises(DavUrlError):
        dav_client.post_pinned_form("https://router.example/u", {"List-Unsubscribe": "One-Click"})


# --- ClamAV ------------------------------------------------------------------------

class _FakeClamd:
    """Echter TCP-Server mit dem clamd-INSTREAM-Protokoll (EICAR = Virus)."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._lauf, daemon=True).start()

    def _lauf(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                cmd = b""
                while not cmd.endswith(b"\0"):
                    cmd += conn.recv(1)
                if cmd == b"zVERSION\0":
                    conn.sendall(b"ClamAV 1.5.4/1/test\0")
                    continue
                daten = b""
                while True:
                    laenge = struct.unpack("!L", self._genau(conn, 4))[0]
                    if laenge == 0:
                        break
                    daten += self._genau(conn, laenge)
                conn.sendall(b"stream: Eicar-Test-Signature FOUND\0" if b"EICAR" in daten else b"stream: OK\0")

    @staticmethod
    def _genau(conn, n):
        buf = b""
        while len(buf) < n:
            buf += conn.recv(n - len(buf))
        return buf


@pytest.fixture()
def clamd(monkeypatch):
    srv = _FakeClamd()
    monkeypatch.setattr(get_settings(), "clamav", f"127.0.0.1:{srv.port}")
    yield srv
    srv.sock.close()


def test_clamav_erkennt_eicar_und_sauberes(clamd):
    assert scan_mod.scan_bytes(EICAR) == ("infected", "Eicar-Test-Signature")
    assert scan_mod.scan_bytes(b"ganz normal" * 50000) == ("clean", "")      # mehrere 64-KiB-Pakete
    assert scan_mod.clamav_version() == "ClamAV 1.5.4/1/test"


def test_clamav_sagt_nie_sauber_ohne_pruefung(monkeypatch):
    monkeypatch.setattr(get_settings(), "clamav", "127.0.0.1:1")              # niemand hoert zu
    assert scan_mod.scan_bytes(b"x")[0] == "error"
    monkeypatch.setattr(get_settings(), "clamav", "")
    assert scan_mod.scan_bytes(b"x")[0] == "error"
    monkeypatch.setattr(get_settings(), "clamav", "127.0.0.1:1")
    monkeypatch.setattr(scan_mod, "_CLAM_MAX", 3)
    assert scan_mod.scan_bytes(b"zu gross")[0] == "skipped"


def _anhang(monkeypatch, daten: bytes):
    monkeypatch.setattr(imap_mod, "get_attachment", lambda *a, **k: ("rechnung.exe", "application/octet-stream", daten))


def test_download_mit_virenscan(client, admin, account, schalter, clamd, monkeypatch):
    url = f"/api/v1/mail/{account}/messages/7/attachments/0?folder=INBOX"
    _anhang(monkeypatch, EICAR)
    assert client.get(url, headers=admin).status_code == 200                   # Schalter aus: kein Scan
    schalter(sec_virus_scan=True)
    r = client.get(url, headers=admin)
    assert r.status_code == 403 and "Eicar-Test-Signature" in r.json()["detail"]
    _anhang(monkeypatch, b"harmlos")
    r = client.get(url, headers=admin)
    assert r.status_code == 200 and r.headers["x-selfmailer-scan"] == "clean"


def test_download_scanner_weg_nur_mit_bestaetigung(client, admin, account, schalter, monkeypatch):
    schalter(sec_virus_scan=True)
    monkeypatch.setattr(get_settings(), "clamav", "127.0.0.1:1")
    _anhang(monkeypatch, b"harmlos")
    url = f"/api/v1/mail/{account}/messages/7/attachments/0?folder=INBOX"
    assert client.get(url, headers=admin).status_code == 503
    r = client.get(url + "&ungeprueft=true", headers=admin)
    assert r.status_code == 200 and r.headers["x-selfmailer-scan"] == "unchecked"


def test_befunde_in_der_mail_ansicht(client, admin, account, schalter, monkeypatch):
    scan_mod.record(account, "<v1@x.de>", [(0, "a.pdf", "clean", ""), (1, "b.exe", "infected", "Eicar-Test-Signature")])
    monkeypatch.setattr(mail_api, "_message_detail", lambda *a, **k: {
        "uid": "3", "folder": "Spam", "message_id": "<v1@x.de>", "subject": "", "from": "", "date": "",
        "seen": False, "flagged": False, "to": [], "text": "", "html": "", "attachments": []})
    url = f"/api/v1/mail/{account}/messages/3?folder=Spam"
    assert client.get(url, headers=admin).json()["virus"] == []                 # Schalter aus
    schalter(sec_virus_scan=True)
    assert client.get(url, headers=admin).json()["virus"] == [
        {"index": 1, "filename": "b.exe", "status": "infected", "signature": "Eicar-Test-Signature"}]
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM attachmentscan WHERE mail_key = '<v1@x.de>'"))


# --- Pruefung beim Eintreffen -------------------------------------------------------

def _msg(uid, *, anhaenge=(), gelesen=False, mid=None):
    return SimpleNamespace(
        uid=uid, size_rfc822=1000, flags=(imap_mod.SEEN,) if gelesen else (),
        attachments=[SimpleNamespace(filename=n, payload=p) for n, p in anhaenge],
        obj=SimpleNamespace(get=lambda k, d="": (mid or f"<{uid}@x.de>") if k == "Message-ID" else d,
                            as_bytes=lambda: b"Subject: x\r\n\r\nhallo"),
    )


class _PruefBox:
    def __init__(self, mails):
        self.mails = mails
        self.verschoben = []

    def fetch(self, *a, **kw):
        return self.mails

    def move(self, uids, ziel):
        self.verschoben.append((list(uids), ziel))


def test_eintreffen_virus_und_spam_in_den_spam_ordner(account, clamd, monkeypatch):
    mails = [_msg("1", anhaenge=[("ok.pdf", b"sauber")]),
             _msg("2", anhaenge=[("boese.exe", EICAR)]),
             _msg("3", gelesen=True),
             _msg("4")]
    box = _PruefBox(mails)
    monkeypatch.setattr(imap_mod, "_mailbox", lambda *a, **kw: nullcontext(box))
    monkeypatch.setattr(imap_mod, "_spam_folder", lambda b: "Spam")
    monkeypatch.setattr(get_settings(), "rspamd_url", "http://rspamd.local:11333")
    spam_uids = {"3"}
    # Welche Mail gerade bewertet wird, ueber die Reihenfolge der Mails ermitteln.
    reihenfolge = iter(["1", "3", "4"])          # "2" wird wegen Virus nicht mehr bewertet
    monkeypatch.setattr(scan_mod, "rspamd_check", lambda raw: scan_mod.SpamUrteil(is_spam=next(reihenfolge) in spam_uids))
    erg = scan_mod.inspect_new(_konto(account), "pw", "INBOX", ["1", "2", "3", "4"], virus=True, spam=True)
    assert erg["moved"] == ["2", "3"] and erg["virus"] == 1 and erg["spam"] == 1
    assert erg["moved_unseen"] == 1                       # 2 ungelesen, 3 war gelesen
    assert box.verschoben == [(["2", "3"], "Spam")]
    with Session(engine) as s:
        befunde = s.exec(select(AttachmentScan).where(AttachmentScan.account_id == account)).all()
    assert {(b.mail_key, b.status) for b in befunde} >= {("<1@x.de>", "clean"), ("<2@x.de>", "infected")}
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM attachmentscan WHERE account_id = :a"), {"a": account})


def test_eintreffen_ohne_spam_ordner_bleibt_alles_liegen(account, clamd, monkeypatch):
    box = _PruefBox([_msg("2", anhaenge=[("boese.exe", EICAR)])])
    monkeypatch.setattr(imap_mod, "_mailbox", lambda *a, **kw: nullcontext(box))
    monkeypatch.setattr(imap_mod, "_spam_folder", lambda b: None)
    erg = scan_mod.inspect_new(_konto(account), "pw", "INBOX", ["2"], virus=True, spam=False)
    assert erg["moved"] == [] and box.verschoben == []
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM attachmentscan WHERE account_id = :a"), {"a": account})


def test_eintreffen_ohne_dienste_tut_nichts(account, monkeypatch):
    monkeypatch.setattr(get_settings(), "clamav", "")
    monkeypatch.setattr(get_settings(), "rspamd_url", "")
    monkeypatch.setattr(imap_mod, "_mailbox", lambda *a, **kw: pytest.fail("keine Verbindung noetig"))
    assert scan_mod.inspect_new(_konto(account), "pw", "INBOX", ["1"], virus=True, spam=True)["moved"] == []


def test_scheduler_prueft_vor_liste_und_push(account, schalter, monkeypatch):
    res = {"new": 3, "new_uids": ["7", "8", "9"], "unseen": 5}
    assert scheduler._neue_pruefen(_konto(account), "pw", res) is res             # Schalter aus
    schalter(sec_virus_scan=True)
    monkeypatch.setattr(scan_mod, "inspect_new", lambda *a, **k: {"moved": ["8"], "moved_unseen": 1, "virus": 1, "spam": 0})
    entfernt = []
    monkeypatch.setattr(scheduler.cache_mod, "remove_uids", lambda s, aid, folder, uids, **k: entfernt.append((folder, uids)))
    neu = scheduler._neue_pruefen(_konto(account), "pw", res)
    assert neu["new_uids"] == ["7", "9"] and neu["new"] == 2 and neu["unseen"] == 4
    assert entfernt == [("INBOX", ["8"])]


def test_scheduler_pruefung_ist_nie_fatal(account, schalter, monkeypatch):
    schalter(sec_spam_filter=True)

    def kaputt(*a, **k):
        raise TimeoutError("synthetisch")

    monkeypatch.setattr(scan_mod, "inspect_new", kaputt)
    res = {"new": 1, "new_uids": ["7"], "unseen": 1}
    assert scheduler._neue_pruefen(_konto(account), "pw", res) == res


# --- rspamd -------------------------------------------------------------------------

def test_rspamd_urteil(monkeypatch):
    monkeypatch.setattr(get_settings(), "rspamd_url", "http://rspamd.local:11333")
    antworten = {"add header": True, "rewrite subject": True, "reject": True, "no action": False, "greylist": False}
    for action, spam in antworten.items():
        monkeypatch.setattr(scan_mod.httpx, "post", lambda url, **k: httpx.Response(
            200, json={"action": action, "score": 9.5, "required_score": 15}, request=httpx.Request("POST", url)))
        u = scan_mod.rspamd_check(b"raw")
        assert u.is_spam is spam, action
    monkeypatch.setattr(scan_mod.httpx, "post", lambda url, **k: (_ for _ in ()).throw(httpx.ConnectError("weg")))
    assert scan_mod.rspamd_check(b"raw") is None                                  # nicht bewertet != kein Spam


def test_spam_knopf_lernt(account, monkeypatch):
    monkeypatch.setattr(get_settings(), "rspamd_controller_url", "http://rspamd.local:11334")
    monkeypatch.setattr(get_settings(), "rspamd_password", "geheim")
    gelernt = []
    monkeypatch.setattr(scan_mod, "rspamd_learn", lambda raw, spam: gelernt.append(spam) or True)
    box = SimpleNamespace(fetch=lambda *a, **k: [_msg("5")])
    monkeypatch.setattr(imap_mod, "_mailbox", lambda *a, **kw: nullcontext(box))
    monkeypatch.setattr(imap_mod, "_spam_folder", lambda b: "Spam")
    monkeypatch.setattr(imap_mod, "_trash_folder", lambda b, cur: "Trash")
    acc = _konto(account)
    assert scan_mod.learn_after_move(acc, "pw", ["<5@x.de>"], "INBOX", "Spam") == 1      # -> Spam
    assert scan_mod.learn_after_move(acc, "pw", ["<5@x.de>"], "Spam", "INBOX") == 1      # zurueck -> kein Spam
    assert scan_mod.learn_after_move(acc, "pw", ["<5@x.de>"], "Spam", "Trash") == 0      # Papierkorb: kein Signal
    assert scan_mod.learn_after_move(acc, "pw", ["<5@x.de>"], "INBOX", "Archiv") == 0
    assert scan_mod.learn_after_move(acc, "pw", ['<x"\r\nA1 LOGOUT@y>'], "INBOX", "Spam") == 0   # kaputte Message-ID
    assert gelernt == [True, False]
