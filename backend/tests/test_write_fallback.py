"""Nutzer-Aktionen warten nicht auf einen laufenden Hintergrund-Sync.

Am 02.09.2026 an einem echten Gmail-Konto gemessen (113 Mails im Posteingang):
ein Hintergrund-Sync haelt die Konto-Verbindung 27 s am Stueck. Jeder Versuch,
in diesem Fenster eine Mail zu loeschen, lief in den Lock-Timeout und meldete
"Loeschen fehlgeschlagen" - obwohl nichts kaputt war. Lesen und Flags durften
schon immer auf eine Kurzverbindung ausweichen, Loeschen und Verschieben nicht.

Der Test prueft die Absicht (ausweichen duerfen), nicht die IMAP-Mechanik.
"""
import inspect

from app.mail import imap as imap_mod

# Alles, was ein Mensch im Browser ausloest und was nicht warten darf.
NUTZER_AKTIONEN = [
    "delete_message",
    "delete_messages",
    "move_message",
    "move_messages",
    "set_flags",
    "set_flags_many",
]


def test_nutzer_aktionen_duerfen_ausweichen():
    """Diese Operationen oeffnen bei belegter Verbindung eine eigene Kurzverbindung."""
    ohne_ausweg = []
    for name in NUTZER_AKTIONEN:
        fn = getattr(imap_mod, name, None)
        assert fn is not None, f"{name} gibt es nicht mehr - Test anpassen"
        quelle = inspect.getsource(fn)
        if "read_fallback=True" not in quelle:
            ohne_ausweg.append(name)

    assert not ohne_ausweg, (
        "Diese Nutzer-Aktionen warten wieder auf die belegte Konto-Verbindung und "
        f"scheitern dann mit einem Fehler: {ohne_ausweg}"
    )


def test_sync_weicht_bewusst_NICHT_aus():
    """Gegenprobe: der Sync selbst bleibt serialisiert.

    Zwei gleichzeitige Syncs desselben Ordners wuerden dieselben Zeilen schreiben.
    Der Sync darf also gerade NICHT ausweichen - sonst waere die Absicherung oben
    wertlos, weil einfach alles eine eigene Verbindung aufmachte.
    """
    quelle = inspect.getsource(imap_mod._mailbox)
    assert "read_fallback" in quelle, "Der Ausweich-Mechanismus fehlt"

    from app.mail import cache as cache_mod
    sync_quelle = inspect.getsource(cache_mod.sync_folder)
    assert "read_fallback=True" not in sync_quelle, (
        "sync_folder darf NICHT ausweichen - sonst laufen zwei Syncs parallel"
    )


def test_haengende_verbindung_wird_erkannt():
    """Eine Verbindung, die jemand ewig haelt, macht das Konto nicht dauerhaft unbrauchbar."""
    entry = imap_mod._PooledBox()
    assert imap_mod._is_stuck(entry) is False, "ohne Inhaber nichts zu tun"

    import time
    entry.holder = ("sync-hintergrund", "INBOX", time.monotonic())
    assert imap_mod._is_stuck(entry) is False, "frisch gehalten ist normal"

    entry.holder = ("sync-hintergrund", "INBOX", time.monotonic() - imap_mod._STUCK_AFTER - 1)
    assert imap_mod._is_stuck(entry) is True, "ewig gehalten muss auffallen"


def test_stuck_schwelle_ueber_lock_timeout():
    """Die Haenger-Schwelle muss deutlich ueber der normalen Wartezeit liegen.

    Sonst wuerde eine langsame, aber voellig gesunde Operation abgeraeumt.
    """
    assert imap_mod._STUCK_AFTER > imap_mod._LOCK_TIMEOUT * 2
    assert imap_mod._STUCK_AFTER > imap_mod._IMAP_TIMEOUT * 2


def test_hintergrund_aufraeumen_blockiert_nicht():
    """Der Block-Sweep laeuft auf eigener Verbindung.

    Am 02.09.2026 ueber /mail/pool-status gemessen: er hielt die einzige
    Konto-Verbindung je Ordner rund 20 s besetzt ("sweep_block_folders/
    [Gmail]/Spam seit 19.8s"), waehrend Sync und Loeschen des Nutzers davor
    warteten. Reine Hintergrundarbeit darf das nicht.
    """
    quelle = inspect.getsource(imap_mod.sweep_block_folders)
    assert quelle.count("read_fallback=True") >= 2, (
        "Der Sweep belegt wieder die Konto-Verbindung - Nutzer-Aktionen warten dann darauf"
    )


# Reine Leser, die im Hintergrund oder fuer die Anzeige laufen. Sie duerfen die
# eine Konto-Verbindung nicht monopolisieren - sonst warten Sync und
# Nutzer-Aktionen darauf. Ueber /mail/pool-status gemessen, nacheinander:
#   sweep_block_folders/[Gmail]/Spam seit 19.8s
#   folder_counts/INBOX              seit 16.0s
HINTERGRUND_LESER = [
    "folder_counts", "inbox_unseen", "list_folders",
    "collect_thread", "list_messages", "search_messages", "get_messages",
]


def test_hintergrund_leser_blockieren_nicht():
    blockierend = []
    for name in HINTERGRUND_LESER:
        fn = getattr(imap_mod, name, None)
        assert fn is not None, f"{name} gibt es nicht mehr - Test anpassen"
        if "read_fallback=True" not in inspect.getsource(fn):
            blockierend.append(name)
    assert not blockierend, (
        "Diese Leser belegen wieder die Konto-Verbindung; Sync und Loeschen "
        f"warten dann darauf: {blockierend}"
    )
