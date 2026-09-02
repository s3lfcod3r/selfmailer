"""Diagnose der Verbindungs-Belegung.

Ein Gmail-Konto war am 02.09.2026 ueber Minuten dauerhaft "beschaeftigt" -
25 Sync-Versuche in Folge liefen in den Lock-Timeout. WAS die Verbindung
hielt, stand nur in den Container-Logs, an die im Zweifel niemand herankommt.
Der Endpunkt macht es abfragbar. Er darf dabei NIE Zugangsdaten preisgeben:
der Pool-Schluessel enthaelt den Login-Namen.
"""
import time

from app.mail import imap as imap_mod


def setup_function():
    imap_mod._POOL.clear()


def teardown_function():
    imap_mod._POOL.clear()


def test_leerer_pool():
    assert imap_mod.pool_status() == []


def test_freie_verbindung():
    imap_mod._POOL["9:sven@imap.gmail.com:993"] = imap_mod._PooledBox()
    (eintrag,) = imap_mod.pool_status()
    assert eintrag["account_id"] == 9
    assert eintrag["belegt"] is False
    assert eintrag["operation"] is None


def test_belegte_verbindung_nennt_operation_und_dauer():
    entry = imap_mod._PooledBox()
    entry.holder = ("sync-hintergrund", "INBOX", time.monotonic() - 12.0)
    imap_mod._POOL["9:sven@imap.gmail.com:993"] = entry

    (eintrag,) = imap_mod.pool_status()
    assert eintrag["belegt"] is True
    assert eintrag["operation"] == "sync-hintergrund"
    assert eintrag["ordner"] == "INBOX"
    assert 11.0 <= eintrag["haelt_seit_s"] <= 14.0
    assert eintrag["gilt_als_haengend"] is False


def test_haengende_verbindung_wird_ausgewiesen():
    entry = imap_mod._PooledBox()
    entry.holder = ("sync-hintergrund", "INBOX", time.monotonic() - imap_mod._STUCK_AFTER - 5)
    imap_mod._POOL["9:sven@imap.gmail.com:993"] = entry
    assert imap_mod.pool_status()[0]["gilt_als_haengend"] is True


def test_gibt_keine_zugangsdaten_preis():
    """Der Pool-Schluessel enthaelt den Login - der darf nicht nach aussen."""
    imap_mod._POOL["9:geheim.nutzer@imap.gmail.com:993"] = imap_mod._PooledBox()
    text = repr(imap_mod.pool_status())
    assert "geheim.nutzer" not in text
    assert "imap.gmail.com" not in text
