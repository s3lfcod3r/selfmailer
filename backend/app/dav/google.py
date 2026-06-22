"""Google-Kalender via OAuth + Calendar-REST-API.

Google verlangt seit 14.03.2025 OAuth 2.0 — Benutzer + (App-)Passwort wird mit
401 abgelehnt. Da SelfMailer im LAN über http läuft und Google keine
http-Redirects (außer localhost) erlaubt, nutzen wir das **Refresh-Token-
Verfahren**: Der Nutzer holt EINMALIG client_id/client_secret + refresh_token
(z. B. über den Google OAuth Playground) und hinterlegt sie. Hier wird daraus je
Sync ein kurzlebiges access_token gemintet.

Statt Googles eigenwilligem CalDAV nutzen wir die **Calendar REST API v3** —
dasselbe OAuth-Token, aber deutlich robuster und sauber für späteres Schreiben.
"""
from __future__ import annotations

import datetime as dt
import urllib.parse
from typing import Any

import httpx

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{cal}/events"
_CALLIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
_TIMEOUT = httpx.Timeout(20.0)


def access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    """Tauscht das refresh_token gegen ein frisches access_token (Bearer)."""
    with httpx.Client(timeout=_TIMEOUT) as http:
        r = http.post(
            _TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        r.raise_for_status()
        tok = r.json().get("access_token")
    if not tok:
        raise httpx.HTTPError("Kein access_token von Google erhalten")
    return tok


def _utc_naive(d: dt.datetime) -> dt.datetime:
    """tz-aware → naive UTC (passt zur Speicherung der lokalen Events)."""
    return d.astimezone(dt.timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def _map_event(item: dict[str, Any]) -> dict[str, Any] | None:
    """Google-REST-Event → SelfMailer-Event-Dict (wie ical.parse_events)."""
    if item.get("status") == "cancelled":
        return None
    uid = item.get("id") or ""
    if not uid:
        return None
    start = item.get("start") or {}
    end = item.get("end") or {}
    if start.get("date"):
        # Ganztägig: Google end.date ist exklusiv → inklusiven letzten Tag speichern.
        all_day = True
        s = dt.datetime.fromisoformat(start["date"])
        e = dt.datetime.fromisoformat(end["date"]) - dt.timedelta(days=1) if end.get("date") else s
    elif start.get("dateTime"):
        all_day = False
        s = _utc_naive(dt.datetime.fromisoformat(start["dateTime"]))
        e = _utc_naive(dt.datetime.fromisoformat(end["dateTime"])) if end.get("dateTime") else s
    else:
        return None
    return {
        "uid": uid,
        "title": item.get("summary", "") or "",
        "description": item.get("description", "") or "",
        "location": item.get("location", "") or "",
        "start": s,
        "end": e,
        "all_day": all_day,
    }


def calendars(access_tok: str) -> list[dict[str, Any]]:
    """Listet ALLE Kalender des Kontos (eigene, geteilte, abonnierte: Geburtstage,
    Familienkalender …). Gibt ``[{id, name, primary, access_role}, …]`` zurück.

    ``access_role`` ist ``owner``/``writer``/``reader``/``freeBusyReader`` — nur
    owner/writer sind beschreibbar (relevant fuer die Ziel-Auswahl beim Anlegen)."""
    headers = {"Authorization": f"Bearer {access_tok}"}
    out: list[dict[str, Any]] = []
    page: str | None = None
    with httpx.Client(timeout=_TIMEOUT) as http:
        while True:
            params: dict[str, str] = {"maxResults": "250"}
            if page:
                params["pageToken"] = page
            r = http.get(_CALLIST_URL, headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
            for item in data.get("items", []):
                cid = item.get("id")
                if cid:
                    out.append({
                        "id": cid,
                        "name": item.get("summary", "") or cid,
                        "primary": bool(item.get("primary")),
                        "access_role": item.get("accessRole", "") or "",
                        "color": item.get("backgroundColor", "") or "",
                    })
            page = data.get("nextPageToken")
            if not page:
                break
    return out


def writable_calendars(access_tok: str) -> list[dict[str, Any]]:
    """Nur die Kalender, in die der Nutzer schreiben darf (owner/writer)."""
    return [c for c in calendars(access_tok) if c.get("access_role") in ("owner", "writer")]


def primary_calendar_id(access_tok: str) -> str:
    """Echte ID des Hauptkalenders (= i. d. R. die Konto-E-Mail). Fallback
    ``primary``, falls keiner als primary markiert ist."""
    for c in calendars(access_tok):
        if c.get("primary"):
            return str(c["id"])
    return "primary"


def _to_google_body(ev: dict[str, Any]) -> dict[str, Any]:
    """SelfMailer-Event-Dict → Google-REST-Event-Body.

    Der lokale Store haelt Zeiten als **naive UTC** (so liefert sie auch der
    Pull, siehe ``_utc_naive``) → beim Zurueckschreiben als UTC mit ``Z`` senden.
    Ganztags: Google ``end.date`` ist EXKLUSIV → inklusiven letzten Tag + 1 Tag.
    """
    start: dt.datetime = ev["start"]
    end: dt.datetime = ev["end"]
    body: dict[str, Any] = {
        "summary": ev.get("title", "") or "",
        "description": ev.get("description", "") or "",
        "location": ev.get("location", "") or "",
    }
    if ev.get("all_day"):
        body["start"] = {"date": start.date().isoformat()}
        body["end"] = {"date": (end.date() + dt.timedelta(days=1)).isoformat()}
    else:
        body["start"] = {"dateTime": _iso_z(start), "timeZone": "UTC"}
        body["end"] = {"dateTime": _iso_z(end), "timeZone": "UTC"}
    return body


def _iso_z(d: dt.datetime) -> str:
    """naive (UTC) datetime → ISO-8601 mit ``Z``."""
    base = d.replace(tzinfo=None) if d.tzinfo else d
    return base.isoformat(timespec="seconds") + "Z"


def create_event(access_tok: str, calendar_id: str, ev: dict[str, Any]) -> str:
    """Legt einen Termin in Google an und gibt dessen Event-ID zurueck."""
    headers = {"Authorization": f"Bearer {access_tok}"}
    url = _EVENTS_URL.format(cal=urllib.parse.quote(calendar_id, safe=""))
    with httpx.Client(timeout=_TIMEOUT) as http:
        r = http.post(url, headers=headers, json=_to_google_body(ev))
        r.raise_for_status()
        return str(r.json().get("id") or "")


def patch_event(access_tok: str, calendar_id: str, event_id: str, ev: dict[str, Any]) -> None:
    """Aktualisiert einen vorhandenen Google-Termin (partielles Update)."""
    headers = {"Authorization": f"Bearer {access_tok}"}
    base = _EVENTS_URL.format(cal=urllib.parse.quote(calendar_id, safe=""))
    url = f"{base}/{urllib.parse.quote(event_id, safe='')}"
    with httpx.Client(timeout=_TIMEOUT) as http:
        r = http.patch(url, headers=headers, json=_to_google_body(ev))
        r.raise_for_status()


def delete_event(access_tok: str, calendar_id: str, event_id: str) -> None:
    """Loescht einen Google-Termin. Bereits-geloescht (404/410) gilt als Erfolg."""
    headers = {"Authorization": f"Bearer {access_tok}"}
    base = _EVENTS_URL.format(cal=urllib.parse.quote(calendar_id, safe=""))
    url = f"{base}/{urllib.parse.quote(event_id, safe='')}"
    with httpx.Client(timeout=_TIMEOUT) as http:
        r = http.delete(url, headers=headers)
        if r.status_code not in (404, 410):
            r.raise_for_status()


def split_uid(external_uid: str) -> tuple[str, str]:
    """``{calId}::{eventId}`` → (calId, eventId). Ohne Trenner: ('', uid)."""
    if "::" in external_uid:
        cal_id, _, event_id = external_uid.partition("::")
        return cal_id, event_id
    return "", external_uid


def events(access_tok: str, calendar_id: str = "primary") -> list[dict[str, Any]]:
    """Holt alle Termine EINES Google-Kalenders (paginierte REST-Abfrage)."""
    headers = {"Authorization": f"Bearer {access_tok}"}
    url = _EVENTS_URL.format(cal=urllib.parse.quote(calendar_id, safe=""))
    out: list[dict[str, Any]] = []
    page: str | None = None
    with httpx.Client(timeout=_TIMEOUT) as http:
        while True:
            params: dict[str, str] = {"singleEvents": "true", "maxResults": "2500", "showDeleted": "false"}
            if page:
                params["pageToken"] = page
            r = http.get(url, headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
            for item in data.get("items", []):
                mapped = _map_event(item)
                if mapped:
                    out.append(mapped)
            page = data.get("nextPageToken")
            if not page:
                break
    return out


def all_events(access_tok: str) -> list[dict[str, Any]]:
    """Termine ALLER Kalender des Kontos. UID wird mit der Kalender-ID präfixt
    (eindeutig über Kalender hinweg); Kalendername als Quelle mitgegeben."""
    out: list[dict[str, Any]] = []
    for cal in calendars(access_tok):
        for ev in events(access_tok, cal["id"]):
            ev = dict(ev)
            ev["uid"] = f'{cal["id"]}::{ev["uid"]}'
            ev["cal_id"] = cal["id"]
            ev["calendar"] = cal["name"]
            ev["color"] = cal.get("color", "")
            out.append(ev)
    return out
