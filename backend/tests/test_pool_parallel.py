"""Mehrere Verbindungen je Konto - der eigentliche Umbau.

Die Fehlerklasse, die das beseitigt: JEDE Blockade am 02.09.2026 entstand
dadurch, dass eine Operation die eine Konto-Verbindung hielt und alle anderen
davor warteten - Flag-Abgleich, Block-Sweep, Ordnerzaehler, Papierkorb.
Mit mehreren Verbindungen ist das strukturell nicht mehr moeglich.

Getestet wird die Vergabe-Logik (_greife_zu) ohne echten IMAP-Server: genau
dort entscheidet sich, ob parallel gearbeitet wird oder gewartet.
"""
import threading
import time

import pytest

from app.mail import imap as imap_mod

KEY = "9:sven@imap.gmail.com:993"


def setup_function():
    imap_mod._POOL.clear()


def teardown_function():
    imap_mod._POOL.clear()


def _frei(conn):
    """Verbindung wieder zurueckgeben (wie der finally-Block in _mailbox)."""
    conn.holder = None
    conn.lock.release()


def test_zweite_anfrage_bekommt_eigene_verbindung():
    """Der Kern: die zweite Anfrage wartet NICHT mehr auf die erste."""
    a = imap_mod._greife_zu(KEY, "INBOX", limit=3)
    assert a is not None
    b = imap_mod._greife_zu(KEY, "INBOX", limit=3)
    assert b is not None and b is not a, "zweite Anfrage haette warten muessen - Umbau wirkungslos"
    assert len(imap_mod._POOL[KEY]) == 2


def test_kontingent_wird_eingehalten():
    """Nicht unbegrenzt: Provider lehnen zu viele Verbindungen ab (Gmail ~15)."""
    belegt = [imap_mod._greife_zu(KEY, "INBOX", limit=3) for _ in range(3)]
    assert all(c is not None for c in belegt)
    assert imap_mod._greife_zu(KEY, "INBOX", limit=3) is None, "Kontingent ueberschritten"


def test_nutzer_aktionen_duerfen_ueberziehen():
    """read_fallback: Loeschen soll nie warten, nur weil im Hintergrund gesynct wird."""
    for _ in range(3):
        assert imap_mod._greife_zu(KEY, "INBOX", limit=3) is not None
    # Hintergrund-Kontingent erschoepft ...
    assert imap_mod._greife_zu(KEY, "INBOX", limit=3) is None
    # ... die Nutzer-Aktion kommt trotzdem durch
    extra = imap_mod._greife_zu(KEY, "INBOX", limit=3 + imap_mod._POOL_EXTRA)
    assert extra is not None


def test_freigegebene_verbindung_wird_wiederverwendet():
    """Wiederverwendung statt Wegwerfen - bei 8 s Verbindungsaufbau entscheidend."""
    a = imap_mod._greife_zu(KEY, "INBOX", limit=3)
    _frei(a)
    b = imap_mod._greife_zu(KEY, "INBOX", limit=3)
    assert b is a, "es wurde eine neue Verbindung angelegt statt der freien"
    assert len(imap_mod._POOL[KEY]) == 1


def test_bevorzugt_passenden_ordner():
    """Spart ein SELECT - bei einem langsamen Konto eine ganze Runde."""
    a = imap_mod._greife_zu(KEY, "INBOX", limit=3)
    a.folder = "INBOX"
    _frei(a)
    b = imap_mod._greife_zu(KEY, "Archiv", limit=3)
    b.folder = "Archiv"
    _frei(b)

    gewaehlt = imap_mod._greife_zu(KEY, "Archiv", limit=3)
    assert gewaehlt is b, "haette die Verbindung mit passendem Ordner nehmen muessen"


def test_haengende_verbindung_blockiert_niemanden():
    """Ein haengender Thread darf das Konto nicht lahmlegen."""
    tot = imap_mod._greife_zu(KEY, "INBOX", limit=1)
    tot.holder = ("sync-hintergrund", "INBOX", time.monotonic() - imap_mod._STUCK_AFTER - 5)

    # Kontingent 1 waere erschoepft - die haengende Verbindung fliegt aber raus.
    neu = imap_mod._greife_zu(KEY, "INBOX", limit=1)
    assert neu is not None and neu is not tot
    assert tot not in imap_mod._POOL[KEY]


def test_parallele_threads_bekommen_verschiedene_verbindungen():
    """Realitaetsnah: gleichzeitige Zugriffe, keine doppelte Vergabe."""
    vergeben = []
    sperre = threading.Lock()

    def hole():
        c = imap_mod._greife_zu(KEY, "INBOX", limit=5)
        if c is not None:
            with sperre:
                vergeben.append(c)

    threads = [threading.Thread(target=hole) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(vergeben) == 5
    assert len({id(c) for c in vergeben}) == 5, "dieselbe Verbindung wurde doppelt vergeben"


def test_pool_groesse_sinnvoll():
    """Mehr als eine (sonst waere der Umbau sinnlos), aber unter dem Provider-Limit."""
    assert imap_mod._POOL_SIZE >= 2
    assert imap_mod._POOL_SIZE + imap_mod._POOL_EXTRA <= 10, "Gmail erlaubt ~15 je Konto"


@pytest.mark.parametrize("limit", [1, 2, 5])
def test_keine_verbindung_geht_verloren(limit):
    """Nach Rueckgabe muessen alle wieder vergebbar sein."""
    belegt = []
    while (c := imap_mod._greife_zu(KEY, "INBOX", limit=limit)) is not None:
        belegt.append(c)
    assert len(belegt) == limit
    for c in belegt:
        _frei(c)
    for _ in range(limit):
        assert imap_mod._greife_zu(KEY, "INBOX", limit=limit) is not None
