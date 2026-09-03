"""Die Gelesen-Sperre schuetzt ein Rennen -- sie ist kein Dauerschloss.

Am 03.09.2026 an einem echten Gmail-Konto gefunden: der Server meldete vier
ungelesene Mails im Posteingang, SelfMailer zeigte null. Zwei davon namentlich
nachgewiesen (uid 2443, 2439) -- live ungelesen, im Cache "gelesen".

Ursache war `seen_sticky` in zwei Stufen:
  1. Die Sperre galt DAUERHAFT: `if not row.seen_sticky` verhinderte jede
     spaetere Uebernahme des Server-Flags -- im CONDSTORE-Weg wie im
     Vollabgleich.
  2. Die Migration setzte sie beim Anlegen der Spalte per
     `UPDATE cachedmessage SET seen_sticky = seen` fuer JEDE damals gelesene
     Mail. Damit war praktisch der gesamte Bestand gesperrt.

Wer eine solche Mail spaeter in der Gmail-App als ungelesen markierte, erfuhr
SelfMailer das nie -- und `unseen_by_folder` zaehlt `seen == False`, also fehlte
sie auch im Badge.

Legitim ist nur das kurze Rennen: der Nutzer klickt "gelesen", gleichzeitig
laeuft ein Sync mit dem alten Serverstand. Das ist in Sekunden vorbei.
"""
import datetime as dt
import inspect

from app.core import db as db_mod
from app.mail import cache as cache_mod
from app.models import CachedMessage


def _row(*, seen=True, sticky=False, alter_s=None):
    """Cache-Zeile; alter_s = vor wie vielen Sekunden die Sperre gesetzt wurde."""
    gesetzt = None
    if alter_s is not None:
        gesetzt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=alter_s)
    return CachedMessage(
        account_id=9, folder="INBOX", uid="2443", seen=seen,
        seen_sticky=sticky, seen_sticky_at=gesetzt,
    )


def test_frische_nutzerentscheidung_gewinnt():
    """Der eigentliche Zweck: der Klick ueberlebt einen gleichzeitigen Sync."""
    assert cache_mod._sticky_aktiv(_row(sticky=True, alter_s=1)) is True


def test_sperre_laeuft_ab():
    """Danach ist der Server wieder die Wahrheit."""
    alt = _row(sticky=True, alter_s=cache_mod._STICKY_SECS + 1)
    assert cache_mod._sticky_aktiv(alt) is False


def test_altbestand_ohne_zeitstempel_gilt_als_abgelaufen():
    """Genau das heilt die Zeilen aus der alten Migration -- ohne Daten-Reparatur.

    Die alte Migration setzte seen_sticky=1; einen Zeitstempel gab es damals
    nicht. Ohne diese Regel blieben uid 2443 und 2439 fuer immer gesperrt.
    """
    assert cache_mod._sticky_aktiv(_row(sticky=True, alter_s=None)) is False


def test_ohne_nutzerentscheidung_keine_sperre():
    assert cache_mod._sticky_aktiv(_row(sticky=False, alter_s=1)) is False


def test_sperre_ist_kurz_genug_um_nicht_zu_schaden():
    """Sie darf das Rennen abdecken, aber keine echte Server-Aenderung verschlucken."""
    assert 5.0 <= cache_mod._STICKY_SECS <= 300.0


def test_jeder_abgleich_weg_respektiert_den_ablauf():
    """JEDER Weg, der `seen` vom Server uebernimmt, muss den Ablauf pruefen.

    Es sind drei: der CONDSTORE-Weg, der SEARCH-Weg ueber den ganzen Ordner
    (seit 1.89.0) und der Kopfzeilen-Abgleich der neuesten Mails. Bliebe einer
    davon bei `row.seen_sticky`, waere die Sperre dort weiterhin ein
    Dauerschloss -- der Fehler also nur teilweise behoben.

    Gezaehlt wird gegen die Zuweisungen selbst, nicht gegen eine feste Zahl:
    kommt ein vierter Weg dazu, faellt der Test auf, statt stillzuhalten.
    """
    quelle = inspect.getsource(cache_mod.sync_folder)
    zuweisungen = quelle.count("row.seen = ")
    assert zuweisungen >= 3, "es gibt weniger Uebernahme-Stellen als erwartet"
    assert quelle.count("_sticky_aktiv(") == zuweisungen, (
        f"{zuweisungen} Stellen uebernehmen `seen` vom Server, aber nur "
        f"{quelle.count('_sticky_aktiv(')} pruefen den Ablauf"
    )
    assert "not row.seen_sticky" not in quelle, (
        "irgendwo wird die Sperre wieder ohne Ablauf geprueft"
    )


def test_migration_setzt_die_sperre_nicht_mehr_pauschal():
    """Der Backfill `SET seen_sticky = seen` darf nicht zurueckkommen.

    Er hat praktisch den ganzen Cache stillgelegt; ohne diesen Test faellt so
    etwas erst Wochen spaeter an falschen Ungelesen-Zahlen auf. Geprueft wird
    nur ausfuehrbarer Code -- im Kommentar darf der Vorfall stehen bleiben.
    """
    code = [
        z for z in inspect.getsource(db_mod).splitlines()
        if not z.lstrip().startswith("#")
    ]
    treffer = [z.strip() for z in code if "seen_sticky = seen" in z]
    assert not treffer, f"der pauschale Backfill ist zurueck: {treffer}"


def test_spalte_wird_migriert():
    """Bestandsdatenbanken brauchen die neue Spalte, sonst kippt jeder Sync."""
    spalten = dict(db_mod._ADDITIVE_COLUMNS["cachedmessage"])
    assert "seen_sticky_at" in spalten, "Spalte fehlt in der additiven Migration"
    assert "DATETIME" in spalten["seen_sticky_at"].upper()
