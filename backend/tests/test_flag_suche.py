"""Flags des GANZEN Ordners per SEARCH -- nicht nur der neuesten 120.

Am 03.09.2026 an web.de aufgefallen: der Server meldete 53 ungelesene Mails im
Posteingang, der Cache kannte 8. Der volle Flag-Abgleich holte Kopfzeilen der
neuesten _FLAG_WINDOW (120) Mails -- bei 964 Mails im Ordner wurden die Flags
alles Aelteren nach dem ersten Einlesen also nie wieder aufgefrischt.

Kopfzeilen fuer 964 Mails zu holen waere die falsche Loesung (rund acht
Fetch-Runden je Abgleich). `UID SEARCH UNSEEN` und `UID SEARCH FLAGGED` kosten
zwei winzige Antworten, unabhaengig von der Ordnergroesse.

Der gefaehrliche Teil daran ist die Fehlerbehandlung: eine leere oder
abgeschnittene SEARCH-Antwort ist nicht von "nichts ungelesen" zu
unterscheiden. Wuerde man sie uebernehmen, waere der ganze Ordner still auf
gelesen gesetzt -- schlimmer als der Fehler, den das hier behebt. Deshalb die
Gegenprobe gegen die STATUS-Zahl desselben Ordners.
"""
import inspect

from imap_tools import AND

from app.mail import cache as cache_mod
from app.mail import imap as imap_mod


class _Box:
    """Merkt sich die Suchkriterien und antwortet nach Skript."""

    def __init__(self, antworten):
        self.kriterien = []
        self._antworten = antworten

    def uids(self, criteria="ALL"):
        self.kriterien.append(str(criteria))
        if self._antworten == "fehler":
            raise RuntimeError("SEARCH nicht unterstuetzt")
        return self._antworten[str(criteria)]


def test_liefert_ungelesene_und_markierte():
    box = _Box({str(AND(seen=False)): ["7", "9"], str(AND(flagged=True)): ["9"]})
    ungelesen, markiert = imap_mod.flag_mengen(box)
    assert ungelesen == {"7", "9"}
    assert markiert == {"9"}


def test_zwei_kommandos_unabhaengig_von_der_ordnergroesse():
    """Der Grund fuer SEARCH statt Kopfzeilen: die Kosten wachsen nicht mit."""
    box = _Box({str(AND(seen=False)): [str(i) for i in range(900)],
                str(AND(flagged=True)): []})
    imap_mod.flag_mengen(box)
    assert len(box.kriterien) == 2, f"mehr als zwei Kommandos: {box.kriterien}"


def test_leere_uids_werden_verworfen():
    box = _Box({str(AND(seen=False)): ["7", "", None], str(AND(flagged=True)): []})
    ungelesen, _ = imap_mod.flag_mengen(box)
    assert ungelesen == {"7"}


def test_scheitert_leise_statt_hart():
    """Kann der Server nicht suchen, laesst der Aufrufer die Flags in Ruhe."""
    assert imap_mod.flag_mengen(_Box("fehler")) is None


def test_leeres_ergebnis_ist_kein_fehler():
    """Ein Ordner ohne ungelesene Mails ist ein voellig normaler Zustand."""
    box = _Box({str(AND(seen=False)): [], str(AND(flagged=True)): []})
    assert imap_mod.flag_mengen(box) == (set(), set())


def test_sync_uebernimmt_nur_bei_passender_status_zahl():
    """Die Absicherung gegen die schlimmste Variante.

    Eine leere/abgeschnittene SEARCH-Antwort ist nicht von "nichts ungelesen"
    zu unterscheiden. Ohne die Gegenprobe gegen die STATUS-Zahl wuerde der
    ganze Ordner still auf gelesen gesetzt -- also genau die Datenzerstoerung,
    gegen die der ganze Umbau von heute laeuft.
    """
    quelle = inspect.getsource(cache_mod.sync_folder)
    assert "flag_mengen(" in quelle, "der SEARCH-Abgleich fehlt im Sync"

    # Die Uebernahme muss hinter einem Vergleich mit der STATUS-Zahl stehen.
    block = quelle.split("flag_mengen(")[1].split("Kopfzeilen der neuesten")[0]
    assert "== unseen" in block, (
        "die SEARCH-Zahl wird nicht gegen den Ordner-Status geprueft"
    )
    vergleich_pos = block.index("== unseen")
    uebernahme_pos = block.index("row.seen = uid not in")
    assert vergleich_pos < uebernahme_pos, (
        "die Flags werden uebernommen, bevor die Plausibilitaet geprueft ist"
    )


def test_sync_deckt_alle_gecachten_mails_ab():
    """Der Kern: die Uebernahme laeuft ueber ALLE Zeilen, nicht ueber ein Fenster.

    Stuende dort wieder `server_uids[-_FLAG_WINDOW:]`, waere der web.de-Fehler
    zurueck -- ohne dass irgendetwas fehlschlaegt.
    """
    quelle = inspect.getsource(cache_mod.sync_folder)
    block = quelle.split("flag_mengen(")[1].split("Kopfzeilen der neuesten")[0]
    assert "cached_by_uid.items()" in block, (
        "der SEARCH-Abgleich laeuft nicht ueber alle gecachten Mails"
    )
    assert "_FLAG_WINDOW" not in block, (
        "der SEARCH-Abgleich ist wieder auf ein Fenster begrenzt"
    )


def test_kopfzeilen_abgleich_bleibt_erhalten():
    """SEARCH kann keine Schlagworte und keine Thread-Kopfzeilen liefern.

    Der Kopfzeilen-Abgleich der neuesten Mails darf also nicht wegfallen, nur
    weil die Flags jetzt woanders herkommen.
    """
    quelle = inspect.getsource(cache_mod.sync_folder)
    assert "_FLAG_WINDOW" in quelle, "der Kopfzeilen-Abgleich ist verschwunden"
    assert "row.keywords = " in quelle, "Schlagworte werden nicht mehr gepflegt"


def test_search_abgleich_ist_per_default_aus():
    """ROLLBACK vom 03.09.2026 abends -- festgehalten, damit er nicht aus Versehen kippt.

    Der ordnerweite Abgleich war fachlich richtig, hat aber im Betrieb eine
    Anzeige erzeugt, die der Nutzer zu Recht als kaputt empfand: der Zaehler
    meldete ungelesene Mails, die in der Liste nicht auftauchten. Bis das
    geklaert ist, gilt: Zahl und Liste muessen zueinander passen, auch wenn die
    Zahl dadurch alte Aenderungen verpasst.

    Wer ihn wieder anschaltet, muss diesen Test bewusst anfassen -- und dabei
    ueber die Konsequenz stolpern.
    """
    assert cache_mod._FLAG_SEARCH_ENABLED is False, (
        "der ordnerweite SEARCH-Abgleich ist wieder per Default an"
    )


def test_schalter_ist_ueber_env_wieder_einschaltbar():
    """Der Code bleibt stehen, damit die Untersuchung ohne Wiederaufbau weitergeht."""
    quelle = inspect.getsource(cache_mod)
    assert "SELFMAILER_FLAG_SEARCH" in quelle, "kein Weg mehr, ihn einzuschalten"
    assert "_FLAG_SEARCH_ENABLED and do_flags" in quelle, (
        "der Schalter haengt nicht mehr vor dem SEARCH-Abgleich"
    )
