"""Geburtstage aus den Kontakten als jährliche Termine in einen Google-Kalender.

Pro Kontakt mit Geburtstag wird ein ganztägiger, jährlich wiederkehrender Termin
im gewählten Kalender des Users angelegt und gepflegt; ``Contact.bday_event_id``
verknüpft Kontakt ↔ Google-Termin. Best-effort: ein Fehler bei einem Kontakt
kippt den Lauf nicht.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlmodel import Session, select

from .core.crypto import decrypt
from .dav import google
from .models import Contact, DavAccount, DavKind, User

logger = logging.getLogger(__name__)


def _name(c: Contact) -> str:
    n = " ".join(p for p in (c.first_name, c.last_name) if p).strip()
    return n or c.organization or c.email or "Kontakt"


def _bday_event(c: Contact) -> dict:
    b = c.birthday  # dt.date
    base = dt.datetime(b.year if b and b.year > 1900 else 2000, b.month, b.day)
    return {
        "title": f"🎂 {_name(c)}",
        "description": "",
        "location": "",
        "start": base, "end": base, "all_day": True,
        "recurrence": ["RRULE:FREQ=YEARLY"],
        "transparency": "transparent",   # blockiert die Zeit nicht
    }


def _token_for(session: Session, user: User) -> tuple[str, str] | None:
    """(access_token, calendar_id) für den Geburtstage-Kalender des Users, oder None."""
    if not user.bday_cal_account_id or not user.bday_cal_id:
        return None
    acc = session.get(DavAccount, user.bday_cal_account_id)
    if acc is None or acc.user_id != user.id or acc.kind != DavKind.gcal:
        return None
    try:
        tok = google.access_token(
            acc.oauth_client_id, decrypt(acc.oauth_secret_enc), decrypt(acc.oauth_refresh_enc)
        )
    except Exception:  # noqa: BLE001
        return None
    return tok, user.bday_cal_id


def sync_one(session: Session, user: User, contact: Contact) -> None:
    """Einen Kontakt-Geburtstag im Kalender anlegen/aktualisieren/löschen
    (best-effort; Fehler werden geloggt, nie geworfen)."""
    tc = _token_for(session, user)
    if tc is None:
        return
    tok, cal = tc
    try:
        if contact.birthday:
            ev = _bday_event(contact)
            if contact.bday_event_id:
                google.patch_event(tok, cal, contact.bday_event_id, ev)
            else:
                contact.bday_event_id = google.create_event(tok, cal, ev)
                session.add(contact)
                session.commit()
        elif contact.bday_event_id:
            google.delete_event(tok, cal, contact.bday_event_id)
            contact.bday_event_id = ""
            session.add(contact)
            session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("Geburtstags-Sync (einzeln) Kontakt %s fehlgeschlagen", contact.id, exc_info=True)


def delete_one(session: Session, user: User, contact: Contact) -> None:
    """Geburtstags-Termin eines zu löschenden Kontakts aus dem Kalender entfernen."""
    tc = _token_for(session, user)
    if tc is None or not contact.bday_event_id:
        return
    tok, cal = tc
    try:
        google.delete_event(tok, cal, contact.bday_event_id)
    except Exception:  # noqa: BLE001
        logger.warning("Geburtstags-Termin löschen (Kontakt %s) fehlgeschlagen", contact.id, exc_info=True)


def sync_user_birthdays(session: Session, user: User) -> dict:
    """Gleicht alle Geburtstage des Users mit dem gewählten Google-Kalender ab."""
    if not user.bday_cal_account_id or not user.bday_cal_id:
        return {"ok": False, "reason": "kein Kalender gewählt"}
    acc = session.get(DavAccount, user.bday_cal_account_id)
    if acc is None or acc.user_id != user.id or acc.kind != DavKind.gcal:
        return {"ok": False, "reason": "Kalender-Konto ungültig"}
    try:
        tok = google.access_token(
            acc.oauth_client_id, decrypt(acc.oauth_secret_enc), decrypt(acc.oauth_refresh_enc)
        )
    except Exception:  # noqa: BLE001
        logger.warning("Geburtstags-Sync: Token-Fehler (user=%s)", user.id, exc_info=True)
        return {"ok": False, "reason": "Google-Zugang fehlgeschlagen"}

    cal = user.bday_cal_id
    contacts = list(session.exec(select(Contact).where(Contact.user_id == user.id)).all())
    created = updated = removed = 0
    for c in contacts:
        try:
            if c.birthday:
                ev = _bday_event(c)
                if c.bday_event_id:
                    google.patch_event(tok, cal, c.bday_event_id, ev)
                    updated += 1
                else:
                    c.bday_event_id = google.create_event(tok, cal, ev)
                    session.add(c)
                    created += 1
            elif c.bday_event_id:
                google.delete_event(tok, cal, c.bday_event_id)
                c.bday_event_id = ""
                session.add(c)
                removed += 1
        except Exception:  # noqa: BLE001 - ein Kontakt darf den Lauf nicht kippen
            logger.warning("Geburtstags-Sync: Kontakt %s fehlgeschlagen", c.id, exc_info=True)
    session.commit()
    return {"ok": True, "created": created, "updated": updated, "removed": removed}
