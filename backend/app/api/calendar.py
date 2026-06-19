"""Kalender: lokale Events pro User (CRUD).

Externe CalDAV-Synchronisation ist eine spaetere Erweiterung; der lokale Store
ist eigenstaendig nutzbar und testbar.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel import Session, select

from ..core.db import get_session
from ..dav.ical import build_calendar
from ..models import CalendarEvent, User
from ..schemas import EventCreate, EventOut, EventUpdate
from .deps import get_current_user
from .feeds import feed_or_bearer_user

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])


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
    ev = CalendarEvent(user_id=user.id, **data.model_dump())
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
        setattr(ev, field, value)
    ev.updated_at = dt.datetime.now(dt.timezone.utc)
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
    session.delete(ev)
    session.commit()
