"""Externe CalDAV/CardDAV-Konten: Verwaltung + read-only Pull-Sync.

Client-Proxy-Modell (Variante B aus KONZEPT.md): SelfMailer bindet fremde
DAV-Collections an und spiegelt sie in den lokalen Store. Der lokale Store
bleibt die Quelle für WebUI/Export; importierte Einträge tragen
dav_account_id + external_uid und werden bei jedem Sync abgeglichen.

Zugangsdaten werden Fernet-verschlüsselt gespeichert (secret_enc), analog zu
den Mailkonten. Klartext existiert nur transient während des Sync.
"""
from __future__ import annotations

import datetime as dt
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..core.crypto import decrypt, encrypt
from ..core.db import get_session
from ..dav import client, google
from ..dav.client import DavUrlError
from ..dav.ical import parse_events
from ..dav.vcard import parse_vcards
from ..models import CalendarEvent, Contact, DavAccount, DavKind, User
from ..schemas import (
    DavAccountCreate,
    DavAccountOut,
    DavAccountUpdate,
    DavDiscoverRequest,
    DiscoveredCollection,
    GcalCalendarOut,
    GoogleCalCreate,
    SyncResult,
)
from .deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dav", tags=["dav"])


def gcal_token(acc: DavAccount) -> str:
    """Mintet aus den gespeicherten OAuth-Daten eines gcal-Kontos ein frisches
    access_token. Gemeinsame Stelle für Sync (Pull) UND Push (calendar.py)."""
    return google.access_token(
        acc.oauth_client_id,
        decrypt(acc.oauth_secret_enc),
        decrypt(acc.oauth_refresh_enc),
    )


@router.post("/discover", response_model=list[DiscoveredCollection])
def discover(
    data: DavDiscoverRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    """Findet die Kalender/Adressbücher eines Servers (Zugang wird NICHT gespeichert)."""
    try:
        cols = client.discover_collections(
            data.url, data.username, data.password,
            want_contacts=data.kind == DavKind.carddav,
        )
    except DavUrlError as exc:
        logger.warning("DAV-Discover SSRF-Block: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ziel-URL nicht erlaubt")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Anmeldung fehlgeschlagen")
        logger.warning("DAV-Discover HTTP-Fehler: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Server-Antwort nicht verwertbar")
    except httpx.HTTPError as exc:
        logger.warning("DAV-Discover Verbindungsfehler: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Verbindungsfehler zum DAV-Server")
    return cols


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


@router.patch("/accounts/{account_id}", response_model=DavAccountOut)
def update_dav_account(
    account_id: int,
    data: DavAccountUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> DavAccount:
    """Ändert Felder eines CalDAV/CardDAV-Kontos (Bezeichnung, Collection-URL,
    Benutzername, optional Passwort). Leeres/fehlendes Passwort lässt die
    gespeicherten Zugangsdaten unverändert. Die eigentliche SSRF-Prüfung der URL
    passiert – wie beim Anlegen – erst beim Sync-Verbindungsaufbau."""
    acc = _owned(account_id, user, session)
    fields = data.model_dump(exclude_unset=True)
    password = fields.pop("password", None)
    if password:  # leer = Zugangsdaten nicht ändern
        acc.secret_enc = encrypt(password)
    for field, value in fields.items():
        setattr(acc, field, value)
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


@router.post("/google", response_model=DavAccountOut, status_code=status.HTTP_201_CREATED)
def add_google_account(
    data: GoogleCalCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> DavAccount:
    """Google-Kalender via OAuth anbinden (refresh_token-Verfahren). Sofortiger
    Token-Test, damit Tippfehler gleich auffallen statt erst beim Sync."""
    try:
        google.access_token(data.client_id, data.client_secret, data.refresh_token)
    except httpx.HTTPError as exc:
        logger.warning("Google-OAuth-Test fehlgeschlagen: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Google-Zugang ungültig (client_id/secret/refresh_token prüfen)")
    acc = DavAccount(
        user_id=user.id,
        kind=DavKind.gcal,
        label=data.label or data.email,
        url="",                       # wird aus der E-Mail abgeleitet
        username=data.email,
        secret_enc=encrypt(""),
        oauth_client_id=data.client_id,
        oauth_secret_enc=encrypt(data.client_secret),
        oauth_refresh_enc=encrypt(data.refresh_token),
    )
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


@router.get("/accounts/{account_id}/calendars", response_model=list[GcalCalendarOut])
def list_gcal_calendars(
    account_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    """ALLE Google-Kalender eines gcal-Kontos (für Filter UND Ziel-Auswahl).
    ``writable`` markiert die beschreibbaren (owner/writer) für das Anlegen."""
    acc = _owned(account_id, user, session)
    if acc.kind != DavKind.gcal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Kein Google-Konto")
    try:
        cals = google.calendars(gcal_token(acc))
    except httpx.HTTPError as exc:
        logger.warning("Google-Kalenderliste konto=%s: %s", account_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Google-Kalender nicht abrufbar")
    return [
        {
            "id": c["id"], "name": c["name"], "primary": c.get("primary", False),
            "color": c.get("color", ""),
            "writable": c.get("access_role") in ("owner", "writer"),
        }
        for c in cals
    ]


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dav_account(
    account_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    acc = _owned(account_id, user, session)
    # Importierte lokale Einträge dieses Kontos mitlöschen.
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
    acc: DavAccount, resources: list[tuple[str, str]], session: Session
) -> SyncResult:
    """CalDAV/iCal: ICS-Bodies parsen, dann in den lokalen Store übernehmen."""
    events: list[dict] = []
    for _href, body in resources:
        events.extend(parse_events(body))
    return _upsert_events(acc, events, session)


def _upsert_events(
    acc: DavAccount, events: list[dict], session: Session
) -> SyncResult:
    """Übernimmt eine Liste Event-Dicts (uid/title/…/start/end/all_day) in den Store."""
    seen: set[str] = set()
    imported = updated = 0
    # Vorab ALLE vorhandenen Events des Kontos in einem Query laden (statt ein
    # SELECT je Event) und nach external_uid indizieren.
    by_uid: dict[str, CalendarEvent] = {
        e.external_uid: e
        for e in session.exec(
            select(CalendarEvent).where(CalendarEvent.dav_account_id == acc.id)
        ).all()
        if e.external_uid
    }
    for ev in events:
            uid = ev["uid"]
            if not uid:
                continue
            seen.add(uid)
            existing = by_uid.get(uid)
            target = existing or CalendarEvent(
                user_id=acc.user_id,
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
            # Quell-Kalender für Farben/Filter: gcal liefert cal_id/calendar/color,
            # CalDAV/iCal fallen auf das Konto zurück.
            target.source_key = ev.get("cal_id") or f"dav:{acc.id}"
            target.source_name = ev.get("calendar") or acc.label
            target.source_color = ev.get("color") or ""
            target.updated_at = dt.datetime.now(dt.timezone.utc)
            session.add(target)
            updated += 1 if existing else 0
            imported += 0 if existing else 1
    removed = _prune(CalendarEvent, acc.id, seen, session)
    return SyncResult(ok=True, imported=imported, updated=updated, removed=removed)


def _sync_contacts(
    acc: DavAccount, resources: list[tuple[str, str]], session: Session
) -> SyncResult:
    seen: set[str] = set()
    imported = updated = 0
    # Vorab ALLE vorhandenen Kontakte des Kontos in einem Query laden (statt ein
    # SELECT je Kontakt) und nach external_uid indizieren.
    by_uid: dict[str, Contact] = {
        c.external_uid: c
        for c in session.exec(
            select(Contact).where(Contact.dav_account_id == acc.id)
        ).all()
        if c.external_uid
    }
    for _href, body in resources:
        for card in parse_vcards(body):
            uid = card["uid"]
            if not uid:
                continue
            seen.add(uid)
            existing = by_uid.get(uid)
            target = existing or Contact(
                user_id=acc.user_id, dav_account_id=acc.id, external_uid=uid
            )
            target.first_name = card["first_name"]
            target.last_name = card["last_name"]
            target.email = card["email"]
            target.phone = card["phone"]
            target.mobile = card.get("mobile", "")
            target.work_phone = card.get("work_phone", "")
            target.organization = card["organization"]
            target.title = card.get("title", "")
            target.website = card.get("website", "")
            target.street = card.get("street", "")
            target.postal_code = card.get("postal_code", "")
            target.city = card.get("city", "")
            target.country = card.get("country", "")
            target.notes = card["notes"]
            target.photo = card.get("photo", "")
            target.birthday = card.get("birthday")
            target.updated_at = dt.datetime.now(dt.timezone.utc)
            session.add(target)
            updated += 1 if existing else 0
            imported += 0 if existing else 1
    removed = _prune(Contact, acc.id, seen, session)
    return SyncResult(ok=True, imported=imported, updated=updated, removed=removed)


def _prune(model, account_id: int, seen: set[str], session: Session) -> int:
    """Löscht lokale Einträge dieses DAV-Kontos, deren UID nicht mehr in der
    Quelle vorkommt."""
    removed = 0
    for row in session.exec(select(model).where(model.dav_account_id == account_id)).all():
        if row.external_uid not in seen:
            session.delete(row)
            removed += 1
    return removed


def run_dav_sync(acc: DavAccount, session: Session) -> SyncResult:
    """Holt die externe Quelle und gleicht sie in den lokalen Store ab.
    Wiederverwendbar: vom Sync-Endpoint UND vom Hintergrund-Scheduler genutzt.
    Wirft NICHT — Fehler landen in acc.last_status + SyncResult(ok=False)."""
    gcal_events: list[dict] | None = None
    resources: list[tuple[str, str]] = []
    try:
        if acc.kind == DavKind.gcal:
            # Google: refresh_token → access_token → Calendar REST API (alle Kalender).
            gcal_events = google.all_events(gcal_token(acc))
        elif acc.kind == DavKind.ics:
            # Einzelner iCal-Feed (z. B. Google secret .ics) — direkter GET.
            text = client.fetch_ics(acc.url, acc.username, decrypt(acc.secret_enc))
            resources = [(acc.url, text)]
        else:
            resources = client.fetch_collection(acc.url, acc.username, decrypt(acc.secret_enc))
    except DavUrlError as exc:
        logger.warning("DAV-Sync konto=%s SSRF-Block: %s", acc.id, exc)
        acc.last_status = "Ziel-URL nicht erlaubt"
        session.add(acc)
        session.commit()
        return SyncResult(ok=False, error="Ziel-URL nicht erlaubt")
    except httpx.HTTPStatusError as exc:
        # Konkreter HTTP-Status hilft bei der Diagnose (403 = Google Calendar API
        # nicht aktiviert, 401 = Token/Scope falsch).
        code = exc.response.status_code
        logger.warning("DAV-Sync konto=%s HTTP %s: %s", acc.id, code, exc)
        hint = " (Google Calendar API aktivieren?)" if code == 403 else ""
        acc.last_status = f"HTTP {code}"
        session.add(acc)
        session.commit()
        return SyncResult(ok=False, error=f"Server-Fehler HTTP {code}{hint}")
    except httpx.HTTPError as exc:
        logger.warning("DAV-Sync konto=%s Verbindungsfehler: %s", acc.id, exc)
        acc.last_status = "Verbindungsfehler"
        session.add(acc)
        session.commit()
        return SyncResult(ok=False, error="Verbindungsfehler zum DAV-Server")

    if acc.kind == DavKind.gcal:
        result = _upsert_events(acc, gcal_events or [], session)
    elif acc.kind in (DavKind.caldav, DavKind.ics):
        result = _sync_events(acc, resources, session)
    else:
        result = _sync_contacts(acc, resources, session)

    acc.last_sync = dt.datetime.now(dt.timezone.utc)
    acc.last_status = "ok"
    session.add(acc)
    session.commit()
    return result


@router.post("/accounts/{account_id}/sync", response_model=SyncResult)
def sync_dav_account(
    account_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SyncResult:
    """Holt die externe Collection und gleicht sie in den lokalen Store ab."""
    acc = _owned(account_id, user, session)
    return run_dav_sync(acc, session)
