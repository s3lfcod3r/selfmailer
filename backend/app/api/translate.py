"""Mail-Übersetzung über eine self-hosted LibreTranslate-Instanz.

Datenschutz: Der Mailtext geht NUR an die selbst betriebene LibreTranslate-URL
(`SELFMAILER_TRANSLATE_URL`), nicht an Dritte. Ohne konfigurierte URL ist die
Funktion aus (503). Kein Speichern — reiner Proxy.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from ..core.config import get_settings
from ..dav.client import DavUrlError, validate_external_url
from ..schemas import TranslateRequest, TranslateResponse
from .deps import get_current_user
from ..models import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["translate"])

_MAX_CHARS = 20000   # Schutz vor Riesen-Requests
_TIMEOUT = httpx.Timeout(30.0)


@router.get("/translate/status")
def translate_status(user: User = Depends(get_current_user)) -> dict:
    """Ist die Übersetzung konfiguriert? (Frontend blendet den Button entsprechend ein.)"""
    return {"enabled": bool(get_settings().translate_url.strip())}


@router.post("/translate", response_model=TranslateResponse)
def translate(
    data: TranslateRequest,
    user: User = Depends(get_current_user),
) -> TranslateResponse:
    settings = get_settings()
    base = settings.translate_url.strip().rstrip("/")
    if not base:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Übersetzung nicht konfiguriert (SELFMAILER_TRANSLATE_URL fehlt).",
        )
    text = (data.text or "").strip()
    if not text:
        return TranslateResponse(translated="", source="")
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS]

    payload: dict = {"q": text, "source": data.source or "auto", "target": data.target or "de", "format": "text"}
    if settings.translate_api_key.strip():
        payload["api_key"] = settings.translate_api_key.strip()
    # SSRF-Schutz: die admin-konfigurierte Ziel-URL gegen die Blockliste prüfen
    # (loopback/link-local/Cloud-Metadata; privat nur bei DAV_BLOCK_PRIVATE).
    try:
        validate_external_url(f"{base}/translate")
    except DavUrlError as exc:
        logger.warning("Übersetzungs-URL blockiert (SSRF): %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Übersetzungsdienst nicht erreichbar")
    try:
        with httpx.Client(timeout=_TIMEOUT) as http:
            r = http.post(f"{base}/translate", json=payload)
            r.raise_for_status()
            body = r.json()
    except httpx.HTTPError as exc:
        logger.warning("Übersetzung fehlgeschlagen: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Übersetzungsdienst nicht erreichbar")

    translated = str(body.get("translatedText", "") or "")
    detected = ""
    dl = body.get("detectedLanguage")
    if isinstance(dl, dict):
        detected = str(dl.get("language", "") or "")
    return TranslateResponse(translated=translated, source=detected)
