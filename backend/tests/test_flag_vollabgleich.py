"""CONDSTORE schaut nur nach vorne -- deshalb muss ab und zu voll gelesen werden.

Am 03.09.2026 der zweite Anlauf auf denselben Fehler. In 1.86.0 war die
Ursache behoben (die Gelesen-Sperre laeuft ab), trotzdem blieben uid 2443 und
2439 im Cache "gelesen", waehrend Gmail sie als ungelesen fuehrte. Der Sync
meldete stur `flags_weg=condstore, flags_geprueft=0`.

Der Grund ist die Bauart von CONDSTORE (RFC 7162): der Server meldet nur, was
sich SEIT dem gespeicherten MODSEQ geaendert hat. Was schon falsch im Cache
stand, als dieser Stand gesetzt wurde, meldet er nie wieder -- und weil der
CONDSTORE-Weg `do_flags = False` setzte, lief auch der volle Abgleich nie mehr.
Es gab also keinen Weg, der die Abweichung je haette korrigieren koennen.

Lehre daraus: eine Optimierung, die den langsamen Weg vollstaendig abschaltet,
schaltet auch dessen Selbstheilung ab. Der schnelle Weg bleibt der Normalfall,
aber der volle laeuft periodisch mit.
"""
import inspect
import time

from app.mail import cache as cache_mod

SCHLUESSEL = (9, "INBOX")


def setup_function():
    cache_mod._flag_full_at.clear()


def teardown_function():
    cache_mod._flag_full_at.clear()


def test_beim_ersten_mal_faellig():
    """Nach einem Neustart einmal voll lesen -- heilt Drift sofort."""
    assert cache_mod._voller_abgleich_faellig(*SCHLUESSEL) is True


def test_direkt_danach_nicht_mehr_faellig():
    cache_mod._flag_full_at[SCHLUESSEL] = time.monotonic()
    assert cache_mod._voller_abgleich_faellig(*SCHLUESSEL) is False


def test_nach_ablauf_wieder_faellig():
    cache_mod._flag_full_at[SCHLUESSEL] = time.monotonic() - cache_mod._FLAG_FULL_SECS - 1
    assert cache_mod._voller_abgleich_faellig(*SCHLUESSEL) is True


def test_frisch_gebootetes_system_blockiert_nicht():
    """time.monotonic() startet nahe null -- der Klassiker.

    Mit `.get(key, 0.0)` statt einer None-Pruefung waere `jetzt - 0.0` kleiner
    als das Intervall und der erste Voll-Abgleich liefe NIE. Genau dieser Fehler
    ist am 02.09.2026 schon einmal in der Sweep-Drosselung passiert und nur in
    der CI aufgefallen, weil dort die Uptime klein ist.
    """
    echte_zeit = time.monotonic
    try:
        time.monotonic = lambda: 12.0        # 12 s Uptime
        cache_mod._flag_full_at.clear()
        assert cache_mod._voller_abgleich_faellig(*SCHLUESSEL) is True
    finally:
        time.monotonic = echte_zeit


def test_ordner_werden_getrennt_gezaehlt():
    cache_mod._flag_full_at[(9, "INBOX")] = time.monotonic()
    assert cache_mod._voller_abgleich_faellig(9, "INBOX") is False
    assert cache_mod._voller_abgleich_faellig(9, "Archiv") is True
    assert cache_mod._voller_abgleich_faellig(6, "INBOX") is True


def test_condstore_schaltet_den_vollen_weg_nicht_mehr_dauerhaft_ab():
    """Der Kern: `do_flags = False` darf nicht mehr fest verdrahtet sein.

    Stuende dort wieder eine Konstante, gaebe es keinen Weg mehr, der eine
    bestehende Abweichung je korrigiert -- und der Fehler waere zurueck, ohne
    dass irgendetwas fehlschlaegt.
    """
    quelle = inspect.getsource(cache_mod.sync_folder)
    assert "do_flags = False" not in quelle, (
        "CONDSTORE schaltet den vollen Abgleich wieder dauerhaft ab"
    )
    assert "_voller_abgleich_faellig(" in quelle, (
        "der periodische Voll-Abgleich wird nicht mehr geprueft"
    )


def test_intervall_ist_vernuenftig():
    """Oft genug, dass Abweichungen nicht tagelang stehen; selten genug, dass
    der teure Weg nicht den Gewinn aus CONDSTORE auffrisst."""
    assert 300.0 <= cache_mod._FLAG_FULL_SECS <= 3600.0
    assert cache_mod._FLAG_FULL_SECS > cache_mod._FLAG_REFRESH_SECS * 10
