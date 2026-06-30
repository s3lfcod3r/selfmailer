"""iCalendar (RFC 5545) bauen und parsen — auf das beschränkt, was SelfMailer
für VEVENTs braucht (Titel, Zeitraum, Ort, Beschreibung, Ganztags-Flag).

build_calendar() erzeugt den Export-Feed; parse_events() liest fremde Feeds
beim CalDAV-Pull. Beide arbeiten auf demselben schlichten dict-Schema, damit
api/calendar.py es direkt in CalendarEvent überführen kann.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Iterable

from . import rfc

PRODID = "-//SelfMailer//Calendar 0.1//DE"


def _event_uid(ev: Any) -> str:
    """Stabile UID: vorhandene external_uid bevorzugen, sonst aus der lokalen id."""
    ext = getattr(ev, "external_uid", "") or ""
    if ext:
        return ext
    return f"selfmailer-event-{getattr(ev, 'id', 'x')}@selfmailer"


def build_calendar(events: Iterable[Any], *, stamp: dt.datetime | None = None) -> str:
    """Serialisiert CalendarEvent-ähnliche Objekte zu einem VCALENDAR-String."""
    now = stamp or dt.datetime.now(dt.timezone.utc)
    dtstamp = rfc.fmt_datetime_utc(now)
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
    ]
    for ev in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{_event_uid(ev)}")
        lines.append(f"DTSTAMP:{dtstamp}")
        if getattr(ev, "all_day", False):
            lines.append(f"DTSTART;VALUE=DATE:{rfc.fmt_date(ev.start)}")
            # iCalendar DTEND ist bei Ganztags exklusiv -> +1 Tag.
            end_excl = ev.end + dt.timedelta(days=1)
            lines.append(f"DTEND;VALUE=DATE:{rfc.fmt_date(end_excl)}")
        else:
            lines.append(f"DTSTART:{rfc.fmt_datetime_utc(ev.start)}")
            lines.append(f"DTEND:{rfc.fmt_datetime_utc(ev.end)}")
        lines.append(f"SUMMARY:{rfc.escape_text(getattr(ev, 'title', '') or '')}")
        if getattr(ev, "description", ""):
            lines.append(f"DESCRIPTION:{rfc.escape_text(ev.description)}")
        if getattr(ev, "location", ""):
            lines.append(f"LOCATION:{rfc.escape_text(ev.location)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return rfc.CRLF.join(rfc.fold_line(ln) for ln in lines) + rfc.CRLF


def parse_events(text: str) -> list[dict[str, Any]]:
    """Liest VEVENTs aus iCalendar-Text in dicts.

    Schluessel: uid, title, description, location, start (datetime), end
    (datetime), all_day (bool). Termine ohne gültiges DTSTART werden
    übersprungen.
    """
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in rfc.unfold(text):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            current = {
                "uid": "",
                "title": "",
                "description": "",
                "location": "",
                "start": None,
                "end": None,
                "all_day": False,
            }
            continue
        if upper == "END:VEVENT":
            if current and current["start"] is not None:
                if current["end"] is None:
                    current["end"] = current["start"]
                events.append(current)
            current = None
            continue
        if current is None:
            continue
        name, params, value = rfc.split_property(line)
        is_date = params.get("VALUE") == "DATE"
        if name == "UID":
            current["uid"] = value.strip()
        elif name == "SUMMARY":
            current["title"] = rfc.unescape_text(value)
        elif name == "DESCRIPTION":
            current["description"] = rfc.unescape_text(value)
        elif name == "LOCATION":
            current["location"] = rfc.unescape_text(value)
        elif name == "DTSTART":
            current["start"] = rfc.parse_dt(value, params.get("TZID"))
            current["all_day"] = is_date
        elif name == "DTEND":
            end = rfc.parse_dt(value, params.get("TZID"))
            if end is not None and is_date:
                # DTEND ist bei Ganztags exklusiv -> inklusiven letzten Tag speichern.
                end = end - dt.timedelta(days=1)
            current["end"] = end
    return events
