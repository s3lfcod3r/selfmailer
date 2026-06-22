"""Google-OAuth-Helfer für CalDAV.

Google verlangt seit 14.03.2025 OAuth 2.0 für CalDAV/CardDAV/IMAP — Benutzer +
(App-)Passwort wird mit 401 abgelehnt. Da SelfMailer im LAN über http läuft und
Google keine http-Redirects (außer localhost) erlaubt, nutzen wir das
**Refresh-Token-Verfahren**: Der Nutzer holt EINMALIG client_id/client_secret +
refresh_token (z. B. über den Google OAuth Playground) und hinterlegt sie. Hier
wird daraus je Sync ein kurzlebiges access_token gemintet — kein Redirect/HTTPS
auf SelfMailer-Seite nötig.
"""
from __future__ import annotations

import httpx

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_TIMEOUT = httpx.Timeout(20.0)

# CalDAV-Collection der Hauptkalender-Termine eines Kontos.
CALDAV_EVENTS_URL = "https://apidata.googleusercontent.com/caldav/v2/{email}/events/"


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
