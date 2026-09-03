"""Ordnerzaehler laufen parallel - und bleiben trotzdem vollstaendig.

Am 03.09.2026 gemessen: die Live-Zaehler eines Gmail-Kontos brauchten 18,7 s,
laenger als der komplette Sync desselben Kontos (8,0 s). Ursache war kein
Warten mehr - der Verbindungs-Pool war da schon umgebaut -, sondern schlichte
Reihenfolge: je Ordner ein STATUS, nacheinander, auf einer Verbindung.

Der Test prueft beides, was dabei schiefgehen kann:
  1. es wird wirklich verteilt (sonst ist der Umbau wirkungslos),
  2. die Zaehler bleiben vollstaendig und richtig zugeordnet, auch wenn kein
     Helfer eine Verbindung bekommt - ein falsches Badge waere schlimmer als
     ein langsames.
"""
import threading
import time
from contextlib import contextmanager

from app.mail import imap as imap_mod
from app.models import MailAccount


class _FakeFolder:
    """Minimaler Ersatz fuer box.folder: LIST + STATUS, mit Kunst-Verzoegerung."""

    def __init__(self, box, namen, dauer):
        self._box = box
        self._namen = namen
        self._dauer = dauer

    def list(self, *_a, **_kw):
        return [type("F", (), {"name": n, "flags": ()})() for n in self._namen]

    def status(self, name, _keys):
        self._box.aufrufe.append(name)
        time.sleep(self._dauer)
        # Eindeutige Zahlen je Ordner: so faellt eine Verwechslung auf.
        nr = self._namen.index(name) + 1
        return {"MESSAGES": nr * 10, "UNSEEN": nr}


class _FakeBox:
    def __init__(self, namen, dauer):
        self.aufrufe = []
        self.folder = _FakeFolder(self, namen, dauer)


NAMEN = ["INBOX", "Archiv", "Entwuerfe", "Gesendet", "Papierkorb", "Spam"]


def _konto():
    return MailAccount(id=9, email="sven@example.org", imap_host="imap.example.org")


def _stelle(monkeypatch, *, helfer_bekommen_verbindung, dauer=0.05):
    """_mailbox faelschen: der Aufrufer bekommt immer eine Box, Helfer optional."""
    boxen = []
    zaehler = {"n": 0}
    sperre = threading.Lock()

    @contextmanager
    def fake_mailbox(account, password, folder="INBOX", *, op="?", **_kw):
        with sperre:
            zaehler["n"] += 1
            erster = zaehler["n"] == 1
        if not erster and not helfer_bekommen_verbindung:
            raise imap_mod.ImapBusyError()
        box = _FakeBox(NAMEN, dauer)
        boxen.append(box)
        yield box

    monkeypatch.setattr(imap_mod, "_mailbox", fake_mailbox)
    return boxen


def test_zaehler_sind_vollstaendig_und_richtig_zugeordnet(monkeypatch):
    boxen = _stelle(monkeypatch, helfer_bekommen_verbindung=True)
    out = imap_mod.folder_counts(_konto(), "geheim")

    assert [e["name"] for e in out] == NAMEN, "Reihenfolge muss stabil bleiben"
    for nr, e in enumerate(out, 1):
        assert e["unseen"] == nr, f"{e['name']}: Zaehler eines anderen Ordners"
        assert e["total"] == nr * 10

    # Jeder Ordner genau EINMAL abgefragt - nicht doppelt (Verschwendung),
    # nicht gar nicht (fehlendes Badge).
    alle = [n for b in boxen for n in b.aufrufe]
    assert sorted(alle) == sorted(NAMEN)


def test_arbeit_wird_wirklich_verteilt(monkeypatch):
    boxen = _stelle(monkeypatch, helfer_bekommen_verbindung=True)
    imap_mod.folder_counts(_konto(), "geheim")

    # Nicht die Zahl der Boxen pruefen: seit die LIST-Varianten ebenfalls
    # parallel laufen, gehoert eine davon dem Ordnerlisten-Helfer. Es zaehlt,
    # wie sich die STATUS-Aufrufe verteilen.
    beteiligt = [b for b in boxen if b.aufrufe]
    assert len(beteiligt) >= 2, (
        "alle STATUS liefen auf einer Verbindung - der Umbau ist wirkungslos"
    )
    groesste = max(len(b.aufrufe) for b in beteiligt)
    assert groesste < len(NAMEN), "eine Verbindung hat trotzdem alles allein gemacht"


def test_ohne_freie_verbindung_zaehlt_der_aufrufer_alles_selbst(monkeypatch):
    """Der wichtigere Fall: Helfer sind Zugabe, kein Muss."""
    boxen = _stelle(monkeypatch, helfer_bekommen_verbindung=False)
    out = imap_mod.folder_counts(_konto(), "geheim")

    assert len(boxen) == 1, "ein Helfer hat wider Erwarten eine Box bekommen"
    assert sorted(boxen[0].aufrufe) == sorted(NAMEN)
    assert [e["unseen"] for e in out] == [1, 2, 3, 4, 5, 6]


def test_kein_status_fuer_virtuelle_gmail_ordner(monkeypatch):
    """Gegenprobe: der teure STATUS bleibt fuer Label-Sichten aus."""
    namen = ["INBOX", "[Gmail]/Alle Nachrichten", "[Gmail]/Markiert"]
    monkeypatch.setattr(
        imap_mod, "_folder_special",
        lambda name, flags: {"INBOX": "inbox",
                             "[Gmail]/Alle Nachrichten": "all",
                             "[Gmail]/Markiert": "flagged"}[name],
    )
    boxen = []

    @contextmanager
    def fake_mailbox(account, password, folder="INBOX", *, op="?", **_kw):
        box = _FakeBox(namen, 0.0)
        boxen.append(box)
        yield box

    monkeypatch.setattr(imap_mod, "_mailbox", fake_mailbox)
    out = imap_mod.folder_counts(_konto(), "geheim")

    alle = [n for b in boxen for n in b.aufrufe]
    assert alle == ["INBOX"], f"teurer STATUS fuer virtuelle Ordner: {alle}"
    assert [e["total"] for e in out] == [10, 0, 0]
    # Sie bleiben trotzdem gelistet - nur ohne Badge.
    assert [e["name"] for e in out] == namen


def test_parallel_ist_messbar_schneller(monkeypatch):
    """Die eigentliche Zusage: aus 18,7 s wird rund ein Drittel."""
    _stelle(monkeypatch, helfer_bekommen_verbindung=False, dauer=0.05)
    t0 = time.monotonic()
    imap_mod.folder_counts(_konto(), "geheim")
    seriell = time.monotonic() - t0

    _stelle(monkeypatch, helfer_bekommen_verbindung=True, dauer=0.05)
    t0 = time.monotonic()
    imap_mod.folder_counts(_konto(), "geheim")
    parallel = time.monotonic() - t0

    assert parallel < seriell * 0.75, (
        f"parallel {parallel:.2f}s vs. seriell {seriell:.2f}s - keine Ersparnis"
    )
