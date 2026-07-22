"""Geräteübergreifende Oberflächen-Einstellungen eines Benutzers.

Zweck: WebUI und Android-App sollen denselben Stand teilen — schaltet man im Web
etwas um, zieht die App beim nächsten Start nach. Gespeichert wird ein kleines
JSON-Objekt in ``User.ui_settings``.

BEWUSST NICHT hier: gerätespezifische Einstellungen (Textgröße, Auto-Abruf-
Intervall, helles/dunkles Design). Die sollen sich pro Gerät unterscheiden dürfen
— 100 % Textgröße am Monitor ist am Handy zu klein — und bleiben deshalb im
lokalen Speicher des jeweiligen Clients.

Erweitern: einen Eintrag in ``_ALLOWED`` ergänzen, mehr ist serverseitig nicht nötig.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlmodel import Session

from ..core.db import get_session
from ..models import User
from .deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["settings"])

# Whitelist: Schlüssel -> (Typ, Standardwert). Unbekannte Schlüssel werden
# abgewiesen, statt sie zu speichern — sonst wird das Feld zur Müllhalde und
# ein Tippfehler im Client fällt nie auf.
_ALLOWED: dict[str, tuple[type, Any]] = {
    # Markierte (Stern-)Mails in jeder Liste oben anheften.
    "pin_flagged": (bool, False),
    # Zusammengehörende Mails (Antwortketten) als EINE Konversation zusammenfassen.
    "conversation_view": (bool, False),
}


def _read(user: User) -> dict:
    """Gespeicherte Einstellungen + Standardwerte für alles Fehlende.

    Defensiv: Ist das Feld leer oder (durch einen Fehler) kein gültiges JSON-Objekt,
    liefern wir die Standardwerte statt einen 500 zu werfen — die Einstellungen
    sind Komfort, kein kritischer Zustand."""
    stored: dict = {}
    raw = (user.ui_settings or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                stored = parsed
        except ValueError:
            logger.warning("ui_settings von user_id=%s ist kein gültiges JSON — Standardwerte", user.id)
    out: dict = {}
    for key, (typ, default) in _ALLOWED.items():
        val = stored.get(key, default)
        out[key] = val if isinstance(val, typ) else default
    return out


@router.get("/settings/ui")
def get_ui_settings(user: User = Depends(get_current_user)) -> dict:
    return _read(user)


@router.put("/settings/ui")
def put_ui_settings(
    patch: dict = Body(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Teil-Aktualisierung: nur die mitgeschickten Schlüssel ändern sich.

    Absicht: Clients sollen einen einzelnen Schalter senden können, ohne den
    Rest zu kennen. Schickte ein älterer Client das ganze Objekt, würde er sonst
    neuere Einstellungen überschreiben, die er gar nicht kennt."""
    if not isinstance(patch, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Objekt erwartet")
    unknown = set(patch) - set(_ALLOWED)
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unbekannte Einstellung: {', '.join(sorted(unknown))}")
    current = _read(user)
    for key, val in patch.items():
        typ, _default = _ALLOWED[key]
        if not isinstance(val, typ):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"'{key}' erwartet {typ.__name__}")
        current[key] = val
    db_user = session.get(User, user.id)
    if db_user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Benutzer nicht gefunden")
    db_user.ui_settings = json.dumps(current)
    session.add(db_user)
    session.commit()
    return current
