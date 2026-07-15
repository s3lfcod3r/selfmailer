"""Kontakte: lokales Adressbuch pro User (CRUD).

Externe CardDAV-Synchronisation ist eine spätere Erweiterung.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session, or_, select

from ..birthdays import delete_one, sync_one, sync_user_birthdays
from ..core import jobs
from ..core.db import engine, get_session
from ..dav.vcard import build_vcards
from ..models import Contact, User
from ..schemas import BirthdayCalIn, BirthdayCalOut, ContactCreate, ContactOut, ContactUpdate
from .deps import get_current_user
from .feeds import feed_or_bearer_user

router = APIRouter(prefix="/api/v1/contacts", tags=["contacts"])

# Harte Obergrenze, damit die Kontaktliste nie unbegrenzt viele Zeilen zurückgibt.
_MAX_LIST = 2000


@router.get("/birthday-calendar", response_model=BirthdayCalOut)
def get_birthday_calendar(user: User = Depends(get_current_user)) -> BirthdayCalOut:
    return BirthdayCalOut(dav_account_id=user.bday_cal_account_id, gcal_calendar_id=user.bday_cal_id or "")


@router.put("/birthday-calendar", response_model=dict)
def set_birthday_calendar(
    data: BirthdayCalIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Geburtstage-Kalender setzen (oder leeren) und sofort abgleichen. Bei
    Kalenderwechsel werden die Verknüpfungen geleert, damit die Termine im neuen
    Kalender neu entstehen (alte bleiben im alten Kalender stehen)."""
    changed = (user.bday_cal_account_id != data.dav_account_id) or (user.bday_cal_id != (data.gcal_calendar_id or ""))
    if changed:
        for c in session.exec(select(Contact).where(Contact.user_id == user.id)).all():
            if c.bday_event_id:
                c.bday_event_id = ""
                session.add(c)
    user.bday_cal_account_id = data.dav_account_id
    user.bday_cal_id = data.gcal_calendar_id or ""
    session.add(user)
    session.commit()
    if not user.bday_cal_account_id or not user.bday_cal_id:
        return {"ok": True, "reason": "Geburtstage-Kalender deaktiviert"}
    return sync_user_birthdays(session, user)


@router.post("/birthdays/sync", response_model=dict)
def sync_birthdays_now(
    background: bool = False,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Alle Geburtstage jetzt mit dem Google-Kalender abgleichen.

    ``background=true`` läuft (bei vielen Kontakten langsam, viele Google-Calls) in
    einem Hintergrund-Job mit EIGENER DB-Session und liefert sofort ``{job_id}``
    (Status via ``GET /mail/jobs/{job_id}``); sonst synchron."""
    if not background:
        return sync_user_birthdays(session, user)

    # Der Job läuft nach dem Request weiter -> die Request-Session ist dann zu.
    # Deshalb im Job eine frische Session öffnen und den User neu laden.
    uid = user.id

    def _run() -> dict:
        with Session(engine) as s:
            u = s.get(User, uid)
            if u is None:
                return {"ok": False, "reason": "User nicht gefunden"}
            return sync_user_birthdays(s, u)

    job_id = jobs.create_job(uid, "birthdays")
    jobs.start(job_id, _run)
    return {"job_id": job_id}


@router.get("/export.vcf")
def export_vcf(
    user: User = Depends(feed_or_bearer_user),
    session: Session = Depends(get_session),
) -> Response:
    """Liefert alle Kontakte des Users als vCard-Datei.

    Auth über ``?token=`` (Abo) oder Bearer (Direkt-Download).
    """
    stmt = select(Contact).where(Contact.user_id == user.id).order_by(
        Contact.last_name, Contact.first_name
    )
    body = build_vcards(session.exec(stmt).all())
    return Response(
        content=body,
        media_type="text/vcard; charset=utf-8",
        headers={"Content-Disposition": 'inline; filename="selfmailer.vcf"'},
    )


def _owned(contact_id: int, user: User, session: Session) -> Contact:
    ct = session.get(Contact, contact_id)
    if ct is None or ct.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kontakt nicht gefunden")
    return ct


@router.get("", response_model=list[ContactOut])
def list_contacts(
    q: str = "",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[Contact]:
    stmt = select(Contact).where(Contact.user_id == user.id)
    if q:
        # %/_ (und den Escape-Char selbst) escapen, damit Nutzereingaben nicht als
        # LIKE-Wildcards wirken ("%" = alle Kontakte, teure Scans / Info-Leak).
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{esc}%"
        stmt = stmt.where(
            or_(
                Contact.first_name.ilike(like, escape="\\"),
                Contact.last_name.ilike(like, escape="\\"),
                Contact.email.ilike(like, escape="\\"),
                Contact.organization.ilike(like, escape="\\"),
            )
        )
    stmt = stmt.order_by(Contact.last_name, Contact.first_name).limit(_MAX_LIST)
    return list(session.exec(stmt).all())


@router.post("", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
def create_contact(
    data: ContactCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Contact:
    ct = Contact(user_id=user.id, **data.model_dump())
    session.add(ct)
    session.commit()
    session.refresh(ct)
    if ct.birthday:
        sync_one(session, user, ct)   # Geburtstag gleich in den Kalender (falls aktiv)
    return ct


@router.patch("/{contact_id}", response_model=ContactOut)
def update_contact(
    contact_id: int,
    data: ContactUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Contact:
    ct = _owned(contact_id, user, session)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(ct, field, value)
    ct.updated_at = dt.datetime.now(dt.timezone.utc)
    session.add(ct)
    session.commit()
    session.refresh(ct)
    sync_one(session, user, ct)       # Geburtstag im Kalender nachziehen (falls aktiv)
    return ct


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(
    contact_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    ct = _owned(contact_id, user, session)
    delete_one(session, user, ct)     # Geburtstags-Termin im Kalender mit entfernen
    session.delete(ct)
    session.commit()
