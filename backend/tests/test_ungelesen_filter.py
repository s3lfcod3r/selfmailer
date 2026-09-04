"""Der Ungelesen-Filter muss den GANZEN Ordner treffen, nicht die geladene Seite.

Am 03.09.2026 gemeldet: bei web.de zeigte der Zaehler 34 ungelesene Mails im
Posteingang -- live per IMAP-STATUS bestaetigt, der Cache kannte dieselben 34.
Filterte der Nutzer aber auf "ungelesen", zeigte die Liste NICHTS.

Ursache lag im Frontend: die Liste laedt EINE Seite (PAGE_SIZE = 50) und der
Filter siebte clientseitig ueber genau diese 50 Zeilen. Bei 965 Mails im Ordner
und 34 ungelesenen weiter hinten konnte er nichts finden. Der Zaehler war also
richtig -- die Mails waren nur unerreichbar.

Das Backend hatte dasselbe Problem schon zweimal geloest und sagt es in seiner
eigenen Doku: `pin_flagged` sortiert serverseitig, "nur so stehen auch markierte
Mails von Seite 12 oben auf Seite 1", und `keyword` filtert ueber den ganzen
Ordner-Cache. Nur "ungelesen" war im Frontend geblieben.

Diese Tests halten fest, dass der Filter serverseitig arbeitet -- inklusive der
beiden Stellen, an denen er sonst still zu "alle Mails anzeigen" kippen wuerde.
"""
import datetime as dt
import inspect

from sqlmodel import Session, delete

import app.api.mail as mail_api
import app.mail.cache as cache_mod
from app.core.db import engine
from app.models import CachedMessage

FOLDER = "INBOX"
KONTO = 4242


def _fuelle(neu_gelesen: int, alt_ungelesen: int) -> None:
    """Die Situation von web.de nachbauen: viele gelesene NEUE Mails, und die
    ungelesenen liegen ALT -- also ausserhalb der ersten Seite."""
    basis = dt.datetime(2026, 9, 3, 12, 0, tzinfo=dt.timezone.utc)
    with Session(engine) as s:
        s.exec(delete(CachedMessage).where(CachedMessage.account_id == KONTO))
        s.commit()
        for i in range(neu_gelesen):
            s.add(CachedMessage(
                account_id=KONTO, folder=FOLDER, uid=f"n{i}", subject=f"neu {i}",
                seen=True, sort_date=basis - dt.timedelta(minutes=i),
            ))
        for i in range(alt_ungelesen):
            s.add(CachedMessage(
                account_id=KONTO, folder=FOLDER, uid=f"a{i}", subject=f"alt {i}",
                seen=False, sort_date=basis - dt.timedelta(days=30 + i),
            ))
        s.commit()


def teardown_function():
    with Session(engine) as s:
        s.exec(delete(CachedMessage).where(CachedMessage.account_id == KONTO))
        s.commit()


def test_findet_ungelesene_ausserhalb_der_ersten_seite():
    """DER Fehlerfall: 60 gelesene neue Mails, 5 ungelesene alte, Seitengroesse 50.

    Ohne serverseitigen Filter waeren auf Seite 1 nur gelesene Mails -- und der
    Nutzer saehe beim Filter "ungelesen" eine leere Liste.
    """
    _fuelle(neu_gelesen=60, alt_ungelesen=5)
    with Session(engine) as s:
        seite1 = cache_mod.read_messages(s, KONTO, FOLDER, limit=50, offset=0)
        assert all(m["seen"] for m in seite1), "Aufbau falsch: Seite 1 hat schon Ungelesene"

        gefiltert = cache_mod.read_messages(s, KONTO, FOLDER, limit=50, offset=0, unread=True)
    assert len(gefiltert) == 5, f"der Filter findet die alten Ungelesenen nicht: {len(gefiltert)}"
    assert all(not m["seen"] for m in gefiltert)


def test_ohne_filter_bleibt_alles_wie_bisher():
    """Gegenprobe: der neue Parameter darf die normale Liste nicht anfassen."""
    _fuelle(neu_gelesen=10, alt_ungelesen=3)
    with Session(engine) as s:
        alle = cache_mod.read_messages(s, KONTO, FOLDER, limit=100, offset=0)
    assert len(alle) == 13


def test_leeres_ergebnis_ist_gueltig():
    """Ein Ordner ohne ungelesene Mails liefert eine leere Liste -- kein Fehler."""
    _fuelle(neu_gelesen=5, alt_ungelesen=0)
    with Session(engine) as s:
        assert cache_mod.read_messages(s, KONTO, FOLDER, limit=50, unread=True) == []


def test_filter_greift_auch_ueber_seiten():
    """Bei mehr Ungelesenen als eine Seite fasst muss geblaettert werden koennen."""
    _fuelle(neu_gelesen=0, alt_ungelesen=7)
    with Session(engine) as s:
        s1 = cache_mod.read_messages(s, KONTO, FOLDER, limit=5, offset=0, unread=True)
        s2 = cache_mod.read_messages(s, KONTO, FOLDER, limit=5, offset=5, unread=True)
    assert len(s1) == 5 and len(s2) == 2
    assert not ({m["uid"] for m in s1} & {m["uid"] for m in s2}), "Seiten ueberlappen"


def test_selbstheilung_kippt_nicht_in_alle_mails():
    """Die gefaehrlichste Stelle.

    Ist die erste Seite leer, laedt der Endpunkt normalerweise live nach und
    liefert am Ende die UNGEFILTERTE Liste. Bei aktivem Ungelesen-Filter ist eine
    leere Seite aber voellig normal -- ohne die Ausnahme saehe der Nutzer dann
    ausgerechnet ALLE Mails, wenn er nach ungelesenen filtert.
    """
    quelle = inspect.getsource(mail_api.messages)
    assert "not kw and not unread" in quelle, (
        "die Selbstheilung nimmt den Ungelesen-Filter nicht aus"
    )


def test_notfall_weg_haelt_den_filter_ein():
    """Faellt der Cache aus, wird live geladen -- auch dort muss gefiltert werden."""
    quelle = inspect.getsource(mail_api.messages)
    block = quelle.split("except Exception")[1]
    assert "if unread:" in block and 'not m.get("seen")' in block, (
        "der Live-Fallback liefert bei aktivem Filter weiterhin alle Mails"
    )


def test_endpunkt_reicht_den_parameter_durch():
    quelle = inspect.getsource(mail_api.messages)
    assert "unread: bool = Query(default=False)" in quelle, "Parameter fehlt am Endpunkt"
    assert "unread=unread" in quelle, "Parameter wird nicht an read_messages gereicht"
