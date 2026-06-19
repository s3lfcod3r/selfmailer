"""Kontakte: lokales Adressbuch pro User (CRUD).

Externe CardDAV-Synchronisation ist eine spaetere Erweiterung.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session, or_, select

from ..core.db import get_session
from ..dav.vcard import build_vcards
from ..models import Contact, User
from ..schemas import ContactCreate, ContactOut, ContactUpdate
from .deps import get_current_user
from .feeds import feed_or_bearer_user

router = APIRouter(prefix="/api/v1/contacts", tags=["contacts"])


@router.get("/export.vcf")
def export_vcf(
    user: User = Depends(feed_or_bearer_user),
    session: Session = Depends(get_session),
) -> Response:
    """Liefert alle Kontakte des Users als vCard-Datei.

    Auth ueber ``?token=`` (Abo) oder Bearer (Direkt-Download).
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
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Contact.first_name.ilike(like),
                Contact.last_name.ilike(like),
                Contact.email.ilike(like),
                Contact.organization.ilike(like),
            )
        )
    stmt = stmt.order_by(Contact.last_name, Contact.first_name)
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
    return ct


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(
    contact_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    ct = _owned(contact_id, user, session)
    session.delete(ct)
    session.commit()
