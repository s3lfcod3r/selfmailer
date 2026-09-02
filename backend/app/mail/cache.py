"""Lokaler Mail-Cache (DB) für schnelle Listenanzeige + Delta-Sync.

Idee (Thunderbird-Stil): Die Liste eines Ordners kommt SOFORT aus der SQLite-DB.
Ein Sync holt vom IMAP-Server nur die NEUEN Mails (Köpfe), entfernt gelöschte
und gleicht Flags ab. Der Cache ist reine Beschleunigung — schlägt etwas fehl,
fällt der Aufrufer auf den Live-Abruf zurück.
"""
from __future__ import annotations

import logging
import time
import datetime as dt
import json
import re
from email.utils import parsedate_to_datetime

from imap_tools import AND
from sqlalchemy import func, literal, update
from sqlmodel import Session, select

from ..models import CachedFolder, CachedMessage, FolderSync, MailAccount
from .imap import FLAGGED, SEEN, _detail_dict, _mailbox, _snippet, keywords_of, thread_headers

logger = logging.getLogger(__name__)

# Obergrenze, wie viele (neueste) Mail-Köpfe pro Sync nachgeladen werden.
# Bewusst nicht zu hoch: ein Lauf bleibt zeitlich beschaenkt; sehr große Ordner
# füllen sich über mehrere Syncs. Der Sync committet ATOMAR (nur einmal am Ende),
# damit die Liste während des Aufbaus nie einen inkonsistenten Zwischenstand zeigt.
_SYNC_CAP = 1000
# Erst nach so vielen Syncs IN FOLGE ohne Server-Treffer wird eine Mail entfernt.
# Härtung gegen flatternde Cluster-Server (z. B. web.de), die je Verbindung eine
# andere Ordnersicht liefern — sonst würden Mails ständig verschwinden/wiederkommen.
_MISS_LIMIT = 5
# Flag-Abgleich nur für die neuesten N UIDs (dort ändern sich Flags am ehesten).
_FLAG_WINDOW = 120
# Der Flag-Abgleich (Header-Fetch vieler Mails) ist der teuerste Teil eines Syncs
# und ändert sich selten. Darum höchstens alle N Sekunden — häufige Ordner-
# wechsel/Polls laufen dann nur noch billig (Status + UID-Diff).
_FLAG_REFRESH_SECS = 25
# Obergrenze für den Volltext, den der Sync gleich mit ablegt (JSON-Länge).
# Der Fetch lädt die Mail ohnehin KOMPLETT (Snippet + Anhang-Flag brauchen den
# Body), also kostet das Mitspeichern keine einzige zusätzliche IMAP-Runde — es
# spart dem Frontend das zweite Holen derselben Mails über /messages/prefetch.
# Der Deckel hält Ausreißer (bildlastige Newsletter) aus der DB; solche Mails
# werden beim Öffnen wie bisher live geholt.
_DETAIL_CACHE_MAX = 100 * 1024


def _as_utc(value: dt.datetime | None) -> dt.datetime | None:
    """Naive (aus SQLite gelesene) Zeit als UTC interpretieren, sonst unverändert."""
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def _to_utc_naive(value: dt.datetime | None) -> dt.datetime | None:
    """Sortier-Datum auf NAIVE UTC normalisieren. Sonst mischen sich timezone-aware
    Header (z. B. +0200/GMT) und naive (z. B. '-0000' oder ohne Offset) — SQLite
    vergleicht dann die lokale Uhrzeit-Komponente statt des absoluten Zeitpunkts und
    sortiert falsch (Uhrzeit zerwürfelt). Aware -> nach UTC umrechnen + tz entfernen;
    naive bleibt (RFC '-0000' meint bereits UTC)."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value


def backfill_sort_dates(session: Session) -> int:
    """Einmalige Reparatur: bestehende sort_date aus dem gespeicherten Header (date_str)
    NEU parsen + auf naive UTC normalisieren. Behebt falsch sortierte Altbestände
    (gemischte Zeitzonen). Idempotent; liefert die Anzahl korrigierter Zeilen."""
    fixed = 0
    rows = session.exec(select(CachedMessage).where(CachedMessage.date_str != "")).all()
    for r in rows:
        try:
            d = _to_utc_naive(parsedate_to_datetime(r.date_str))
        except (TypeError, ValueError):
            continue
        if d is not None and r.sort_date != d:
            r.sort_date = d
            session.add(r)
            fixed += 1
    if fixed:
        session.commit()
    return fixed


def _to_dict(r: CachedMessage) -> dict:
    return {
        "uid": r.uid, "subject": r.subject, "from": r.from_addr, "date": r.date_str,
        "seen": r.seen, "flagged": r.flagged, "snippet": r.snippet,
        "has_attachments": r.has_attachments,
        # Thread-Header für die Konversations-Gruppierung im Frontend.
        "message_id": r.message_id, "in_reply_to": r.in_reply_to, "references": r.refs,
        # Gesetzte Labels (IMAP-Keywords).
        "labels": r.keywords.split() if r.keywords else [],
    }


def has_cache(session: Session, account_id: int, folder: str) -> bool:
    return session.exec(
        select(FolderSync).where(FolderSync.account_id == account_id, FolderSync.folder == folder)
    ).first() is not None


def read_messages(
    session: Session, account_id: int, folder: str, limit: int = 50, offset: int = 0,
    *, pin_flagged: bool = False, keyword: str = "",
) -> list[dict]:
    """Kopfzeilen eines Ordners, neueste zuerst.

    ``pin_flagged``: markierte Mails (Stern) zuerst. Bewusst SERVERSEITIG sortiert
    und nicht im Frontend — nur so stehen auch markierte Mails von Seite 12 oben
    auf Seite 1. Eine Frontend-Sortierung könnte immer nur die geladene Seite
    umsortieren.
    ``keyword``: nur Mails mit diesem Label (IMAP-Keyword) — über den GANZEN Ordner-
    Cache (alle Seiten), damit der Label-Filter nicht nur die geladene Seite trifft."""
    order = [CachedMessage.sort_date.desc(), CachedMessage.id.desc()]
    if pin_flagged:
        order.insert(0, CachedMessage.flagged.desc())
    stmt = (
        select(CachedMessage)
        .where(
            CachedMessage.account_id == account_id, CachedMessage.folder == folder,
            CachedMessage.hidden == False,  # noqa: E712 - ausgeblendete (gelöschte) nicht zeigen
        )
    )
    if keyword:
        # Wort-genau gegen die leerzeichen-getrennten Keywords matchen (kein Teilstring:
        # "Shelly" darf nicht "ShellyPro" treffen) → mit Leerzeichen umrahmt vergleichen.
        # LIKE-Sonderzeichen im Keyword escapen (v. a. "_" = Einzelzeichen-Wildcard, sonst
        # würde "test_pro" auch "testXpro" matchen), mit expliziter ESCAPE-Klausel.
        esc = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(
            (literal(" ") + CachedMessage.keywords + literal(" ")).like(f"% {esc} %", escape="\\")
        )
    rows = session.exec(stmt.order_by(*order).offset(offset).limit(limit)).all()
    return [_to_dict(r) for r in rows]


def read_unified(
    session: Session, account_ids: list[int], limit: int = 50, offset: int = 0,
) -> list[dict]:
    """Aggregierte INBOX-Kopfzeilen ÜBER MEHRERE Konten (nur Cache), neueste zuerst.

    Eine einzige Abfrage über alle Konten → korrekte globale Paginierung. Jede Zeile
    trägt zusätzlich ``account_id`` zur Zuordnung im Frontend (Öffnen/Antworten)."""
    if not account_ids:
        return []
    stmt = (
        select(CachedMessage)
        .where(
            CachedMessage.account_id.in_(account_ids),
            CachedMessage.folder == "INBOX",
            CachedMessage.hidden == False,  # noqa: E712 - Ausgeblendete (gelöschte) nicht zeigen
        )
        .order_by(CachedMessage.sort_date.desc(), CachedMessage.id.desc())
        .offset(offset)
        .limit(limit)
    )
    out: list[dict] = []
    for r in session.exec(stmt).all():
        d = _to_dict(r)
        d["account_id"] = r.account_id
        out.append(d)
    return out


# Betreff-Normalisierung — MUSS 1:1 zum Frontend (lib/threads.ts normalizeSubject)
# passen, damit die Schlüssel der Counterpart-Map dort greifen: Re/AW/Fwd/…-Präfixe
# (auch CJK) wiederholt abschneiden, Whitespace glätten, kleinschreiben.
_CP_PREFIX_RE = re.compile(r"^\s*(re|aw|fwd?|wg|sv|antw?|antwort|回复|转发)\s*(\[\d+])?\s*:\s*", re.IGNORECASE)


def _norm_subject(subject: str) -> str:
    s = (subject or "").strip()
    for _ in range(10):
        n = _CP_PREFIX_RE.sub("", s)
        if n == s:
            break
        s = n
    return re.sub(r"\s+", " ", s).strip().lower()


def _addr_of(frm: str) -> str:
    m = re.search(r"<([^>]+)>", frm or "")
    return (m.group(1) if m else (frm or "")).strip().lower()


def thread_counterparts(session: Session, account_id: int, own_emails: list[str]) -> dict[str, str]:
    """Je Thread (normalisierter Betreff) den zuletzt gesehenen NICHT-eigenen Absender
    über den GESAMTEN Konto-Cache (alle Ordner).

    Zweck: Die Listen-Konversation kennt so ihren Gesprächspartner auch dann, wenn
    dessen Eingangsmails gerade nicht in der geladenen Seite stehen (Pagination/
    Stern-Sortierung) — dann steht in der Liste NIE fälschlich „Ich".
    Rückgabe: { normalisierter_Betreff: roher From-Header des Gegenübers }.
    """
    own = {_addr_of(e) for e in own_emails if e}
    rows = session.exec(
        select(CachedMessage.subject, CachedMessage.from_addr)
        .where(CachedMessage.account_id == account_id)
        .order_by(CachedMessage.sort_date.desc(), CachedMessage.id.desc())
        .limit(4000)
    ).all()
    out: dict[str, str] = {}
    for subject, from_addr in rows:
        key = _norm_subject(subject or "")
        if not key or key in out:                 # neueste zuerst → erster Treffer gewinnt
            continue
        if _addr_of(from_addr) in own:            # eigene Mail → nicht der Gegenüber
            continue
        out[key] = from_addr or ""
    return out


def recent_unseen(session: Session, account_id: int, folder: str = "INBOX", limit: int = 5) -> list[dict]:
    """Neueste UNGELESENE Köpfe eines Ordners (für eine Badge-Vorschau).

    Reiner Cache-Lesezugriff; enthält zusätzlich `ts` (ISO-Sortierdatum), damit
    der Aufrufer Mails mehrerer Konten zeitlich mischen kann.
    """
    rows = session.exec(
        select(CachedMessage)
        .where(
            CachedMessage.account_id == account_id,
            CachedMessage.folder == folder,
            CachedMessage.seen == False,  # noqa: E712 - SQL-Vergleich, nicht `is False`
            CachedMessage.hidden == False,  # noqa: E712
        )
        .order_by(CachedMessage.sort_date.desc(), CachedMessage.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "uid": r.uid, "subject": r.subject, "from": r.from_addr,
            "date": r.date_str, "ts": r.sort_date.isoformat() if r.sort_date else "",
        }
        for r in rows
    ]


def folder_uids(session: Session, account_id: int, folder: str) -> list[str]:
    """Alle gecachten UIDs eines Ordners (neueste zuerst) — für "Alle auswählen".

    Bewusst nur die UIDs (kein Body/Snippet), damit das Selektieren über alle
    Seiten hinweg billig bleibt. Begrenzt durch die Cache-Tiefe (sync cap).
    """
    rows = session.exec(
        select(CachedMessage.uid)
        .where(
            CachedMessage.account_id == account_id, CachedMessage.folder == folder,
            CachedMessage.hidden == False,  # noqa: E712
        )
        .order_by(CachedMessage.sort_date.desc(), CachedMessage.id.desc())
    ).all()
    return [u for u in rows if u]


def unseen_by_folder(session: Session, account_id: int) -> dict[str, int]:
    """Ungelesen-Zahl je Ordner AUS DEM CACHE — konsistent mit der angezeigten Liste.

    Verhindert die verwirrende Situation „Badge zeigt 7 ungelesen, aber keine sichtbar":
    die Zahl kommt dann aus derselben Quelle wie die Liste (der Cache), nicht aus dem
    (bei web.de unzuverlässigen) Server-Status.
    """
    rows = session.exec(
        select(CachedMessage.folder, func.count())
        .where(
            CachedMessage.account_id == account_id,
            CachedMessage.seen == False,  # noqa: E712
            CachedMessage.hidden == False,  # noqa: E712 - ausgeblendete zählen nicht
        )
        .group_by(CachedMessage.folder)
    ).all()
    return {folder: int(cnt) for folder, cnt in rows}


def cached_folder_names(session: Session, account_id: int) -> set[str]:
    """Ordner, für die überhaupt Cache-Zeilen existieren (nur für die stimmt die
    Cache-Ungelesen-Zahl; sonst besser beim Server-Wert bleiben)."""
    rows = session.exec(
        select(CachedMessage.folder).where(CachedMessage.account_id == account_id).distinct()
    ).all()
    return {f for f in rows if f}


def read_folder_counts(session: Session, account_id: int) -> list[dict]:
    """Gecachte Ordnerliste + Zähler (für die SOFORTige Seitenleiste beim F5).

    Leer, wenn für das Konto noch nie ein Live-Abruf lief — dann fällt der
    Aufrufer auf Live-IMAP zurück.
    """
    rows = session.exec(
        select(CachedFolder)
        .where(CachedFolder.account_id == account_id)
        .order_by(CachedFolder.idx)
    ).all()
    return [{"name": r.folder, "unseen": r.unseen, "total": r.total, "special": r.special} for r in rows]


def write_folder_counts(session: Session, account_id: int, items: list[dict]) -> None:
    """Ersetzt den Ordner-Cache eines Kontos mit frisch live geholten Zählern."""
    old = session.exec(
        select(CachedFolder).where(CachedFolder.account_id == account_id)
    ).all()
    for r in old:
        session.delete(r)
    for idx, it in enumerate(items):
        name = it.get("name")
        if not name:
            continue
        session.add(CachedFolder(
            account_id=account_id, folder=name, idx=idx,
            unseen=int(it.get("unseen", 0) or 0), total=int(it.get("total", 0) or 0),
            special=str(it.get("special", "") or ""),
        ))
    session.commit()


def read_detail(session: Session, account_id: int, folder: str, uid: str) -> dict | None:
    """Gecachten Mail-Volltext zurückgeben (oder None, wenn noch nie geöffnet).

    seen/flagged werden aus der aktuellen Cache-Zeile übernommen (frischer als
    der eingefrorene JSON-Stand), damit die Anzeige stimmt.
    """
    row = session.exec(
        select(CachedMessage).where(
            CachedMessage.account_id == account_id, CachedMessage.folder == folder, CachedMessage.uid == uid
        )
    ).first()
    if not row or not row.detail_json:
        return None
    try:
        detail = json.loads(row.detail_json)
    except (ValueError, TypeError):
        return None
    detail["seen"] = row.seen
    detail["flagged"] = row.flagged
    return detail


def read_header(session: Session, account_id: int, folder: str, uid: str) -> dict | None:
    """Nur den gecachten Kopf (Betreff/Absender/Datum/Vorschau) einer Mail.

    Rückfall, wenn die Mail serverseitig weg ist und KEIN Volltext gecacht wurde:
    besser die Vorschau aus dem Zwischenspeicher zeigen als „nicht gefunden".
    """
    row = session.exec(
        select(CachedMessage).where(
            CachedMessage.account_id == account_id, CachedMessage.folder == folder, CachedMessage.uid == uid
        )
    ).first()
    return _to_dict(row) if row else None


def uncached_detail_uids(session: Session, account_id: int, folder: str, uids: list[str]) -> list[str]:
    """Von uids diejenigen, die noch KEINEN gecachten Volltext haben.

    Damit das Vorwärmen nur fehlende Bodies holt (kein erneuter Server-Abruf
    für schon gecachte Mails).
    """
    if not uids:
        return []
    rows = session.exec(
        select(CachedMessage.uid, CachedMessage.detail_json).where(
            CachedMessage.account_id == account_id,
            CachedMessage.folder == folder,
            CachedMessage.uid.in_(uids),
        )
    ).all()
    have = {uid for uid, dj in rows if dj}
    return [u for u in uids if u not in have]


def write_detail(session: Session, account_id: int, folder: str, uid: str, detail: dict) -> None:
    """Legt den live geholten Mail-Volltext im Cache ab (für schnelles Wieder-Öffnen)."""
    row = session.exec(
        select(CachedMessage).where(
            CachedMessage.account_id == account_id, CachedMessage.folder == folder, CachedMessage.uid == uid
        )
    ).first()
    if not row:
        return
    try:
        row.detail_json = json.dumps(detail, ensure_ascii=False)
    except (TypeError, ValueError):
        return
    session.add(row)
    session.commit()


def _adjust_cached_unseen(session: Session, account_id: int, folder: str, delta: int) -> None:
    """Passt den aggregierten Ungelesen-Zähler eines Ordners SOFORT an (clamped >=0),
    sodass summary()/die Postfach-Badges nicht erst auf den nächsten Scheduler-Sync
    warten. Baut auf dem echten (vom Scheduler per IMAP STATUS gepflegten) Wert auf und
    korrigiert nur um die Nutzer-Aktion. Hält FolderSync UND CachedFolder konsistent."""
    if delta == 0:
        return
    fs = session.exec(
        select(FolderSync).where(FolderSync.account_id == account_id, FolderSync.folder == folder)
    ).first()
    if fs is not None:
        fs.unseen = max(0, int(fs.unseen) + delta)
        session.add(fs)
    cf = session.exec(
        select(CachedFolder).where(CachedFolder.account_id == account_id, CachedFolder.folder == folder)
    ).first()
    if cf is not None:
        cf.unseen = max(0, int(cf.unseen) + delta)
        session.add(cf)


def _set_row_seen(session: Session, account_id: int, row: CachedMessage, seen: bool) -> None:
    """seen an EINER Cache-Zeile setzen: Ordner-Ungelesen-Zähler nur bei echtem
    Wechsel mitziehen und gegen Sync-Überschreibung sperren (seen_sticky)."""
    if bool(row.seen) != bool(seen):
        _adjust_cached_unseen(session, account_id, row.folder, -1 if seen else 1)
        row.seen = seen
    row.seen_sticky = True
    session.add(row)


def update_flags(session: Session, account_id: int, folder: str, uid: str, *, seen: bool | None = None, flagged: bool | None = None) -> None:
    """Hält den Cache konsistent, wenn der Nutzer selbst Flags ändert.

    Bei einer ECHTEN seen-Änderung wird der Ordner-Ungelesen-Zähler sofort
    mitgezogen (gelesen -> -1, ungelesen -> +1), damit summary()/Badges in Web UND
    App direkt stimmen, statt erst nach dem nächsten Scheduler-Sync."""
    row = session.exec(
        select(CachedMessage).where(
            CachedMessage.account_id == account_id, CachedMessage.folder == folder, CachedMessage.uid == uid
        )
    ).first()
    if not row:
        return
    if seen is not None:
        _set_row_seen(session, account_id, row, seen)
        # Gmail & Co.: dieselbe Mail liegt als KOPIE in weiteren Ordnern (Gmail-
        # Label-Ordner "Alle Nachrichten"/"Wichtig"/"Markiert" enthalten Kopien der
        # INBOX-Mails). Liest man in der INBOX, blieb die Kopie sonst ungelesen und
        # die Mail "kam wieder" bzw. verfälschte den Zähler. Über die Message-ID ALLE
        # Kopien im Konto mitziehen (auch gegen Sync-Überschreibung gesperrt).
        if row.message_id:
            twins = session.exec(
                select(CachedMessage).where(
                    CachedMessage.account_id == account_id,
                    CachedMessage.message_id == row.message_id,
                    CachedMessage.id != row.id,
                )
            ).all()
            for t in twins:
                _set_row_seen(session, account_id, t, seen)
    if flagged is not None:
        row.flagged = flagged
    session.add(row)
    session.commit()


def update_keyword(session: Session, account_id: int, folder: str, uid: str, keyword: str, on: bool) -> None:
    """Cache-Keywords (Labels) einer Mail nachziehen, wenn der Nutzer ein Label
    setzt/entfernt — sonst zeigt die Liste bis zum nächsten Sync das Alte."""
    row = session.exec(
        select(CachedMessage).where(
            CachedMessage.account_id == account_id, CachedMessage.folder == folder, CachedMessage.uid == uid
        )
    ).first()
    if not row:
        return
    kws = [k for k in row.keywords.split() if k]
    if on and keyword not in kws:
        kws.append(keyword)
    elif not on and keyword in kws:
        kws = [k for k in kws if k != keyword]
    row.keywords = " ".join(kws)
    session.add(row)
    session.commit()


def set_flags_bulk(
    session: Session, account_id: int, folder: str, uids: list[str],
    *, seen: bool | None = None, flagged: bool | None = None,
) -> None:
    """Setzt seen/flagged für viele Cache-Zeilen in EINEM UPDATE je Chunk.

    Gechunkt (<=500), weil SQLite die Anzahl Variablen je Query begrenzt (~999)."""
    vals: dict = {}
    if seen is not None:
        vals["seen"] = seen
        vals["seen_sticky"] = True   # Nutzer-Entscheidung → gegen Sync-Überschreibung sperren
    if flagged is not None:
        vals["flagged"] = flagged
    uids = [u for u in uids if u]
    if not vals or not uids:
        return
    # Ungelesen-Zähler-Delta bestimmen, BEVOR das UPDATE laeuft: nur Zeilen zählen,
    # die tatsächlich von/zu "ungelesen" wechseln (gechunkt wie das UPDATE).
    if seen is not None:
        changed = 0
        for i in range(0, len(uids), 500):
            chunk = uids[i:i + 500]
            states = session.exec(
                select(CachedMessage.seen).where(
                    CachedMessage.account_id == account_id,
                    CachedMessage.folder == folder,
                    CachedMessage.uid.in_(chunk),
                )
            ).all()
            changed += sum(1 for s in states if bool(s) != bool(seen))
        if changed:
            _adjust_cached_unseen(session, account_id, folder, -changed if seen else changed)
    for i in range(0, len(uids), 500):
        chunk = uids[i:i + 500]
        session.execute(
            update(CachedMessage)
            .where(
                CachedMessage.account_id == account_id,
                CachedMessage.folder == folder,
                CachedMessage.uid.in_(chunk),
            )
            .values(**vals)
        )
    # Gmail & Co.: Kopien derselben Mails in weiteren Ordnern (Label-Ordner) über die
    # Message-ID mitziehen — sonst bleiben sie ungelesen und der Zähler stimmt nicht.
    if seen is not None:
        _propagate_seen_by_message(session, account_id, folder, uids, seen)
    session.commit()


def _propagate_seen_by_message(
    session: Session, account_id: int, src_folder: str, uids: list[str], seen: bool,
) -> None:
    """Zieht den seen-Status auf alle KOPIEN (gleiche Message-ID) in ANDEREN Ordnern
    des Kontos mit — für Gmail-Label-Ordner ("Alle Nachrichten" usw.). Ohne Commit;
    der Aufrufer committet."""
    mids: set[str] = set()
    for i in range(0, len(uids), 500):
        chunk = uids[i:i + 500]
        rows = session.exec(
            select(CachedMessage.message_id).where(
                CachedMessage.account_id == account_id,
                CachedMessage.folder == src_folder,
                CachedMessage.uid.in_(chunk),
            )
        ).all()
        mids.update(m for m in rows if m)
    if not mids:
        return
    mid_list = list(mids)
    for i in range(0, len(mid_list), 500):
        chunk = mid_list[i:i + 500]
        twins = session.exec(
            select(CachedMessage).where(
                CachedMessage.account_id == account_id,
                CachedMessage.message_id.in_(chunk),
                CachedMessage.folder != src_folder,
            )
        ).all()
        for t in twins:
            _set_row_seen(session, account_id, t, seen)


def remove_uids(session: Session, account_id: int, folder: str, uids: list[str]) -> None:
    """Entfernt Cache-Zeilen HART (nur für serverseitig wirklich verschwundene Mails,
    z. B. 404-Selbstheilung). Für Nutzer-Löschungen/-Verschiebungen siehe hide_uids."""
    if not uids:
        return
    rows = session.exec(
        select(CachedMessage).where(
            CachedMessage.account_id == account_id, CachedMessage.folder == folder, CachedMessage.uid.in_(uids)
        )
    ).all()
    for r in rows:
        session.delete(r)
    session.commit()


def hide_uids(session: Session, account_id: int, folder: str, uids: list[str]) -> None:
    """Blendet Cache-Zeilen aus, statt sie zu entfernen (Nutzer-Löschen/-Verschieben).

    So bleibt die Message-ID/UID im Cache bekannt: ein flatternder Server (web.de),
    der die Mail noch listet, führt NICHT dazu, dass der Sync sie als „neu" wieder
    einfügt (Anti-„gelöschte Mail kommt wieder"). Endgültig entfernt wird die Zeile
    erst über den miss_count, sobald der Server die UID nicht mehr liefert."""
    if not uids:
        return
    rows = session.exec(
        select(CachedMessage).where(
            CachedMessage.account_id == account_id, CachedMessage.folder == folder, CachedMessage.uid.in_(uids)
        )
    ).all()
    for r in rows:
        r.hidden = True
        session.add(r)
    session.commit()


def sync_folder(
    session: Session, account: MailAccount, password: str, folder: str,
    cap: int = _SYNC_CAP, *, store_bodies: bool = True, lock_timeout: float | None = None, op: str = "sync",
) -> dict:
    """Gleicht den Cache eines Ordners mit dem Server ab (Delta-Sync).

    ``store_bodies``: den beim Fetch ohnehin geladenen Volltext gleich als
    ``detail_json`` mit ablegen (siehe _DETAIL_CACHE_MAX). Standard an — nur
    abschalten, wenn bewusst nur Kopfzeilen gepflegt werden sollen."""
    fetch_uids: list[str] = []
    _t0 = time.monotonic()
    with _mailbox(account, password, folder=folder,
                  lock_timeout=lock_timeout, op=op) as box:
        _t_lock = time.monotonic()
        try:
            st = box.folder.status(folder, ["UIDVALIDITY", "MESSAGES", "UNSEEN"])
        except Exception:  # noqa: BLE001
            st = {}
        uidvalidity = int(st.get("UIDVALIDITY", 0) or 0)
        total = int(st.get("MESSAGES", 0) or 0)
        unseen = int(st.get("UNSEEN", 0) or 0)

        fs = session.exec(
            select(FolderSync).where(FolderSync.account_id == account.id, FolderSync.folder == folder)
        ).first()

        cached_rows = session.exec(
            select(CachedMessage).where(CachedMessage.account_id == account.id, CachedMessage.folder == folder)
        ).all()

        # UIDVALIDITY-Wechsel: FRÜHER wurde hier der GANZE Ordner-Cache verworfen.
        # web.de u. a. Cluster melden die UIDVALIDITY aber teils flatterhaft (jede
        # Verbindung landet auf einem anderen, nicht synchronen Knoten) → das hätte
        # den Ordner ständig komplett neu aufgebaut: Lese-Status weg, Mails „kommen
        # wieder", Absender kippt auf „Ich". Darum NICHT mehr wipen — verwaiste UIDs
        # altern über miss_count aus, echte Dubletten fängt die Message-ID ab.

        cached_by_uid = {r.uid: r for r in cached_rows}
        cached_mids = {r.message_id for r in cached_rows if r.message_id}
        server_uids = list(box.uids())            # aufsteigend (alt → neu)
        server_set = set(server_uids)

        # Zuverlässigkeit der Server-Antwort prüfen: Liefert die UID-Suche DEUTLICH
        # weniger UIDs, als der Server selbst als MESSAGES meldet, ist diese Sicht
        # kaputt/partiell (web.de-Cluster liefert je Verbindung eine andere Teilmenge).
        # Dann NICHT prunen — sonst würden gültige Mails fälschlich als „verschwunden"
        # gewertet und die Liste flackerte. So wächst der Cache nur zur Vereinigung
        # aller je gesehenen Teilmengen und schrumpft nie durch einen schlechten Sync.
        reliable = total <= 0 or len(server_set) >= total * 0.5

        # Verschwundene NICHT sofort löschen — erst nach _MISS_LIMIT Syncs in Folge
        # ohne Treffer (Härtung gegen flatternde Server). Wieder aufgetauchte Mails
        # setzen den Zähler zurück.
        for uid, row in list(cached_by_uid.items()):
            if uid in server_set:
                if row.miss_count:
                    row.miss_count = 0
                    session.add(row)
            elif reliable:
                row.miss_count = (row.miss_count or 0) + 1
                if row.miss_count >= _MISS_LIMIT:
                    session.delete(row)
                    del cached_by_uid[uid]
                    if row.message_id:
                        cached_mids.discard(row.message_id)
                else:
                    session.add(row)
            # unzuverlässige Sicht → Mail unangetastet lassen (kein miss_count-Hochzählen).

        # Neue Köpfe holen (neueste zuerst, gedeckelt).
        new_uids = [u for u in server_uids if u not in cached_by_uid]
        fetch_uids = new_uids[-cap:] if cap else new_uids
        if fetch_uids:
            # WICHTIG: KEINE Zwischen-Commits. Alle neuen Köpfe werden nur in der
            # Session vorgemerkt und erst am Ende in EINEM Commit sichtbar → Leser
            # sehen nie einen halb aufgebauten Ordner.
            for msg in box.fetch(AND(uid=",".join(fetch_uids)), mark_seen=False, bulk=50):
                if not msg.uid:
                    continue
                th = thread_headers(msg)
                # Dublette über UIDVALIDITY-/Knoten-Wechsel hinweg vermeiden: gleiche
                # Message-ID schon im Cache (unter anderer UID) → nicht doppelt anlegen.
                mid = th["message_id"]
                if mid and mid in cached_mids:
                    continue
                row = CachedMessage(
                    account_id=account.id, folder=folder, uid=msg.uid,
                    subject=msg.subject or "", from_addr=msg.from_ or "", date_str=msg.date_str or "",
                    sort_date=_to_utc_naive(msg.date), seen=SEEN in msg.flags, flagged=FLAGGED in msg.flags,
                    snippet=_snippet(msg.text or "", msg.html or ""), has_attachments=bool(msg.attachments),
                    message_id=mid, in_reply_to=th["in_reply_to"], refs=th["references"],
                    keywords=" ".join(keywords_of(msg)),
                )
                # Volltext gleich mit ablegen. Die Mail ist an dieser Stelle KOMPLETT
                # geladen (Snippet und Anhang-Flag brauchen den Body) — bisher wurde
                # sie danach weggeworfen und vom Frontend über /messages/prefetch ein
                # ZWEITES Mal geholt. Genau das machte den Erstzugriff auf große
                # Postfächer (Gmail) so zäh: dieselben Daten zweimal über IMAP.
                # Anhang-Payloads landen NICHT in detail_json (nur deren Metadaten),
                # der Eintrag bleibt also klein.
                if store_bodies:
                    try:
                        detail = json.dumps(_detail_dict(msg, account), ensure_ascii=False)
                        if len(detail) <= _DETAIL_CACHE_MAX:
                            row.detail_json = detail
                    except (TypeError, ValueError):
                        pass  # nicht serialisierbar -> Body wird beim Öffnen live geholt
                session.add(row)
                if mid:
                    cached_mids.add(mid)

        # Flags der neuesten gecachten Mails abgleichen — TEUERSTER Teil (Header-
        # Fetch vieler Mails). Gedrosselt: nur, wenn der letzte Sync länger als
        # _FLAG_REFRESH_SECS her ist. Neue/gelöschte Mails oben laufen immer.
        now = dt.datetime.now(dt.timezone.utc)
        prev_sync = _as_utc(fs.last_sync) if fs else None
        do_flags = prev_sync is None or (now - prev_sync).total_seconds() >= _FLAG_REFRESH_SECS
        # Flags NUR von einer VERTRAUENSWÜRDIGEN Server-Sicht übernehmen. Bei einer
        # kaputten/partiellen Antwort (web.de-Cluster) NICHT anfassen — sonst würde
        # eine echte ungelesene Mail fälschlich als gelesen (oder umgekehrt) markiert
        # und wäre in der Liste nicht mehr als ungelesen erkennbar.
        _t_flags = time.monotonic()
        _flag_n = 0
        if do_flags and reliable:
            recent = [u for u in server_uids[-_FLAG_WINDOW:] if u in cached_by_uid]
            _flag_n = len(recent)
            if recent:
                for msg in box.fetch(AND(uid=",".join(recent)), mark_seen=False, headers_only=True, bulk=100):
                    row = cached_by_uid.get(msg.uid or "")
                    if row:
                        # Vom Nutzer selbst gesetzten Gelesen-Status NICHT überschreiben.
                        if not row.seen_sticky:
                            row.seen = SEEN in msg.flags
                        row.flagged = FLAGGED in msg.flags
                        row.keywords = " ".join(keywords_of(msg))
                        # Altbestand ohne Thread-Header (vor dem Update gecacht) einmalig
                        # nachtragen — headers_only liefert Message-ID/References mit.
                        if not row.message_id:
                            th = thread_headers(msg)
                            row.message_id = th["message_id"]
                            row.in_reply_to = th["in_reply_to"]
                            row.refs = th["references"]
                        session.add(row)

        _t_flags_ende = time.monotonic()
        if not fs:
            fs = FolderSync(account_id=account.id, folder=folder)
        fs.uidvalidity = uidvalidity
        fs.total = total
        fs.unseen = unseen
        fs.last_sync = now
        session.add(fs)

    _t_db = time.monotonic()
    # Commit ERST NACH dem Verlassen des _mailbox-Blocks → der (kontospezifische) IMAP-
    # Lock wird nicht mehr während eines evtl. langsamen SQLite-Writes gehalten (kürzere
    # Sperre für parallele Nutzeraktionen auf demselben Konto).
    session.commit()
    # Eine Zeile je Sync im Log: ohne die war bei "Gmail laedt lange" nicht zu
    # unterscheiden, ob die Zeit im Warten auf die Verbindung, im IMAP-Abruf oder
    # in der Datenbank steckt. Sichtbar via `docker logs`.
    _ende = time.monotonic()
    logger.info(
        "Sync fertig (account_id=%s, folder=%s): %d Mails im Ordner, %d neu geholt, "
        "%d Flags geprueft | warten %.1fs, IMAP %.1fs (davon Flags %.1fs), gesamt %.1fs",
        account.id, folder, total, len(fetch_uids), _flag_n,
        _t_lock - _t0, _t_db - _t_lock, _t_flags_ende - _t_flags, _ende - _t0,
    )
    return {
        "total": total, "unseen": unseen, "new": len(fetch_uids), "ok": True,
        # Zeiten in Millisekunden mitliefern: ohne Zugriff auf die Container-Logs
        # ist sonst nicht zu sehen, WO die Zeit steckt. Klein und harmlos.
        "ms": {
            "gesamt": int((_ende - _t0) * 1000),
            "warten": int((_t_lock - _t0) * 1000),
            "imap": int((_t_db - _t_lock) * 1000),
            "flags": int((_t_flags_ende - _t_flags) * 1000),
            "flags_geprueft": _flag_n,
        },
    }
