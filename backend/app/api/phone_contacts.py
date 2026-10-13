"""Selected one-way phone imports with stable links, duplicate preview and conflict protection."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from ..core.db import get_session
from ..models import Contact, PhoneContactLink, User
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/contacts/phone", tags=["contacts"])
Small = Annotated[str, Field(max_length=254)]
FIELDS = ("first_name", "last_name", "email", "phone", "mobile", "work_phone", "organization", "extra_emails", "extra_phones")


class PhoneItem(BaseModel):
    source_key: str = Field(min_length=1, max_length=512)
    first_name: str = Field(default="", max_length=200)
    last_name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=80)
    mobile: str = Field(default="", max_length=80)
    work_phone: str = Field(default="", max_length=80)
    organization: str = Field(default="", max_length=300)
    extra_emails: list[Small] = Field(default_factory=list, max_length=20)
    extra_phones: list[Small] = Field(default_factory=list, max_length=20)
    expected_digest: str | None = Field(default=None, max_length=64)


class PhoneRequest(BaseModel):
    device_id: str = Field(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    contacts: list[PhoneItem] = Field(min_length=1, max_length=200)
    linked_only: bool = False

    @model_validator(mode="after")
    def unique_sources(self):
        if len({v.source_key for v in self.contacts}) != len(self.contacts):
            raise ValueError("Doppelte Handy-Kontaktidentität")
        return self


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def snapshot(contact: Contact) -> str:
    return digest({"id": contact.id, "created": contact.created_at, **{f: getattr(contact, f) for f in FIELDS}})


def normalized_phone(value: str) -> str:
    value = re.sub(r"[^0-9+]", "", value.strip())
    return "+" + value[2:] if value.startswith("00") else value


def identities(contact) -> set[str]:
    keys = {"email:" + e.strip().casefold() for e in [contact.email, *contact.extra_emails] if e.strip()}
    for raw in [contact.phone, contact.mobile, contact.work_phone, *contact.extra_phones]:
        value = normalized_phone(raw)
        if len(re.sub(r"\D", "", value)) >= 7:
            keys.add("phone:" + value)
    return keys


def name_key(contact) -> str:
    return " ".join((contact.first_name + " " + contact.last_name).casefold().split())


def run_import(data: PhoneRequest, user: User, session: Session, *, apply: bool) -> dict:
    # Serialize imports and ordinary concurrent writes before taking their snapshots.
    if apply:
        session.exec(text("BEGIN IMMEDIATE"))
    contacts = list(session.exec(select(Contact).options(load_only(
        Contact.id, Contact.user_id, Contact.created_at, Contact.updated_at, Contact.dav_account_id,
        *(getattr(Contact, field) for field in FIELDS),
    )).where(Contact.user_id == user.id)).all())
    links = {v.source_key: v for v in session.exec(select(PhoneContactLink).where(
        PhoneContactLink.user_id == user.id, PhoneContactLink.device_id == data.device_id)).all()}
    by_id = {v.id: v for v in contacts}
    by_key: dict[str, set[int]] = {}
    for ct in contacts:
        for key in identities(ct):
            by_key.setdefault(key, set()).add(ct.id)
    input_keys: set[str] = set()
    items = []
    linked = []
    changed = 0
    for incoming in data.contacts:
        values = {f: getattr(incoming, f) for f in FIELDS}
        keys = identities(incoming)
        link = links.get(incoming.source_key)
        target = by_id.get(link.contact_id) if link else None
        status = "unchanged"
        changes = {}
        conflicts = []
        source_changed = set()
        retained = []
        if link and target is not None and target.dav_account_id is not None:
            status = "external"
        elif link and target is None:
            status = "server_deleted"
        elif not link and data.linked_only:
            status = "not_linked"
        elif not link and keys & input_keys:
            status = "duplicate_input"
        elif not link:
            matches = set().union(*(by_key.get(key, set()) for key in keys))
            if len(matches) > 1:
                status = "ambiguous"
            elif matches:
                target = by_id[next(iter(matches))]
                status = "external" if target.dav_account_id else "match"
                if status == "match":
                    changes = {f: v for f, v in values.items() if v and not getattr(target, f)}
                    retained = [f for f, v in values.items() if v and getattr(target, f) and getattr(target, f) != v]
                    emails = [*target.extra_emails, *incoming.extra_emails]
                    if incoming.email and incoming.email.casefold() != target.email.casefold():
                        emails.append(incoming.email)
                    final_email = changes.get("email", target.email)
                    emails = list({v.strip().casefold(): v for v in emails
                                   if v.strip().casefold() != final_email.strip().casefold()}.values())
                    phones = [*target.extra_phones, *incoming.extra_phones]
                    phones += [v for v in (incoming.phone, incoming.mobile, incoming.work_phone)
                               if v and v not in (target.phone, target.mobile, target.work_phone)]
                    primary = {normalized_phone(changes.get(f, getattr(target, f))) for f in ("phone", "mobile", "work_phone")}
                    phones = list({normalized_phone(v): v for v in phones if normalized_phone(v) not in primary}.values())
                    if emails != target.extra_emails:
                        changes["extra_emails"] = emails
                    if phones != target.extra_phones:
                        changes["extra_phones"] = phones
                    if len(emails) > 200 or len(phones) > 200:
                        status = "conflict"
                        conflicts = ["extra_emails", "extra_phones"]
            elif name_key(incoming) and any(name_key(c) == name_key(incoming) for c in contacts):
                status = "possible_duplicate"
            elif not keys and not name_key(incoming):
                status = "empty"
            else:
                status = "new"
        else:
            old_source = json.loads(link.source_hashes)
            old_server = json.loads(link.server_hashes)
            for field, value in values.items():
                if digest(value) != old_source.get(field):
                    source_changed.add(field)
                    current = getattr(target, field)
                    if (digest(current) != old_server.get(field) or old_source.get(field) != old_server.get(field)) and current != value:
                        conflicts.append(field)
                    elif current != value:
                        changes[field] = value
            status = "conflict" if conflicts else "update" if changes else "unchanged"
        expected = snapshot(target) if target else ""
        safe = status in {"new", "match", "update", "unchanged"}
        if apply and incoming.expected_digest is not None and incoming.expected_digest != expected:
            raise HTTPException(409, "Import-Vorschau ist veraltet. Neu laden; keine Änderungen übernommen.")
        if safe and apply:
            if target is None:
                target = Contact(user_id=user.id, **values)
                session.add(target)
                session.flush()
                by_id[target.id] = target
                contacts.append(target)
                for key in keys:
                    by_key.setdefault(key, set()).add(target.id)
            else:
                for field, value in changes.items():
                    setattr(target, field, value)
                if changes:
                    target.updated_at = dt.datetime.now(dt.timezone.utc)
                    session.add(target)
            new_link = link is None
            if new_link:
                link = PhoneContactLink(user_id=user.id, device_id=data.device_id, source_key=incoming.source_key, contact_id=target.id)
            link.source_hashes = json.dumps({f: digest(v) for f, v in values.items()})
            baseline = {} if new_link else json.loads(link.server_hashes)
            for field in FIELDS if new_link else source_changed:
                baseline[field] = digest(getattr(target, field))
            link.server_hashes = json.dumps(baseline)
            session.add(link)
            linked.append(incoming.source_key)
            if status in {"new", "update"} or changes:
                changed += 1
        input_keys.update(keys)
        items.append({"source_key": incoming.source_key, "status": status, "contact_id": target.id if target else None,
                      "expected_digest": expected, "changes": list(changes), "conflicts": conflicts, "retained": retained,
                      "current_values": {f: getattr(target, f) for f in set(conflicts + retained)} if target else {},
                      "source_values": {f: values[f] for f in set(conflicts + retained)}})
    if apply:
        session.commit()
    return {"items": items, "changed": changed, "linked_source_keys": linked,
            "counts": {key: sum(v["status"] == key for v in items) for key in {v["status"] for v in items}}}


@router.post("/preview")
def preview(data: PhoneRequest, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict:
    return run_import(data, user, session, apply=False)


@router.post("/import")
def import_phone(data: PhoneRequest, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict:
    return run_import(data, user, session, apply=True)
