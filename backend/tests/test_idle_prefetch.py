"""IMAP IDLE fuer den Posteingang + Vorladen neuer Mails (1.97.2).

Gemessen am 24.10.2026 (Gmail): neue Mail nach 136 s im Cache, erstes Oeffnen
3,9 s. IDLE meldet neue Post sofort, das Vorladen macht das Oeffnen sofortig.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from app.core.db import engine
from app.mail import cache as cache_mod
from app.mail import idle_watch
from app.mail import imap as imap_mod
from app.mail import scheduler
from app.models import CachedMessage, FolderNotify


# --- Hilfen -------------------------------------------------------------------

class _FakeIdle:
    def __init__(self, skript, waechter_ref):
        self.skript = list(skript)
        self.waechter_ref = waechter_ref
        self.starts = 0
        self.stops = 0

    def start(self):
        self.starts += 1

    def poll(self, timeout):
        if self.skript:
            schritt = self.skript.pop(0)
            if isinstance(schritt, BaseException):
                raise schritt
            return schritt
        # Skript zu Ende: Waechter beenden, wie ein ruhiges Postfach antworten.
        self.waechter_ref[0].stopp.set()
        time.sleep(0.01)
        return []

    def stop(self):
        self.stops += 1


class _FakeBox:
    def __init__(self, skript, waechter_ref, caps=("IMAP4REV1", "IDLE")):
        self.client = SimpleNamespace(capabilities=caps)
        self.idle = _FakeIdle(skript, waechter_ref)


@pytest.fixture()
def idle_umgebung(monkeypatch):
    """Waechter ohne echtes Netz: Konto-Laden und Verbindung sind Attrappen."""
    aufrufe: list[int] = []
    erreicht = threading.Event()

    def handler(account_id):
        aufrufe.append(account_id)
        erreicht.set()

    monkeypatch.setattr(idle_watch, "_konto_laden", lambda aid: (SimpleNamespace(id=aid), "pw"))
    monkeypatch.setattr(imap_mod, "close_idle_connection", lambda box: None)
    monkeypatch.setattr(idle_watch, "_PAUSE_MIN", 0.01)
    monkeypatch.setattr(idle_watch, "_handler", handler)
    monkeypatch.setattr(idle_watch, "_durchlauf", {})
    return SimpleNamespace(aufrufe=aufrufe, erreicht=erreicht)


def _waechter_laufen_lassen(monkeypatch, boxen: list[_FakeBox], ref: list) -> idle_watch._Waechter:
    geoeffnet = []

    def oeffnen(acc, pw):
        box = boxen[min(len(geoeffnet), len(boxen) - 1)]
        geoeffnet.append(box)
        return box

    monkeypatch.setattr(imap_mod, "open_idle_connection", oeffnen)
    w = idle_watch._Waechter(42, ("bindung",))
    ref.append(w)
    w.geoeffnet = geoeffnet
    w.start()
    w.join(timeout=5)
    assert not w.is_alive()
    return w


# --- Waechter -------------------------------------------------------------------

def test_neue_mail_loest_schnelldurchlauf_aus(idle_umgebung, monkeypatch):
    ref: list = []
    box = _FakeBox([[b"* 17 EXISTS", b"* 1 RECENT"]], ref)
    _waechter_laufen_lassen(monkeypatch, [box], ref)
    assert idle_umgebung.erreicht.wait(2)
    assert idle_umgebung.aufrufe == [42]
    assert box.idle.starts == 1


def test_flag_aenderung_loest_nichts_aus(idle_umgebung, monkeypatch):
    ref: list = []
    box = _FakeBox([[b"* 3 FETCH (FLAGS (\\Seen))"]], ref)
    _waechter_laufen_lassen(monkeypatch, [box], ref)
    time.sleep(0.05)
    assert idle_umgebung.aufrufe == []


def test_server_ohne_idle_bleibt_beim_abfragen(idle_umgebung, monkeypatch):
    ref: list = []
    box = _FakeBox([], ref, caps=("IMAP4REV1",))
    w = _waechter_laufen_lassen(monkeypatch, [box], ref)
    assert w.unsupported_seit is not None
    assert box.idle.starts == 0 and idle_umgebung.aufrufe == []


def test_tote_verbindung_wird_neu_aufgebaut_und_holt_nach(idle_umgebung, monkeypatch):
    ref: list = []
    tot = _FakeBox([[], [], []], ref)            # sofort "lesbar", aber leer = EOF
    neu = _FakeBox([], ref)
    w = _waechter_laufen_lassen(monkeypatch, [tot, neu], ref)
    assert w.geoeffnet == [tot, neu]
    # Nach dem Wiederverbinden einmal nachholen (es koennte Post gekommen sein).
    assert idle_umgebung.erreicht.wait(2)
    assert idle_umgebung.aufrufe == [42]


def test_bye_fuehrt_zu_neuer_verbindung(idle_umgebung, monkeypatch):
    ref: list = []
    alt = _FakeBox([[b"* BYE Autologout"]], ref)
    neu = _FakeBox([], ref)
    w = _waechter_laufen_lassen(monkeypatch, [alt, neu], ref)
    assert w.geoeffnet == [alt, neu]


def test_idle_wird_regelmaessig_erneuert(idle_umgebung, monkeypatch):
    monkeypatch.setattr(idle_watch, "_ERNEUERN", 0.001)
    ref: list = []
    box = _FakeBox([[b"* OK Still here"], [b"* OK Still here"]], ref)
    langsam = box.idle.poll

    def poll(timeout):
        time.sleep(0.005)
        return langsam(timeout)

    box.idle.poll = poll
    _waechter_laufen_lassen(monkeypatch, [box], ref)
    assert box.idle.starts >= 2 and box.idle.stops >= 1


# --- Zusammenfassen -------------------------------------------------------------

def test_meldungen_waehrend_eines_durchlaufs_ergeben_genau_einen_weiteren(monkeypatch):
    monkeypatch.setattr(idle_watch, "_durchlauf", {})
    frei = threading.Event()
    laeuft = threading.Event()
    fertig = threading.Event()
    aufrufe: list[int] = []

    def handler(aid):
        aufrufe.append(aid)
        if len(aufrufe) == 1:
            laeuft.set()
            frei.wait(2)
        else:
            fertig.set()

    monkeypatch.setattr(idle_watch, "_handler", handler)
    idle_watch.ausloesen(7)
    assert laeuft.wait(2)
    for _ in range(5):
        idle_watch.ausloesen(7)
    frei.set()
    assert fertig.wait(2)
    time.sleep(0.05)
    assert aufrufe == [7, 7]
    assert idle_watch._durchlauf[7] == [False, False]


# --- Konten abgleichen ------------------------------------------------------------

def _konto(aid, secret="s1"):
    return SimpleNamespace(id=aid, imap_host="imap.example.com", imap_port=993, imap_ssl=True,
                           auth_user="", email=f"u{aid}@example.com", secret_enc=secret,
                           imap_tls_fingerprint="")


def test_abgleichen_startet_stoppt_und_erneuert(monkeypatch):
    monkeypatch.setattr(idle_watch, "ENABLED", True)
    monkeypatch.setattr(idle_watch, "_handler", lambda aid: None)
    monkeypatch.setattr(idle_watch, "_waechter", {})
    gestartet: list[int] = []

    class _Attrappe:
        def __init__(self, acc):
            self.bindung = idle_watch._bindung(acc)
            self.stopp = threading.Event()
            self.unsupported_seit = None

        def is_alive(self):
            return not self.stopp.is_set()

    def starten(acc):
        gestartet.append(acc.id)
        idle_watch._waechter[acc.id] = _Attrappe(acc)

    monkeypatch.setattr(idle_watch, "_starten", starten)

    idle_watch.abgleichen([_konto(1), _konto(2)])
    assert sorted(gestartet) == [1, 2]
    alt_2 = idle_watch._waechter[2]

    idle_watch.abgleichen([_konto(1), _konto(2)])        # nichts geaendert
    assert sorted(gestartet) == [1, 2]

    idle_watch.abgleichen([_konto(2, secret="neu")])      # 1 weg, 2 neues Passwort
    assert idle_watch._waechter.keys() == {2}
    assert alt_2.stopp.is_set() and gestartet.count(2) == 2


def test_abgleichen_respektiert_server_ohne_idle(monkeypatch):
    monkeypatch.setattr(idle_watch, "ENABLED", True)
    monkeypatch.setattr(idle_watch, "_handler", lambda aid: None)
    acc = _konto(5)
    tot = SimpleNamespace(bindung=idle_watch._bindung(acc), stopp=threading.Event(),
                          unsupported_seit=time.monotonic(), is_alive=lambda: False)
    monkeypatch.setattr(idle_watch, "_waechter", {5: tot})
    gestartet: list[int] = []
    monkeypatch.setattr(idle_watch, "_starten", lambda a: gestartet.append(a.id))
    idle_watch.abgleichen([acc])
    assert gestartet == []                                # nicht jede Runde neu probieren
    tot.unsupported_seit = time.monotonic() - idle_watch._UNSUPPORTED_RETRY - 1
    idle_watch.abgleichen([acc])
    assert gestartet == [5]


def test_abgeschaltet_startet_nichts(monkeypatch):
    monkeypatch.setattr(idle_watch, "ENABLED", False)
    monkeypatch.setattr(idle_watch, "_waechter", {})
    monkeypatch.setattr(idle_watch, "_starten", lambda a: pytest.fail("darf nicht starten"))
    idle_watch.abgleichen([_konto(1)])


# --- Vorladen -------------------------------------------------------------------

def _zeilen(account_id: int, uids: list[str], mit_text: set[str] = frozenset()) -> None:
    with Session(engine) as s:
        for uid in uids:
            s.add(CachedMessage(account_id=account_id, folder="INBOX", uid=uid, subject=f"S{uid}",
                                detail_json='{"text": "alt"}' if uid in mit_text else ""))
        s.commit()


def _aufraeumen(account_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM cachedmessage WHERE account_id = :a"), {"a": account_id})
        conn.execute(text("DELETE FROM foldernotify WHERE account_id = :a"), {"a": account_id})


def _konto_obj(account_id: int):
    from app.models import MailAccount
    with Session(engine) as s:
        acc = s.get(MailAccount, account_id)
        s.expunge(acc)
        return acc


def test_vorladen_holt_nur_fehlende_in_einem_abruf(account, monkeypatch):
    try:
        _zeilen(account, ["11", "12", "13"], mit_text={"13"})
        gesehen = {}

        def fake_get(acc, pw, uids, folder="INBOX", **kw):
            gesehen.update(uids=list(uids), **kw)
            return [{"uid": u, "text": f"Volltext {u}", "html": "", "attachments": [], "uidvalidity": 0,
                     "folder": folder, "auth": None} for u in uids]

        monkeypatch.setattr(imap_mod, "get_messages", fake_get)
        with Session(engine) as s:
            n = cache_mod.prefetch_new(s, _konto_obj(account), "pw", "INBOX", ["11", "12", "13"])
        assert n == 2
        assert gesehen["uids"] == ["11", "12"]
        assert gesehen["limit"] == 2 and gesehen["max_size"] == cache_mod._PREFETCH_MAX_BYTES
        assert gesehen["op"] == "prefetch-neu"
        with Session(engine) as s:
            assert cache_mod.read_detail(s, account, "INBOX", "11")["text"] == "Volltext 11"
            assert cache_mod.read_detail(s, account, "INBOX", "13")["text"] == "alt"
    finally:
        _aufraeumen(account)


def test_vorladen_begrenzt_und_abschaltbar(account, monkeypatch):
    try:
        uids = [str(100 + i) for i in range(30)]
        _zeilen(account, uids)
        gesehen = {}
        monkeypatch.setattr(imap_mod, "get_messages", lambda acc, pw, u, folder="INBOX", **kw: gesehen.setdefault("u", list(u)) and [])
        with Session(engine) as s:
            cache_mod.prefetch_new(s, _konto_obj(account), "pw", "INBOX", uids)
        assert gesehen["u"] == uids[-cache_mod._PREFETCH_NEW_MAX:]   # die neuesten

        monkeypatch.setattr(cache_mod, "_PREFETCH_NEW_MAX", 0)
        monkeypatch.setattr(imap_mod, "get_messages", lambda *a, **k: pytest.fail("abgeschaltet"))
        with Session(engine) as s:
            assert cache_mod.prefetch_new(s, _konto_obj(account), "pw", "INBOX", uids) == 0
    finally:
        _aufraeumen(account)


def test_vorladen_fehler_ist_nie_fatal(account, monkeypatch):
    try:
        _zeilen(account, ["21"])

        def kaputt(*a, **k):
            raise imap_mod.ImapBusyError()

        monkeypatch.setattr(imap_mod, "get_messages", kaputt)
        with Session(engine) as s:
            assert cache_mod.prefetch_new(s, _konto_obj(account), "pw", "INBOX", ["21"]) == 0
    finally:
        _aufraeumen(account)


# --- Schnelldurchlauf + normale Runde ----------------------------------------------

def _push_basis(account_id: int, unseen: int) -> None:
    with Session(engine) as s:
        s.add(FolderNotify(account_id=account_id, folder="INBOX", last_unseen=unseen))
        s.commit()


def _last_unseen(account_id: int) -> int:
    with Session(engine) as s:
        return s.exec(select(FolderNotify.last_unseen).where(FolderNotify.account_id == account_id)).one()


def test_schnelldurchlauf_synct_pusht_und_laedt_vor(account, monkeypatch):
    try:
        _push_basis(account, 1)
        ablauf: list[str] = []
        monkeypatch.setattr(cache_mod, "sync_folder", lambda s, acc, pw, folder, **kw: (
            ablauf.append(f"sync:{folder}:{kw.get('op')}") or {"new": 1, "new_uids": ["7"], "unseen": 3}))
        monkeypatch.setattr(scheduler.push_mod, "push_new_mail",
                            lambda s, acc, folder, n: ablauf.append(f"push:{folder}:{n}"))
        monkeypatch.setattr(cache_mod, "prefetch_new",
                            lambda s, acc, pw, folder, uids: ablauf.append(f"vorladen:{uids}") or 1)
        scheduler.sync_inbox_now(account)
        assert ablauf == ["sync:INBOX:sync-idle", "push:INBOX:2", "vorladen:['7']"]
        assert _last_unseen(account) == 3
    finally:
        _aufraeumen(account)


def test_schnelldurchlauf_ignoriert_unbekanntes_konto(monkeypatch):
    monkeypatch.setattr(cache_mod, "sync_folder", lambda *a, **k: pytest.fail("kein Sync"))
    scheduler.sync_inbox_now(999999)


def test_normale_runde_laedt_vor_und_pusht_aus_den_zaehlern(account, monkeypatch):
    try:
        _push_basis(account, 0)
        ablauf: list[str] = []
        monkeypatch.setattr(cache_mod, "sync_folder", lambda s, acc, pw, folder, **kw: (
            ablauf.append(f"sync:{kw.get('op')}") or {"new": 2, "new_uids": ["8", "9"], "unseen": 2}))
        monkeypatch.setattr(cache_mod, "prefetch_new",
                            lambda s, acc, pw, folder, uids: ablauf.append(f"vorladen:{uids}") or 2)
        monkeypatch.setattr(imap_mod, "purge_spam", lambda *a, **k: None)
        monkeypatch.setattr(imap_mod, "purge_trash", lambda *a, **k: None)
        monkeypatch.setattr(scheduler.vacation_mod, "process_account", lambda *a, **k: None)
        monkeypatch.setattr(imap_mod, "folder_counts", lambda acc, pw: [{"name": "INBOX", "unseen": 2}])
        monkeypatch.setattr(cache_mod, "write_folder_counts", lambda *a, **k: None)
        monkeypatch.setattr(scheduler.push_mod, "push_new_mail",
                            lambda s, acc, folder, n: ablauf.append(f"push:{n}"))
        scheduler._sync_account(_konto_obj(account))
        assert ablauf == ["sync:sync-hintergrund", "vorladen:['8', '9']", "push:2"]
    finally:
        _aufraeumen(account)


def test_pool_status_zeigt_idle(client, admin):
    r = client.get("/api/v1/mail/pool-status", headers=admin)
    assert r.status_code == 200 and isinstance(r.json()["idle"], list)
