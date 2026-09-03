"""Die Ordnerliste: mehrere LIST-Varianten, aber nicht mehr nacheinander.

Am 03.09.2026 gemessen, nachdem die Zaehler schon parallel liefen: die reine
Ordnerliste eines Gmail-Kontos brauchte 5,9 s der 11,6 s einer kompletten
Zaehler-Abfrage. Drei LIST-Varianten liefen stur hintereinander, bei rund zwei
Sekunden je IMAP-Kommando auf diesem Konto.

Zwei Dinge duerfen dabei nie kaputtgehen:
  - Die zweite Variante (LIST "INBOX" "*") existiert, weil web.de/Courier
    INBOX-Unterordner beim einfachen LIST nicht zeigen. Sie darf nicht
    entfallen, nur weil sie jetzt woanders laeuft.
  - Die erste Variante bestimmt die Flags. Sonst kippt die
    SPECIAL-USE-Erkennung (Papierkorb, Spam, Entwuerfe).
"""
import threading
from contextlib import contextmanager

from app.mail import imap as imap_mod
from app.models import MailAccount


class _FakeFolder:
    def __init__(self, box, antworten):
        self._box = box
        self._antworten = antworten

    def list(self, ref="", muster="*", subscribed_only=False):
        variante = "abo" if subscribed_only else ("inbox" if ref == "INBOX" else "root")
        self._box.varianten.append(variante)
        eintraege = self._antworten.get(variante, [])
        if eintraege == "fehler":
            raise RuntimeError("LIST nicht unterstuetzt")
        return [type("F", (), {"name": n, "flags": f})() for n, f in eintraege]


class _FakeBox:
    def __init__(self, antworten):
        self.varianten = []
        self.folder = _FakeFolder(self, antworten)


def _konto():
    return MailAccount(id=9, email="sven@example.org", imap_host="imap.example.org")


def _stelle(monkeypatch, antworten, *, helfer_bekommt_verbindung=True):
    """_mailbox faelschen. Gibt (aufrufer_box, alle_boxen) zurueck."""
    boxen = []
    sperre = threading.Lock()

    @contextmanager
    def fake_mailbox(account, password, folder="INBOX", *, op="?", **_kw):
        if not helfer_bekommt_verbindung and "helfer" in op:
            raise imap_mod.ImapBusyError()
        box = _FakeBox(antworten)
        with sperre:
            boxen.append(box)
        yield box

    monkeypatch.setattr(imap_mod, "_mailbox", fake_mailbox)
    return boxen


ROOT = [("INBOX", (r"\HasNoChildren",)), ("Archiv", ())]
UNTER = [("INBOX", ("egal",)), ("INBOX.Projekte", (r"\HasNoChildren",))]


def test_beide_varianten_werden_zusammengefuehrt(monkeypatch):
    """Der Grund fuer Variante 2: web.de zeigt INBOX-Unterordner sonst nicht."""
    _stelle(monkeypatch, {"root": ROOT, "inbox": UNTER})
    namen = imap_mod.list_folders(_konto(), "geheim")
    assert namen == ["INBOX", "Archiv", "INBOX.Projekte"]


def test_flags_der_ersten_variante_gewinnen(monkeypatch):
    """Sonst kippt die SPECIAL-USE-Erkennung (Papierkorb, Spam, Entwuerfe)."""
    boxen = _stelle(monkeypatch, {"root": ROOT, "inbox": UNTER})
    with imap_mod._mailbox(_konto(), "geheim") as box:
        flags = imap_mod._liste_ordner(_konto(), "geheim", box)
    assert flags["INBOX"] == (r"\HasNoChildren",), "Variante 2 hat die Flags ueberschrieben"
    assert flags["INBOX.Projekte"] == (r"\HasNoChildren",)
    assert len(boxen) == 2, "Variante 2 lief nicht auf eigener Verbindung"


def test_dritte_variante_bleibt_aus_wenn_ordner_gefunden(monkeypatch):
    """Die eigentliche Ersparnis: eine ganze IMAP-Runde weniger, jedes Mal."""
    boxen = _stelle(monkeypatch, {"root": ROOT, "inbox": UNTER})
    imap_mod.list_folders(_konto(), "geheim")
    alle = [v for b in boxen for v in b.varianten]
    assert "abo" not in alle, f"abonnierte Variante lief umsonst mit: {alle}"
    assert sorted(alle) == ["inbox", "root"]


def test_dritte_variante_greift_wenn_beide_leer_bleiben(monkeypatch):
    """Notnagel fuer Server, die auf beide Varianten nichts liefern."""
    boxen = _stelle(monkeypatch, {
        "root": "fehler", "inbox": [], "abo": [("INBOX", ()), ("Abo-Ordner", ())],
    })
    namen = imap_mod.list_folders(_konto(), "geheim")
    assert namen == ["INBOX", "Abo-Ordner"]
    assert "abo" in [v for b in boxen for v in b.varianten]


def test_ohne_freie_verbindung_holt_der_aufrufer_variante_zwei_selbst(monkeypatch):
    """Ein Helfer ohne Verbindung darf nie zu fehlenden Ordnern fuehren."""
    boxen = _stelle(monkeypatch, {"root": ROOT, "inbox": UNTER},
                    helfer_bekommt_verbindung=False)
    namen = imap_mod.list_folders(_konto(), "geheim")
    assert namen == ["INBOX", "Archiv", "INBOX.Projekte"], "INBOX-Unterordner fehlen"
    assert len(boxen) == 1, "ein Helfer hat wider Erwarten eine Verbindung bekommen"
    assert boxen[0].varianten == ["root", "inbox"]


def test_gescheiterte_variante_wird_nicht_wiederholt(monkeypatch):
    """Ein LIST, das der Server ablehnt, darf nicht ein zweites Mal laufen."""
    boxen = _stelle(monkeypatch, {"root": ROOT, "inbox": "fehler"})
    namen = imap_mod.list_folders(_konto(), "geheim")
    assert namen == ["INBOX", "Archiv"]
    assert [v for b in boxen for v in b.varianten].count("inbox") == 1


def test_leere_liste_faellt_auf_inbox_zurueck(monkeypatch):
    _stelle(monkeypatch, {"root": [], "inbox": [], "abo": []})
    assert imap_mod.list_folders(_konto(), "geheim") == ["INBOX"]
