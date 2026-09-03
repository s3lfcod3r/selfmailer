"""Der Block-Sweep laeuft nicht mehr bei jedem Durchlauf.

Am 02.09.2026 ueber /mail/pool-status sichtbar gemacht: ein Gmail-Konto war
durchgehend belegt - nicht vom Sync, sondern von apply_rules und
sweep_block_folders:

    Konto 9: apply_rules/INBOX                 seit 10,4 s
    Konto 9: sweep_block_folders/INBOX         seit  8,9 s
    Konto 9: sweep_block_folders/[Gmail]/Spam  seit  4,7 s

Alle sieben anderen Konten waren zeitgleich frei.
"""
from app.mail import scheduler as sched


def setup_function():
    sched._last_sweep.clear()


def test_erster_lauf_immer():
    assert sched._sweep_faellig(9) is True


def test_direkt_danach_nicht_wieder():
    assert sched._sweep_faellig(9) is True
    assert sched._sweep_faellig(9) is False, "wuerde die Verbindung dauerhaft belegen"


def test_nach_ablauf_wieder():
    import time
    assert sched._sweep_faellig(9) is True
    sched._last_sweep[9] = time.monotonic() - sched._SWEEP_MIN_SECS - 1
    assert sched._sweep_faellig(9) is True


def test_je_konto_getrennt():
    """Ein langsames Konto darf die anderen nicht ausbremsen."""
    assert sched._sweep_faellig(9) is True
    assert sched._sweep_faellig(6) is True


def test_intervall_deutlich_ueber_dem_takt():
    """Sonst waere die Drosselung wirkungslos."""
    assert sched._SWEEP_MIN_SECS >= sched._INTERVAL


def test_erster_lauf_auch_auf_frisch_gestartetem_system():
    """Regression: mit 0.0 als Startwert lief der erste Sweep nie.

    time.monotonic() zaehlt je nach System ab dem Start. Auf einem frisch
    gebooteten Host (CI-Container, Unraid nach Neustart) ist der Wert klein,
    "jetzt minus 0" also kleiner als das Intervall - die Drosselung haette
    dauerhaft blockiert statt nur gedrosselt.
    """
    import time as _t
    from unittest.mock import patch

    sched._last_sweep.clear()
    with patch.object(_t, "monotonic", return_value=12.0):   # 12 s Uptime
        assert sched._sweep_faellig(9) is True


def test_purge_laeuft_nicht_alle_zwei_minuten():
    """"Loesche, was aelter als N TAGE ist" braucht keinen 2-Minuten-Takt.

    Ueber /mail/pool-status gesehen: "_purge_folder seit 6.5s", waehrend der
    Sync des Nutzers davor wartete - fuer eine Aufraeumarbeit, deren Ergebnis
    sich frühestens am naechsten Tag aendert.
    """
    sched._last_purge.clear()
    assert sched._purge_faellig(9) is True
    assert sched._purge_faellig(9) is False
    assert sched._PURGE_MIN_SECS >= 300


def test_purge_je_konto_getrennt():
    sched._last_purge.clear()
    assert sched._purge_faellig(9) is True
    assert sched._purge_faellig(6) is True


def test_purge_erster_lauf_auf_frischem_system():
    """Gleiche Falle wie beim Sweep: 0.0 als Startwert waere ein Dauerblock."""
    import time as _t
    from unittest.mock import patch
    sched._last_purge.clear()
    with patch.object(_t, "monotonic", return_value=9.0):
        assert sched._purge_faellig(9) is True
