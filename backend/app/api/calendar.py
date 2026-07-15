"""Kalender: lokale Events pro User (CRUD) + Zwei-Wege-Sync mit Google.

Der lokale Store ist eigenständig nutzbar. Ist ein Termin einem Google-Konto
(DavKind.gcal) zugeordnet, werden Anlegen/Ändern/Löschen zusätzlich in die
Google Calendar API zurückgeschrieben (Push). Der Gegenrichtungs-Pull lebt in
``api/dav.py`` (run_dav_sync). external_uid ist durchgängig ``{calId}::{eventId}``.

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

from ..core.db import get_session
from ..dav import google
from ..dav.ical import build_calendar
from ..models import CalendarEvent, DavAccount, DavKind, User
from ..schemas import EventCreate, EventOut, EventUpdate
from .dav import gcal_token
from .feeds import feed_or_bearer_user, feed_write_or_login

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])

# Obergrenze für Listen-Endpoints, damit eine Tabelle nie unbegrenzt viele Zeilen
# liefert (besonders über den Feed-Token-Pfad ohne Login).
_MAX_LIST = 2000


def _to_utc_naive(d: dt.datetime) -> dt.datetime:
    """aware → naive UTC; naive bleibt unverändert (gilt bereits als UTC)."""
    return d.astimezone(dt.timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def _ev_dict(ev: CalendarEvent) -> dict:
    """CalendarEvent → schlankes Dict für die Google-Push-Funktionen."""
    return {
        "title": ev.title, "description": ev.description, "location": ev.location,
        "start": ev.start, "end": ev.end, "all_day": ev.all_day,
    }


def _gcal_account(account_id: int, user: User, session: Session) -> DavAccount:
    acc = session.get(DavAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kalender-Konto nicht gefunden")
    if acc.kind != DavKind.gcal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Zurückschreiben nur für Google-Konten")
    return acc


def _push_error(exc: httpx.HTTPError) -> HTTPException:
    """Übersetzt einen Google-Fehler in eine sprechende HTTP-Antwort.

    403 mit gültigem Token = meist fehlender Schreib-Scope (Token wurde nur mit
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

    Auth über ``?token=`` (Abo) oder Bearer (Direkt-Download).
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
    user: User = Depends(feed_write_or_login),
    session: Session = Depends(get_session),
) -> HiddenCals:
    """Setzt die ausgeblendeten Kalender (ersetzt die Liste vollständig)."""
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
    stmt = stmt.order_by(CalendarEvent.start).limit(_MAX_LIST)
    return list(session.exec(stmt).all())


def _persist_event(data: EventCreate, user: User, session: Session) -> CalendarEvent:
    """Legt einen Termin im lokalen Store an und schreibt ihn optional in einen
    Google-Kalender zurück (Zwei-Wege-Push). Kern von ``POST /events`` — nur per
    vollem Login (Cookie/Bearer) erreichbar; ein Feed-Token ist read-only."""
    if data.end < data.start:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ende liegt vor Beginn")
    ev = CalendarEvent(
        user_id=user.id,
        title=data.title, description=data.description, location=data.location,
        start=_to_utc_naive(data.start), end=_to_utc_naive(data.end),
        all_day=data.all_day,
        source_key="local", source_name="",  # lokal; Frontend zeigt "Lokal"
    )

    # Zwei-Wege: optional gleich in einen Google-Kalender zurückschreiben.
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
        # Quell-Kalender sofort mit Name + Farbe setzen, damit der Termin direkt
        # nach dem Anlegen im richtigen Kalender erscheint (nicht erst „Lokal",
        # bis der nächste Pull den Namen nachträgt). Gleiche Logik wie beim
        # Verschieben (_change_calendar).
        ev.source_key = cal_id
        ev.source_name, ev.source_color = _cal_meta(tok, cal_id)

    session.add(ev)
    session.commit()
    session.refresh(ev)
    return ev


@router.post("/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create_event(
    data: EventCreate,
    user: User = Depends(feed_write_or_login),
    session: Session = Depends(get_session),
) -> CalendarEvent:
    return _persist_event(data, user, session)


def _cal_meta(token: str, cal_id: str) -> tuple[str, str]:
    """Name + Farbe eines Google-Kalenders (für sofort korrekte Anzeige nach Move)."""
    try:
        for c in google.calendars(token):
            if c.get("id") == cal_id:
                return c.get("name", "") or "", c.get("color", "") or ""
    except httpx.HTTPError:
        pass
    return "", ""


def _push_field_update(ev: CalendarEvent, session: Session) -> None:
    """Feldänderung an den bestehenden Google-Termin zurückschreiben (kein Move)."""
    if ev.dav_account_id is None or not ev.external_uid:
        return
    acc = session.get(DavAccount, ev.dav_account_id)
    if acc is None or acc.kind != DavKind.gcal:
        return
    cal_id, gid = google.split_uid(ev.external_uid)
    if cal_id and gid:
        try:
            google.patch_event(gcal_token(acc), cal_id, gid, _ev_dict(ev))
        except httpx.HTTPError as exc:
            logger.warning("Google-Patch konto=%s: %s", acc.id, exc)
            raise _push_error(exc)


def _change_calendar(
    ev: CalendarEvent, target_acc_id: int | None, target_cal: str,
    user: User, session: Session,
) -> None:
    """Verschiebt einen Termin in einen anderen Kalender — oder zurück nach lokal.

    Fälle: gleiches Ziel → nur Feld-Push; lokal→Google → anlegen; Google→lokal →
    dort löschen; Google→Google im selben Konto → echtes ``events.move``;
    kontoübergreifend → alt löschen + neu anlegen.
    """
    cur_acc_id = ev.dav_account_id
    cur_cal, cur_gid = ("", "")
    if cur_acc_id is not None and ev.external_uid:
        cur_cal, cur_gid = google.split_uid(ev.external_uid)

    new_acc: DavAccount | None = None
    new_cal = ""
    if target_acc_id:
        new_acc = _gcal_account(target_acc_id, user, session)
        new_cal = target_cal or google.primary_calendar_id(gcal_token(new_acc))
    new_acc_id = new_acc.id if new_acc else None

    # Gleiches Ziel → nur Feldänderung am bestehenden Termin zurückschreiben.
    if new_acc_id == cur_acc_id and (new_acc_id is None or new_cal == cur_cal):
        _push_field_update(ev, session)
        return

    try:
        if cur_acc_id is None and new_acc is not None:
            tok = gcal_token(new_acc)
            gid = google.create_event(tok, new_cal, _ev_dict(ev))
            ev.dav_account_id = new_acc.id
            ev.external_uid = f"{new_cal}::{gid}"
            ev.source_key = new_cal
            ev.source_name, ev.source_color = _cal_meta(tok, new_cal)
        elif cur_acc_id is not None and new_acc is None:
            old = session.get(DavAccount, cur_acc_id)
            if old is not None and cur_cal and cur_gid:
                google.delete_event(gcal_token(old), cur_cal, cur_gid)
            ev.dav_account_id = None
            ev.external_uid = None
            ev.source_key = "local"
            ev.source_name = ""
            ev.source_color = ""
        elif cur_acc_id == new_acc_id and new_acc is not None:
            tok = gcal_token(new_acc)
            if cur_cal and cur_gid:
                google.move_event(tok, cur_cal, cur_gid, new_cal)
                try:
                    google.patch_event(tok, new_cal, cur_gid, _ev_dict(ev))
                except httpx.HTTPError:
                    pass  # Move hat geklappt; Feld-Push ist Beiwerk
                ev.external_uid = f"{new_cal}::{cur_gid}"
            else:
                gid = google.create_event(tok, new_cal, _ev_dict(ev))
                ev.external_uid = f"{new_cal}::{gid}"
            ev.source_key = new_cal
            ev.source_name, ev.source_color = _cal_meta(tok, new_cal)
        elif new_acc is not None:
            old = session.get(DavAccount, cur_acc_id) if cur_acc_id else None
            if old is not None and cur_cal and cur_gid:
                try:
                    google.delete_event(gcal_token(old), cur_cal, cur_gid)
                except httpx.HTTPError:
                    pass
            tok = gcal_token(new_acc)
            gid = google.create_event(tok, new_cal, _ev_dict(ev))
            ev.dav_account_id = new_acc.id
            ev.external_uid = f"{new_cal}::{gid}"
            ev.source_key = new_cal
            ev.source_name, ev.source_color = _cal_meta(tok, new_cal)
    except httpx.HTTPError as exc:
        logger.warning("Kalender-Wechsel Termin=%s: %s", ev.id, exc)
        raise _push_error(exc)


_UNSET = object()


@router.patch("/events/{event_id}", response_model=EventOut)
def update_event(
    event_id: int,
    data: EventUpdate,
    user: User = Depends(feed_write_or_login),
    session: Session = Depends(get_session),
) -> CalendarEvent:
    ev = _owned(event_id, user, session)
    payload = data.model_dump(exclude_unset=True)
    # Ziel-Felder gesondert behandeln (nicht per setattr ins Modell schreiben).
    target_acc_id = payload.pop("dav_account_id", _UNSET)
    target_cal = payload.pop("gcal_calendar_id", "")
    for field, value in payload.items():
        if field in ("start", "end") and isinstance(value, dt.datetime):
            value = _to_utc_naive(value)
        setattr(ev, field, value)
    ev.updated_at = dt.datetime.now(dt.timezone.utc)

    if target_acc_id is _UNSET:
        # Kein Ziel mitgeschickt → bestehendes Verhalten: Felder am Google-Termin pushen.
        _push_field_update(ev, session)
    else:
        _change_calendar(ev, target_acc_id, target_cal or "", user, session)

    session.add(ev)
    session.commit()
    session.refresh(ev)
    return ev


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(
    event_id: int,
    user: User = Depends(feed_write_or_login),
    session: Session = Depends(get_session),
) -> None:
    ev = _owned(event_id, user, session)

    # Google-Termin auch dort löschen (sonst kaeme er beim nächsten Pull zurück).
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
# Externe Dashboard-Schnittstelle: wählbare Ziel-Kalender (Feed-Token-Auth)
# ---------------------------------------------------------------------------
# Lesende Endpunkte (Liste/Export/Ziel-Kalender) akzeptieren JEDEN Feed-Token
# (feed_or_bearer_user), damit ein Dashboard ganz ohne Login POLLEN kann.
# SCHREIBEN (Anlegen/Ändern/Löschen, inkl. Google-Push) verlangt dagegen den
# separaten SCHREIB-Token oder Login (feed_write_or_login) — der leck-anfällige
# Lese-Token (steckt in Abo-URLs) darf NICHT schreiben. Diese Ziel-Kalender-Liste
# (Lokal + beschreibbare Google-Kalender) versorgt das "in welchen Kalender?"-
# Dropdown eines externen Widgets.


@router.get("/targets")
def targets(
    user: User = Depends(feed_or_bearer_user),
    session: Session = Depends(get_session),
) -> dict:
    """Wählbare Ziel-Kalender fürs Anlegen: ``Lokal`` plus alle beschreibbaren
    Google-Kalender des Users. Der ``key`` wird beim Anlegen zerlegt: ``local``
    → rein lokal, ``{accId}::{calId}`` → ``dav_account_id`` + ``gcal_calendar_id``
    in ``POST /events``. Ein Konto, das gerade nicht erreichbar ist, wird
    übersprungen statt die Liste zu kippen. Heavier (Google-Call) → vom Widget
    nur beim Öffnen des Anlege-Dialogs holen, nicht beim Polling."""
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


@router.get("/calendars")
def all_calendars(
    user: User = Depends(feed_or_bearer_user),
    session: Session = Depends(get_session),
) -> dict:
    """ALLE Quell-Kalender des Users für den Filter eines externen Dashboards:
    ``Lokal`` plus sämtliche Google-Kalender (auch read-only/leere — Feiertage,
    Geburtstage, abonnierte). ``key`` matcht den ``source_key`` der Events
    (Google-Kalender-ID bzw. ``local``), damit das Widget ALLE Kalender auflisten
    kann, nicht nur die mit Terminen im sichtbaren Zeitraum. Heavier (Google-Call)
    → vom Widget nur selten holen (Start/Refresh), nicht beim Polling."""
    out: list[dict] = [{"key": "local", "name": "Lokal", "color": ""}]
    accounts = session.exec(
        select(DavAccount).where(
            DavAccount.user_id == user.id, DavAccount.kind == DavKind.gcal
        )
    ).all()
    for acc in accounts:
        try:
            cals = google.calendars(gcal_token(acc))
        except httpx.HTTPError as exc:
            logger.warning("Calendars: Google-Konto %s nicht erreichbar: %s", acc.id, exc)
            continue
        for c in cals:
            out.append({"key": c["id"], "name": c["name"], "color": c.get("color", "")})
    return {"calendars": out}
