"""Wiederverwendete Pool-Verbindungen kosten keine Rundreise mehr.

Bis 1.85.0 schickte _ensure_box bei JEDER Wiederverwendung ein NOOP
("lebst du noch?"). Am 03.09.2026 gemessen: eine komplette IMAP-Rundreise,
~170 ms auf einem normalen Konto und ~2,8 s auf einem langsamen Gmail-Konto -
bei jeder einzelnen Operation. Die Pruefung garantierte dabei nichts: die
Verbindung kann zwischen NOOP und echtem Kommando genauso sterben.

Was schuetzt, ist _IDLE_TTL (240 s) plus der eine Fall, der GRATIS abfangbar
ist: das SELECT laeuft hier sowieso und noch vor dem yield in _mailbox, also
kann es einmal neu aufbauen, ohne dass der Aufrufer es merkt.
"""
import time

import pytest

from app.mail import imap as imap_mod
from app.models import MailAccount


class _Client:
    def noop(self):
        raise AssertionError(
            "NOOP ist zurueck - jede Operation kostet damit wieder eine ganze "
            "IMAP-Rundreise (auf dem langsamen Konto ~2,8 s)"
        )


class _Box:
    def __init__(self, nr):
        self.nr = nr
        self.client = _Client()
        self.geschlossen = False


def _konto():
    return MailAccount(id=9, email="sven@example.org", imap_host="imap.example.org")


@pytest.fixture
def stelle(monkeypatch):
    """Baut _connect/_close/_select nach und protokolliert, was passiert."""
    log = {"connect": 0, "close": [], "select": []}
    zaehler = {"n": 0}

    def fake_connect(account, login, password, folder):
        log["connect"] += 1
        zaehler["n"] += 1
        return _Box(zaehler["n"])

    def fake_close(box):
        if box is not None:
            log["close"].append(box.nr)

    def fake_select(box, folder):
        log["select"].append((box.nr, folder))

    monkeypatch.setattr(imap_mod, "_connect", fake_connect)
    monkeypatch.setattr(imap_mod, "_close", fake_close)
    monkeypatch.setattr(imap_mod, "_select", fake_select)
    return log


def _conn(box=None, *, folder=None, alter=0.0):
    c = imap_mod._Conn()
    c.box = box
    c.folder = folder
    c.last_used = time.monotonic() - alter
    return c


def test_frische_verbindung_wird_aufgebaut(stelle):
    c = _conn()
    box = imap_mod._ensure_box(c, _konto(), "sven", "geheim", "INBOX")
    assert stelle["connect"] == 1
    assert c.box is box and c.folder == "INBOX"
    assert stelle["select"] == [], "nach dem Aufbau ist der Ordner schon gewaehlt"


def test_passende_verbindung_schickt_gar_kein_kommando(stelle):
    """Der Kern: derselbe Ordner, kuerzlich benutzt -> null Rundreisen.

    Ein NOOP wuerde hier durch _Client.noop() auffallen.
    """
    alt = _Box(1)
    c = _conn(alt, folder="INBOX", alter=5.0)
    box = imap_mod._ensure_box(c, _konto(), "sven", "geheim", "INBOX")
    assert box is alt
    assert stelle == {"connect": 0, "close": [], "select": []}


def test_anderer_ordner_kostet_nur_das_select(stelle):
    alt = _Box(1)
    c = _conn(alt, folder="INBOX", alter=5.0)
    box = imap_mod._ensure_box(c, _konto(), "sven", "geheim", "Archiv")
    assert box is alt
    assert stelle["select"] == [(1, "Archiv")]
    assert stelle["connect"] == 0
    assert c.folder == "Archiv"


def test_zu_lange_ungenutzt_wird_neu_aufgebaut(stelle):
    """_IDLE_TTL ist der eigentliche Schutz - der bleibt."""
    alt = _Box(1)
    c = _conn(alt, folder="INBOX", alter=imap_mod._IDLE_TTL + 1)
    box = imap_mod._ensure_box(c, _konto(), "sven", "geheim", "INBOX")
    assert box is not alt
    assert stelle["close"] == [1] and stelle["connect"] == 1


def test_gestorbene_verbindung_faellt_beim_select_auf_und_wird_ersetzt(monkeypatch, stelle):
    """Der gratis abfangbare Fall: SELECT scheitert -> einmal neu, Aufrufer merkt nichts."""
    versuche = []

    def select_stirbt_einmal(box, folder):
        versuche.append(box.nr)
        if len(versuche) == 1:
            raise OSError("connection reset by peer")
        stelle["select"].append((box.nr, folder))

    monkeypatch.setattr(imap_mod, "_select", select_stirbt_einmal)

    alt = _Box(1)
    c = _conn(alt, folder="INBOX", alter=5.0)
    box = imap_mod._ensure_box(c, _konto(), "sven", "geheim", "Archiv")

    assert box is not alt, "die tote Verbindung wurde weiterbenutzt"
    assert stelle["close"] == [1] and stelle["connect"] == 1
    assert c.box is box and c.folder == "Archiv"


def test_wird_nicht_endlos_wiederholt(monkeypatch, stelle):
    """Ein Konto, das dauerhaft nicht selektieren kann, darf keine Schleife drehen."""
    def connect_ohne_select(account, login, password, folder):
        stelle["connect"] += 1
        if stelle["connect"] > 3:
            raise AssertionError("Endlosschleife beim Neuaufbau")
        raise OSError("Login klappt nicht")

    monkeypatch.setattr(imap_mod, "_connect", connect_ohne_select)
    monkeypatch.setattr(
        imap_mod, "_select",
        lambda box, folder: (_ for _ in ()).throw(OSError("tot")),
    )

    c = _conn(_Box(1), folder="INBOX", alter=5.0)
    with pytest.raises(OSError):
        imap_mod._ensure_box(c, _konto(), "sven", "geheim", "Archiv")
    assert stelle["connect"] == 1, "es wurde mehr als einmal neu aufgebaut"
