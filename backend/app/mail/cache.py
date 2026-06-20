"""Lokaler Mail-Cache (DB) fuer schnelle Listenanzeige + Delta-Sync.

Idee (Thunderbird-Stil): Die Liste eines Ordners kommt SOFORT aus der SQLite-DB.
Ein Sync holt vom IMAP-Server nur die NEUEN Mails (Koepfe), entfernt geloeschte
und gleicht Flags ab. Der Cache ist reine Beschleunigung — schlaegt etwas fehl,
faellt der Aufrufer auf den Live-Abruf zurueck.
"""
from __future__ import annotations

import datetime as dt

from imap_tools import AND
from sqlmodel import Session, select

from ..models import CachedMessage, FolderSync, MailAccount
from .imap import FLAGGED, SEEN, _mailbox, _snippet

# Obergrenze, wie viele (neueste) Mail-Koepfe pro Sync nachgeladen werden.
# Bewusst nicht zu hoch: ein Lauf bleibt zeitlich beschaenkt; sehr grosse Ordner
# fuellen sich ueber mehrere Syncs. Zwischen-Commits sichern Teilfortschritt.
_SYNC_CAP = 1000
_COMMIT_EVERY = 200
# Flag-Abgleich nur fuer die neuesten N UIDs (dort aendern sich Flags am ehesten).
_FLAG_WINDOW = 500


def _to_dict(r: CachedMessage) -> dict:
    return {
        "uid": r.uid, "subject": r.subject, "from": r.from_addr, "date": r.date_str,
        "seen": r.seen, "flagged": r.flagged, "snippet": r.snippet,
        "has_attachments": r.has_attachments,
    }


def has_cache(session: Session, account_id: int, folder: str) -> bool:
    return session.exec(
        select(FolderSync).where(FolderSync.account_id == account_id, FolderSync.folder == folder)
    ).first() is not None


def read_messages(session: Session, account_id: int, folder: str, limit: int = 50, offset: int = 0) -> list[dict]:
    rows = session.exec(
        select(CachedMessage)
        .where(CachedMessage.account_id == account_id, CachedMessage.folder == folder)
        .order_by(CachedMessage.sort_date.desc(), CachedMessage.id.desc())
        .offset(offset).limit(limit)
    ).all()
    return [_to_dict(r) for r in rows]


def folder_uids(session: Session, account_id: int, folder: str) -> list[str]:
    """Alle gecachten UIDs eines Ordners (neueste zuerst) — fuer "Alle auswaehlen".

    Bewusst nur die UIDs (kein Body/Snippet), damit das Selektieren ueber alle
    Seiten hinweg billig bleibt. Begrenzt durch die Cache-Tiefe (sync cap).
    """
    rows = session.exec(
        select(CachedMessage.uid)
        .where(CachedMessage.account_id == account_id, CachedMessage.folder == folder)
        .order_by(CachedMessage.sort_date.desc(), CachedMessage.id.desc())
    ).all()
    return [u for u in rows if u]


def read_counts(session: Session, account_id: int) -> dict[str, FolderSync]:
    rows = session.exec(select(FolderSync).where(FolderSync.account_id == account_id)).all()
    return {r.folder: r for r in rows}


def update_flags(session: Session, account_id: int, folder: str, uid: str, *, seen: bool | None = None, flagged: bool | None = None) -> None:
    """Haelt den Cache konsistent, wenn der Nutzer selbst Flags aendert."""
    row = session.exec(
        select(CachedMessage).where(
            CachedMessage.account_id == account_id, CachedMessage.folder == folder, CachedMessage.uid == uid
        )
    ).first()
    if not row:
        return
    if seen is not None:
        row.seen = seen
    if flagged is not None:
        row.flagged = flagged
    session.add(row)
    session.commit()


def remove_uids(session: Session, account_id: int, folder: str, uids: list[str]) -> None:
    """Entfernt Cache-Zeilen (nach Loeschen/Verschieben durch den Nutzer)."""
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


def sync_folder(session: Session, account: MailAccount, password: str, folder: str, cap: int = _SYNC_CAP) -> dict:
    """Gleicht den Cache eines Ordners mit dem Server ab (Delta-Sync)."""
    fetch_uids: list[str] = []
    with _mailbox(account, password, folder=folder) as box:
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

        # UIDVALIDITY-Wechsel → kompletten Ordner-Cache verwerfen.
        if fs and fs.uidvalidity and uidvalidity and fs.uidvalidity != uidvalidity:
            for r in cached_rows:
                session.delete(r)
            session.commit()
            cached_rows = []

        cached_by_uid = {r.uid: r for r in cached_rows}
        server_uids = list(box.uids())            # aufsteigend (alt → neu)
        server_set = set(server_uids)

        # Geloeschte raus.
        for uid, row in list(cached_by_uid.items()):
            if uid not in server_set:
                session.delete(row)
                del cached_by_uid[uid]

        # Neue Koepfe holen (neueste zuerst, gedeckelt).
        new_uids = [u for u in server_uids if u not in cached_by_uid]
        fetch_uids = new_uids[-cap:] if cap else new_uids
        if fetch_uids:
            added = 0
            for msg in box.fetch(AND(uid=",".join(fetch_uids)), mark_seen=False, bulk=50):
                if not msg.uid:
                    continue
                session.add(CachedMessage(
                    account_id=account.id, folder=folder, uid=msg.uid,
                    subject=msg.subject or "", from_addr=msg.from_ or "", date_str=msg.date_str or "",
                    sort_date=msg.date, seen=SEEN in msg.flags, flagged=FLAGGED in msg.flags,
                    snippet=_snippet(msg.text or "", msg.html or ""), has_attachments=bool(msg.attachments),
                ))
                added += 1
                if added % _COMMIT_EVERY == 0:  # Teilfortschritt sichern
                    session.commit()

        # Flags der neuesten bereits gecachten Mails abgleichen (leicht, kein Body).
        recent = [u for u in server_uids[-_FLAG_WINDOW:] if u in cached_by_uid]
        if recent:
            for msg in box.fetch(AND(uid=",".join(recent)), mark_seen=False, headers_only=True, bulk=100):
                row = cached_by_uid.get(msg.uid or "")
                if row:
                    row.seen = SEEN in msg.flags
                    row.flagged = FLAGGED in msg.flags
                    session.add(row)

        if not fs:
            fs = FolderSync(account_id=account.id, folder=folder)
        fs.uidvalidity = uidvalidity
        fs.total = total
        fs.unseen = unseen
        fs.last_sync = dt.datetime.now(dt.timezone.utc)
        session.add(fs)
        session.commit()

    return {"total": total, "unseen": unseen, "new": len(fetch_uids), "ok": True}
