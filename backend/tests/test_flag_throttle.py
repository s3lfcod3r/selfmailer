"""Der Flag-Abgleich drosselt sich nach seinen eigenen Kosten.

Am 02.09.2026 an einem echten Gmail-Konto gemessen (112 Mails):

    gesamt 26,1 s = warten 4,2 s + IMAP 21,9 s
    davon Flag-Abgleich 13,4 s  -> 51 % des gesamten Syncs

Bei festem _FLAG_REFRESH_SECS=25 lief dieser 13-Sekunden-Abgleich alle 25
Sekunden und belegte die einzige Konto-Verbindung praktisch dauerhaft. Ein
schnelles Konto (web.de, Abgleich im Millisekundenbereich) soll dagegen
weiterhin oft abgleichen.
"""
from app.mail import cache as cache_mod


def setup_function():
    cache_mod._flag_cost.clear()


def test_ohne_messwert_gilt_der_grundwert():
    assert cache_mod._flag_intervall(1, "INBOX") == cache_mod._FLAG_REFRESH_SECS


def test_billiger_abgleich_bleibt_haeufig():
    """web.de-Fall: 20 ms Abgleich -> keine Drosselung."""
    cache_mod._flag_cost[(1, "INBOX")] = 0.02
    assert cache_mod._flag_intervall(1, "INBOX") == cache_mod._FLAG_REFRESH_SECS


def test_teurer_abgleich_wird_gedrosselt():
    """Der gemessene Gmail-Fall: 13,4 s Abgleich -> deutlich seltener."""
    cache_mod._flag_cost[(9, "INBOX")] = 13.4
    intervall = cache_mod._flag_intervall(9, "INBOX")
    assert intervall > 200, f"13,4 s Abgleich alle {intervall:.0f}s ist zu oft"
    assert intervall == 13.4 * cache_mod._FLAG_COST_FACTOR


def test_obergrenze_greift():
    """Auch ein extrem langsames Konto gleicht irgendwann wieder ab."""
    cache_mod._flag_cost[(9, "INBOX")] = 120.0
    assert cache_mod._flag_intervall(9, "INBOX") == cache_mod._FLAG_REFRESH_MAX


def test_getrennt_je_ordner_und_konto():
    """Ein teurer Posteingang darf einen billigen Unterordner nicht ausbremsen."""
    cache_mod._flag_cost[(9, "INBOX")] = 13.4
    assert cache_mod._flag_intervall(9, "Archiv") == cache_mod._FLAG_REFRESH_SECS
    assert cache_mod._flag_intervall(6, "INBOX") == cache_mod._FLAG_REFRESH_SECS
