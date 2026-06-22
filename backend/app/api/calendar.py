"""Kalender: lokale Events pro User (CRUD) + Zwei-Wege-Sync mit Google.

Der lokale Store ist eigenstaendig nutzbar. Ist ein Termin einem Google-Konto
(DavKind.gcal) zugeordnet, werden Anlegen/Aendern/Loeschen zusaetzlich in die
Google Calendar API zurueckgeschrieben (Push). Der Gegenrichtungs-Pull lebt in
``api/dav.py`` (run_dav_sync). external_uid ist durchgaengig ``{calId}::{eventId}``.

Zeiten werden im Store als **naive UTC** gehalten (Pull-Konvention); eingehende
aware-Datetimes werden hier normalisiert.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlmodel import Session, select

from ..core.crypto import decrypt
from ..core.db import get_session
from ..dav import google
from ..dav.ical import build_calendar
from ..models import CalendarEvent, DavAccount, DavKind, User
from ..schemas import EventCreate, EventOut, EventUpdate
from .dav import gcal_token
from .feeds import feed_or_bearer_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])


def _to_utc_naive(d: dt.datetime) -> dt.datetime:
    """aware → naive UTC; naive bleibt unveraendert (gilt bereits als UTC)."""
    return d.astimezone(dt.timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def _ev_dict(ev: CalendarEvent) -> dict:
    """CalendarEvent → schlankes Dict fuer die Google-Push-Funktionen."""
    return {
        "title": ev.title, "description": ev.description, "location": ev.location,
        "start": ev.start, "end": ev.end, "all_day": ev.all_day,
    }


def _gcal_account(account_id: int, user: User, session: Session) -> DavAccount:
    acc = session.get(DavAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kalender-Konto nicht gefunden")
    if acc.kind != DavKind.gcal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Zurueckschreiben nur fuer Google-Konten")
    return acc


def _push_error(exc: httpx.HTTPError) -> HTTPException:
    """Uebersetzt einen Google-Fehler in eine sprechende HTTP-Antwort.

    403 mit gueltigem Token = meist fehlender Schreib-Scope (Token wurde nur mit
    calendar.readonly geholt) → klarer Hinweis statt kryptischem 502."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Google lehnt das Schreiben ab — das refresh_token braucht den "
                "Schreib-Scope 'https://www.googleapis.com/auth/calendar' "
                "(nicht nur calendar.readonly). Bitte neu autorisieren.",
            )
        return HTTPException(status.HTTP_502_BAD_GATEWAY, f"Google-Fehler HTTP {code}")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "Verbindungsfehler zu Google")


@router.get("/export.ics")
def export_ics(
    user: User = Depends(feed_or_bearer_user),
    session: Session = Depends(get_session),
) -> Response:
    """Liefert alle Termine des Users als abonnierbaren iCalendar-Feed.

    Auth ueber ``?token=`` (Abo) oder Bearer (Direkt-Download).
    """
    stmt = select(CalendarEvent).where(CalendarEvent.user_id == user.id).order_by(
        CalendarEvent.start
    )
    body = build_calendar(session.exec(stmt).all())
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'inline; filename="selfmailer.ics"'},
    )


def _owned(event_id: int, user: User, session: Session) -> CalendarEvent:
    ev = session.get(CalendarEvent, event_id)
    if ev is None or ev.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Termin nicht gefunden")
    return ev


class HiddenCals(BaseModel):
    """Geteilte Liste ausgeblendeter Kalender (Quell-Keys, z. B. Google-Cal-IDs)."""

    keys: list[str] = []


@router.get("/hidden", response_model=HiddenCals)
def get_hidden(user: User = Depends(feed_or_bearer_user)) -> HiddenCals:
    """Server-seitig ausgeblendete Kalender des Users — geteilt von WebUI und App."""
    try:
        keys = json.loads(user.hidden_cals or "[]")
        keys = [str(k) for k in keys] if isinstance(keys, list) else []
    except (ValueError, TypeError):
        keys = []
    return HiddenCals(keys=keys)


@router.put("/hidden", response_model=HiddenCals)
def put_hidden(
    data: HiddenCals,
    user: User = Depends(feed_or_bearer_user),
    session: Session = Depends(get_session),
) -> HiddenCals:
    """Setzt die ausgeblendeten Kalender (ersetzt die Liste vollstaendig)."""
    keys = sorted({str(k) for k in data.keys if str(k)})
    db_user = session.get(User, user.id)
    if db_user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User nicht gefunden")
    db_user.hidden_cals = json.dumps(keys)
    session.add(db_user)
    session.commit()
    return HiddenCals(keys=keys)


@router.get("/events", response_model=list[EventOut])
def list_events(
    start_from: dt.datetime | None = Query(default=None),
    start_to: dt.datetime | None = Query(default=None),
    user: User = Depends(feed_or_bearer_user),
    session: Session = Depends(get_session),
) -> list[CalendarEvent]:
    stmt = select(CalendarEvent).where(CalendarEvent.user_id == user.id)
    if start_from is not None:
        stmt = stmt.where(CalendarEvent.start >= start_from)
    if start_to is not None:
        stmt = stmt.where(CalendarEvent.start <= start_to)
    stmt = stmt.order_by(CalendarEvent.start)
    return list(session.exec(stmt).all())


def _persist_event(data: EventCreate, user: User, session: Session) -> CalendarEvent:
    """Legt einen Termin im lokalen Store an und schreibt ihn optional in einen
    Google-Kalender zurueck (Zwei-Wege-Push). Kern von ``POST /events`` — egal ob
    der Aufruf per Login (WebUI) oder per Feed-Token (Dashboard) kommt."""
    if data.end < data.start:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ende liegt vor Beginn")
    ev = CalendarEvent(
        user_id=user.id,
        title=data.title, description=data.description, location=data.location,
        start=_to_utc_naive(data.start), end=_to_utc_naive(data.end),
        all_day=data.all_day,
        source_key="local", source_name="",  # lokal; Frontend zeigt "Lokal"
    )

    # Zwei-Wege: optional gleich in einen Google-Kalender zurueckschreiben.
    if data.dav_account_id is not None:
        acc = _gcal_account(data.dav_account_id, user, session)
        try:
            tok = gcal_token(acc)
            cal_id = data.gcal_calendar_id or google.primary_calendar_id(tok)
            event_id = google.create_event(tok, cal_id, _ev_dict(ev))
        except httpx.HTTPError as exc:
            logger.warning("Google-Create konto=%s: %s", acc.id, exc)
            raise _push_error(exc)
        ev.dav_account_id = acc.id
        ev.external_uid = f"{cal_id}::{event_id}"
        # Quell-Kalender direkt setzen (Name/Farbe ergaenzt der naechste Pull).
        ev.source_key = cal_id

    session.add(ev)
    session.commit()
    session.refresh(ev)
    return ev


@router.post("/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create_event(
    data: EventCreate,
    user: User = Depends(feed_or_bearer_user),
    session: Session = Depends(get_session),
) -> CalendarEvent:
    return _persist_event(data, user, session)


@router.patch("/events/{event_id}", response_model=EventOut)
def update_event(
    event_id: int,
    data: EventUpdate,
    user: User = Depends(feed_or_bearer_user),
    session: Session = Depends(get_session),
) -> CalendarEvent:
    ev = _owned(event_id, user, session)
    for field, value in data.model_dump(exclude_unset=True).items():
        if field in ("start", "end") and isinstance(value, dt.datetime):
            value = _to_utc_naive(value)
        setattr(ev, field, value)
    ev.updated_at = dt.datetime.now(dt.timezone.utc)

    # Aenderung an einem Google-Termin zurueckschreiben.
    if ev.dav_account_id is not None and ev.external_uid:
        acc = session.get(DavAccount, ev.dav_account_id)
        if acc is not None and acc.kind == DavKind.gcal:
            cal_id, gid = google.split_uid(ev.external_uid)
            if cal_id and gid:
                try:
                    google.patch_event(gcal_token(acc), cal_id, gid, _ev_dict(ev))
                except httpx.HTTPError as exc:
                    logger.warning("Google-Patch konto=%s: %s", acc.id, exc)
                    raise _push_error(exc)

    session.add(ev)
    session.commit()
    session.refresh(ev)
    return ev


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(
    event_id: int,
    user: User = Depends(feed_or_bearer_user),
    session: Session = Depends(get_session),
) -> None:
    ev = _owned(event_id, user, session)

    # Google-Termin auch dort loeschen (sonst kaeme er beim naechsten Pull zurueck).
    if ev.dav_account_id is not None and ev.external_uid:
        acc = session.get(DavAccount, ev.dav_account_id)
        if acc is not None and acc.kind == DavKind.gcal:
            cal_id, gid = google.split_uid(ev.external_uid)
            if cal_id and gid:
                try:
                    google.delete_event(gcal_token(acc), cal_id, gid)
                except httpx.HTTPError as exc:
                    logger.warning("Google-Delete konto=%s: %s", acc.id, exc)
                    raise _push_error(exc)

    session.delete(ev)
    session.commit()


# ---------------------------------------------------------------------------
# Externe Dashboard-Schnittstelle: waehlbare Ziel-Kalender (Feed-Token-Auth)
# ---------------------------------------------------------------------------
# Die Event-CRUD oben akzeptiert bereits ``?token=`` (feed_or_bearer_user),
# also kann ein Dashboard ueber denselben Mechanismus wie die Mail-Uebersicht
# (``api/dashboard.py``) Termine lesen/anlegen/aendern/loeschen — der Google-
# Push haengt an der CRUD-Logik. Hier fehlt nur noch die Liste der moeglichen
# Ziel-Kalender, damit ein externes Widget ein "in welchen Kalender?"-Dropdown
# bauen kann (Lokal + beschreibbare Google-Kalender).


@router.get("/targets")
def targets(
    user: User = Depends(feed_or_bearer_user),
    session: Session = Depends(get_session),
) -> dict:
    """Waehlbare Ziel-Kalender fuers Anlegen: ``Lokal`` plus alle beschreibbaren
    Google-Kalender des Users. Der ``key`` wird beim Anlegen zerlegt: ``local``
    → rein lokal, ``{accId}::{calId}`` → ``dav_account_id`` + ``gcal_calendar_id``
    in ``POST /events``. Ein Konto, das gerade nicht erreichbar ist, wird
    uebersprungen statt die Liste zu kippen. Heavier (Google-Call) → vom Widget
    nur beim Oeffnen des Anlege-Dialogs holen, nicht beim Polling."""
    out: list[dict] = [
        {"key": "local", "label": "Lokal", "color": "", "primary": False},
    ]
    accounts = session.exec(
        select(DavAccount).where(
            DavAccount.user_id == user.id, DavAccount.kind == DavKind.gcal
        )
    ).all()
    for acc in accounts:
        try:
            cals = google.writable_calendars(gcal_token(acc))
        except httpx.HTTPError as exc:
            logger.warning("Targets: Google-Konto %s nicht erreichbar: %s", acc.id, exc)
            continue
        for c in cals:
            out.append({
                "key": f"{acc.id}::{c['id']}",
                "label": c["name"],
                "color": c.get("color", ""),
                "primary": c.get("primary", False),
            })
    return {"targets": out}
