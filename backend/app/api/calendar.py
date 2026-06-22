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
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel import Session, select

from ..core.crypto import decrypt
from ..core.db import get_session
from ..dav import google
from ..dav.ical import build_calendar
from ..models import CalendarEvent, DavAccount, DavKind, User
from ..schemas import EventCreate, EventOut, EventUpdate
from .dav import gcal_token
from .deps import get_current_user
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


@router.get("/events", response_model=list[EventOut])
def list_events(
    start_from: dt.datetime | None = Query(default=None),
    start_to: dt.datetime | None = Query(default=None),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[CalendarEvent]:
    stmt = select(CalendarEvent).where(CalendarEvent.user_id == user.id)
    if start_from is not None:
        stmt = stmt.where(CalendarEvent.start >= start_from)
    if start_to is not None:
        stmt = stmt.where(CalendarEvent.start <= start_to)
    stmt = stmt.order_by(CalendarEvent.start)
    return list(session.exec(stmt).all())


@router.post("/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create_event(
    data: EventCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CalendarEvent:
    if data.end < data.start:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ende liegt vor Beginn")
    ev = CalendarEvent(
        user_id=user.id,
        title=data.title, description=data.description, location=data.location,
        start=_to_utc_naive(data.start), end=_to_utc_naive(data.end),
        all_day=data.all_day,
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

    session.add(ev)
    session.commit()
    session.refresh(ev)
    return ev


@router.patch("/events/{event_id}", response_model=EventOut)
def update_event(
    event_id: int,
    data: EventUpdate,
    user: User = Depends(get_current_user),
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
    user: User = Depends(get_current_user),
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
