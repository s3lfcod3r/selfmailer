"""Externe CalDAV/CardDAV-Konten: Verwaltung + read-only Pull-Sync.

Client-Proxy-Modell (Variante B aus KONZEPT.md): SelfMailer bindet fremde
DAV-Collections an und spiegelt sie in den lokalen Store. Der lokale Store
bleibt die Quelle fuer WebUI/Export; importierte Eintraege tragen
dav_account_id + external_uid und werden bei jedem Sync abgeglichen.

Zugangsdaten werden Fernet-verschluesselt gespeichert (secret_enc), analog zu
den Mailkonten. Klartext existiert nur transient waehrend des Sync.
"""
from __future__ import annotations

import datetime as dt

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..core.crypto import decrypt, encrypt
from ..core.db import get_session
from ..dav import client
from ..dav.ical import parse_events
from ..dav.vcard import parse_vcards
from ..models import CalendarEvent, Contact, DavAccount, DavKind, User
from ..schemas import DavAccountCreate, DavAccountOut, SyncResult
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/dav", tags=["dav"])


def _owned(account_id: int, user: User, session: Session) -> DavAccount:
    acc = session.get(DavAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DAV-Konto nicht gefunden")
    return acc


@router.get("/accounts", response_model=list[DavAccountOut])
def list_dav_accounts(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> list[DavAccount]:
    return list(session.exec(select(DavAccount).where(DavAccount.user_id == user.id)).all())


@router.post("/accounts", response_model=DavAccountOut, status_code=status.HTTP_201_CREATED)
def add_dav_account(
    data: DavAccountCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> DavAccount:
    acc = DavAccount(
        user_id=user.id,
        kind=data.kind,
        label=data.label or data.url,
        url=data.url,
        username=data.username,
        secret_enc=encrypt(data.password),
    )
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dav_account(
    account_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    acc = _owned(account_id, user, session)
    # Importierte lokale Eintraege dieses Kontos mitloeschen.
    for ev in session.exec(
        select(CalendarEvent).where(CalendarEvent.dav_account_id == account_id)
    ).all():
        session.delete(ev)
    for ct in session.exec(
        select(Contact).where(Contact.dav_account_id == account_id)
    ).all():
        session.delete(ct)
    session.delete(acc)
    session.commit()


def _sync_events(
    acc: DavAccount, resources: list[tuple[str, str]], user: User, session: Session
) -> SyncResult:
    seen: set[str] = set()
    imported = updated = 0
    for _href, body in resources:
        for ev in parse_events(body):
            uid = ev["uid"]
            if not uid:
                continue
            seen.add(uid)
            existing = session.exec(
                select(CalendarEvent).where(
                    CalendarEvent.dav_account_id == acc.id,
                    CalendarEvent.external_uid == uid,
                )
            ).first()
            target = existing or CalendarEvent(
                user_id=user.id,
                dav_account_id=acc.id,
                external_uid=uid,
                start=ev["start"],
                end=ev["end"],
            )
            target.title = ev["title"]
            target.description = ev["description"]
            target.location = ev["location"]
            target.start = ev["start"]
            target.end = ev["end"]
            target.all_day = ev["all_day"]
            target.updated_at = dt.datetime.now(dt.timezone.utc)
            session.add(target)
            updated += 1 if existing else 0
            imported += 0 if existing else 1
    removed = _prune(CalendarEvent, acc.id, seen, session)
    return SyncResult(ok=True, imported=imported, updated=updated, removed=removed)


def _sync_contacts(
    acc: DavAccount, resources: list[tuple[str, str]], user: User, session: Session
) -> SyncResult:
    seen: set[str] = set()
    imported = updated = 0
    for _href, body in resources:
        for card in parse_vcards(body):
            uid = card["uid"]
            if not uid:
                continue
            seen.add(uid)
            existing = session.exec(
                select(Contact).where(
                    Contact.dav_account_id == acc.id, Contact.external_uid == uid
                )
            ).first()
            target = existing or Contact(
                user_id=user.id, dav_account_id=acc.id, external_uid=uid
            )
            target.first_name = card["first_name"]
            target.last_name = card["last_name"]
            target.email = card["email"]
            target.phone = card["phone"]
            target.organization = card["organization"]
            target.notes = card["notes"]
            target.updated_at = dt.datetime.now(dt.timezone.utc)
            session.add(target)
            updated += 1 if existing else 0
            imported += 0 if existing else 1
    removed = _prune(Contact, acc.id, seen, session)
    return SyncResult(ok=True, imported=imported, updated=updated, removed=removed)


def _prune(model, account_id: int, seen: set[str], session: Session) -> int:
    """Loescht lokale Eintraege dieses DAV-Kontos, deren UID nicht mehr in der
    Quelle vorkommt."""
    removed = 0
    for row in session.exec(select(model).where(model.dav_account_id == account_id)).all():
        if row.external_uid not in seen:
            session.delete(row)
            removed += 1
    return removed


@router.post("/accounts/{account_id}/sync", response_model=SyncResult)
def sync_dav_account(
    account_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SyncResult:
    """Holt die externe Collection und gleicht sie in den lokalen Store ab."""
    acc = _owned(account_id, user, session)
    try:
        resources = client.fetch_collection(acc.url, acc.username, decrypt(acc.secret_enc))
    except httpx.HTTPError as exc:
        acc.last_status = f"Fehler: {exc}"
        session.add(acc)
        session.commit()
        return SyncResult(ok=False, error=str(exc))

    if acc.kind == DavKind.caldav:
        result = _sync_events(acc, resources, user, session)
    else:
        result = _sync_contacts(acc, resources, user, session)

    acc.last_sync = dt.datetime.now(dt.timezone.utc)
    acc.last_status = "ok"
    session.add(acc)
    session.commit()
    return result
