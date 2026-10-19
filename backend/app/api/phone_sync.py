"""Optimistic, owner-scoped writes for explicitly paired two-way contact sync."""
from __future__ import annotations
import datetime as dt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import load_only
from sqlmodel import Session, select
from ..core.db import get_session
from ..models import Contact, User
from .deps import get_current_user
from .phone_contacts import identities, name_key

router = APIRouter(prefix="/api/v1/contacts/phone/sync", tags=["contacts"])
FIELDS = ("first_name", "last_name", "email", "phone", "mobile", "work_phone", "organization", "extra_emails", "extra_phones",
          "title", "website", "street", "postal_code", "city", "country", "notes", "birthday")


class SyncValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: str = Field(default="", max_length=200)
    last_name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=80)
    mobile: str = Field(default="", max_length=80)
    work_phone: str = Field(default="", max_length=80)
    organization: str = Field(default="", max_length=300)
    extra_emails: list[str] = Field(default_factory=list, max_length=200)
    extra_phones: list[str] = Field(default_factory=list, max_length=200)
    title: str = Field(default="", max_length=300)
    website: str = Field(default="", max_length=2000)
    street: str = Field(default="", max_length=1000)
    postal_code: str = Field(default="", max_length=80)
    city: str = Field(default="", max_length=300)
    country: str = Field(default="", max_length=300)
    notes: str = Field(default="", max_length=80000)
    birthday: dt.date | None = None

    @field_validator("birthday", mode="before")
    @classmethod
    def empty_date(cls, value):
        return None if value == "" else value

    @field_validator("extra_emails", "extra_phones")
    @classmethod
    def bound_items(cls, values):
        if any(len(v) > 254 for v in values):
            raise ValueError("Kontaktangabe zu lang")
        return values


class SyncWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_created_at: dt.datetime
    expected_updated_at: dt.datetime
    values: SyncValues


def naive(value: dt.datetime) -> dt.datetime:
    return value.astimezone(dt.timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def owned(session: Session, user: User, contact_id: int) -> Contact:
    contact = session.exec(select(Contact).options(load_only(
        Contact.id, Contact.user_id, Contact.created_at, Contact.updated_at, Contact.dav_account_id,
        *(getattr(Contact, key) for key in FIELDS),
    )).where(Contact.id == contact_id, Contact.user_id == user.id)).first()
    if contact is None:
        raise HTTPException(404, "Kontakt nicht gefunden")
    return contact


def result(contact: Contact) -> dict:
    return {"id": contact.id, "created_at": contact.created_at.isoformat(), "updated_at": contact.updated_at.isoformat(),
            "read_only": contact.dav_account_id is not None,
            "values": {key: (getattr(contact, key).isoformat() if key == "birthday" and contact.birthday else
                             "" if key == "birthday" else getattr(contact, key)) for key in FIELDS}}


def duplicate(session: Session, user: User, contact: Contact, values) -> bool:
    others = session.exec(select(Contact).options(load_only(
        Contact.id, Contact.first_name, Contact.last_name, Contact.email, Contact.phone, Contact.mobile,
        Contact.work_phone, Contact.organization, Contact.extra_emails, Contact.extra_phones,
    )).where(Contact.user_id == user.id, Contact.id != contact.id)).all()
    keys = identities(values)
    return any(keys & identities(other) for other in others) or (
        bool(name_key(values)) and any(name_key(other) == name_key(values) for other in others))


@router.get("/capabilities")
def capabilities(user: User = Depends(get_current_user)) -> dict:
    return {"protocol": 1, "fields": list(FIELDS), "contact_deletions": False}


@router.get("/{contact_id}")
def get_pair(contact_id: int, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict:
    contact = owned(session, user, contact_id)
    return {**result(contact), "duplicate": duplicate(session, user, contact, contact)}


@router.post("/{contact_id}")
def write_pair(contact_id: int, data: SyncWrite, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict:
    session.exec(text("BEGIN IMMEDIATE"))
    contact = owned(session, user, contact_id)
    if contact.dav_account_id is not None:
        raise HTTPException(409, "Externes Adressbuch ist schreibgeschützt")
    if naive(contact.created_at) != naive(data.expected_created_at) or naive(contact.updated_at) != naive(data.expected_updated_at):
        raise HTTPException(409, "Kontaktidentität oder Bearbeitungsstand geändert. Neu prüfen.")
    if duplicate(session, user, contact, data.values):
        raise HTTPException(409, "Andere Kontakte mit gleichen Identitäten oder gleichem Namen. Duplikat prüfen.")
    changed = False
    for key, value in data.values.model_dump().items():
        if getattr(contact, key) != value:
            setattr(contact, key, value)
            changed = True
    if changed:
        contact.updated_at = dt.datetime.now(dt.timezone.utc)
        session.add(contact)
    session.commit()
    session.refresh(contact, attribute_names=["id", "user_id", "created_at", "updated_at", "dav_account_id", *FIELDS])
    return result(contact)
