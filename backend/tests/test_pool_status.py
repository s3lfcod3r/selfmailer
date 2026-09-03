"""Verbindungs-Pool: mehrere Verbindungen je Konto und ihre Diagnose.

Vorher hielt SelfMailer EINE Verbindung je Konto, alles serialisierte sich
darauf. Am 02.09.2026 wurde daraus bei einem Konto mit 8-10 s
Verbindungsaufbau eine Dauerblockade: 25 Sync-Versuche in Folge liefen in den
Lock-Timeout, Loeschen meldete "fehlgeschlagen". Thunderbird haelt fuenf
Verbindungen je Konto - SelfMailer jetzt drei, plus zwei Reserve fuer
Nutzer-Aktionen.

pool_status() hat damals drei Blockade-Ursachen ueberhaupt erst auffindbar
gemacht (Block-Sweep, Ordnerzaehler, Papierkorb-Aufraeumen) und darf dabei nie
Zugangsdaten preisgeben: der Pool-Schluessel enthaelt Login und Mailserver.
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
    imap_mod._POOL["9:sven@imap.gmail.com:993"] = [imap_mod._Conn()]
    (eintrag,) = imap_mod.pool_status()
    assert eintrag["account_id"] == 9
    assert eintrag["belegt"] is False
    assert eintrag["operation"] is None
    assert eintrag["von"] == 1


def test_belegte_verbindung_nennt_operation_und_dauer():
    c = imap_mod._Conn()
    c.holder = ("sync-hintergrund", "INBOX", time.monotonic() - 12.0)
    imap_mod._POOL["9:sven@imap.gmail.com:993"] = [c]

    (eintrag,) = imap_mod.pool_status()
    assert eintrag["belegt"] is True
    assert eintrag["operation"] == "sync-hintergrund"
    assert eintrag["ordner"] == "INBOX"
    assert 11.0 <= eintrag["haelt_seit_s"] <= 14.0
    assert eintrag["gilt_als_haengend"] is False


def test_zeigt_jede_verbindung_einzeln():
    """Der eigentliche Zweck: sehen, WELCHE der Verbindungen belegt ist."""
    frei, belegt = imap_mod._Conn(), imap_mod._Conn()
    belegt.holder = ("apply_rules", "INBOX", time.monotonic() - 3.0)
    imap_mod._POOL["9:sven@imap.gmail.com:993"] = [frei, belegt]

    status = imap_mod.pool_status()
    assert len(status) == 2
    assert [e["verbindung"] for e in status] == [1, 2]
    assert all(e["von"] == 2 for e in status)
    assert [e["belegt"] for e in status] == [False, True]


def test_haengende_verbindung_wird_ausgewiesen():
    c = imap_mod._Conn()
    c.holder = ("sync-hintergrund", "INBOX", time.monotonic() - imap_mod._STUCK_AFTER - 5)
    imap_mod._POOL["9:sven@imap.gmail.com:993"] = [c]
    assert imap_mod.pool_status()[0]["gilt_als_haengend"] is True


def test_gibt_keine_zugangsdaten_preis():
    """Der Pool-Schluessel enthaelt Login UND Mailserver - beides bleibt drin."""
    imap_mod._POOL["9:geheim.nutzer@imap.gmail.com:993"] = [imap_mod._Conn()]
    text = repr(imap_mod.pool_status())
    assert "geheim.nutzer" not in text
    assert "imap.gmail.com" not in text
