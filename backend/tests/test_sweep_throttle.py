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
