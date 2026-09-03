"""Der Loesch-Tombstone laeuft ab -- sonst verschwindet eine Mail fuer immer.

Am 03.09.2026 an einem echten Gmail-Konto gefunden: der Server meldete 104
Mails im Posteingang, die Liste zeigte 103. Die fehlende (uid 2445) war per
`GET /mail/9/messages/2445` problemlos abrufbar -- sie existierte also, war
aber mit `hidden=True` aus der Liste genommen.

Der Tombstone hat einen guten Grund: ein flatternder Server (web.de) liefert
eine gerade geloeschte Mail noch ein paar Syncs lang mit; ohne ihn taeuchte sie
in der Liste wieder auf. Aufgeraeumt wird er ueber `miss_count`, sobald der
Server die UID mehrfach NICHT mehr liefert.

Genau da liegt die Falle: liefert der Server die Mail DAUERHAFT weiter (weil
die Loeschung nie durchging), zaehlt `miss_count` nie hoch -- und die Mail
bleibt fuer immer unsichtbar. Ein Marker ohne Ablauf, dieselbe Fehlerklasse
wie die frueher dauerhafte `seen_sticky`-Sperre.
"""
import datetime as dt
import inspect

from app.core import db as db_mod
from app.mail import cache as cache_mod
from app.models import CachedMessage


def _row(*, hidden=True, alter_s=None):
    """Cache-Zeile; alter_s = vor wie vielen Sekunden ausgeblendet wurde."""
    gesetzt = None
    if alter_s is not None:
        gesetzt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=alter_s)
    return CachedMessage(
        account_id=9, folder="INBOX", uid="2445",
        hidden=hidden, hidden_at=gesetzt,
    )


def test_frisch_geloeschte_mail_bleibt_ausgeblendet():
    """Der eigentliche Zweck: sie darf nicht sofort wieder in der Liste stehen."""
    assert cache_mod._tombstone_aktiv(_row(alter_s=5)) is True


def test_tombstone_laeuft_ab():
    """Danach entscheidet wieder der Server."""
    alt = _row(alter_s=cache_mod._HIDDEN_SECS + 1)
    assert cache_mod._tombstone_aktiv(alt) is False


def test_altbestand_ohne_zeitstempel_gilt_als_abgelaufen():
    """Damit taucht uid 2445 beim naechsten Sync von selbst wieder auf.

    Zeilen aus der Zeit vor dieser Version haben keinen Zeitstempel. Ohne diese
    Regel braeuchte es eine Daten-Reparatur auf der Produktivdatenbank.
    """
    assert cache_mod._tombstone_aktiv(_row(alter_s=None)) is False


def test_nicht_ausgeblendete_mail_ist_nie_betroffen():
    assert cache_mod._tombstone_aktiv(_row(hidden=False, alter_s=5)) is False


def test_ablauf_ist_laenger_als_das_flattern_dauert():
    """Er muss _MISS_LIMIT Syncs ueberdauern -- sonst blitzt die geloeschte Mail
    doch kurz wieder auf, und genau das soll er ja verhindern."""
    flatter_fenster = cache_mod._MISS_LIMIT * cache_mod._FLAG_REFRESH_SECS
    assert cache_mod._HIDDEN_SECS > flatter_fenster
    assert cache_mod._HIDDEN_SECS <= 3600.0


def test_sync_hebt_abgelaufenen_tombstone_auf():
    """Der Kern: nur wenn der Server die Mail NOCH listet, kommt sie zurueck.

    Steht die Aufhebung im falschen Zweig (bei den verschwundenen Mails), waere
    sie wirkungslos - und der Fehler bliebe unbemerkt bestehen.
    """
    quelle = inspect.getsource(cache_mod.sync_folder)
    assert "_tombstone_aktiv(" in quelle, "der Ablauf wird im Sync gar nicht geprueft"

    # Die Aufhebung muss VOR dem elif-Zweig stehen, also im "uid in server_set"-Fall.
    vor_elif = quelle.split("elif reliable:")[0]
    assert "row.hidden = False" in vor_elif, (
        "die Aufhebung steht nicht im Zweig 'Server listet die Mail noch'"
    )


def test_aufhebung_nur_bei_vertrauenswuerdiger_serversicht():
    """Bei einer kaputten/partiellen Antwort (web.de-Cluster) nichts anfassen -
    sonst kaeme eine geloeschte Mail ausgerechnet dann zurueck, wenn der Server
    gerade unzuverlaessig ist."""
    quelle = inspect.getsource(cache_mod.sync_folder)
    zeile = [z for z in quelle.splitlines() if "_tombstone_aktiv(" in z]
    assert zeile, "Aufruf nicht gefunden"
    assert "reliable" in zeile[0], "die Aufhebung prueft die Server-Sicht nicht"


def test_hide_uids_setzt_den_zeitstempel():
    """Ohne Zeitstempel waere jeder neue Tombstone sofort abgelaufen - die
    geloeschte Mail bliebe also in der Liste stehen."""
    quelle = inspect.getsource(cache_mod.hide_uids)
    assert "hidden_at" in quelle, "hide_uids setzt keinen Zeitstempel"


def test_spalte_wird_migriert():
    """Bestandsdatenbanken brauchen die neue Spalte, sonst kippt jeder Sync."""
    spalten = dict(db_mod._ADDITIVE_COLUMNS["cachedmessage"])
    assert "hidden_at" in spalten, "Spalte fehlt in der additiven Migration"
    assert "DATETIME" in spalten["hidden_at"].upper()
