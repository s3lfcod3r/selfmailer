"""Server-Sent-Events-Stream für Live-Sync. Hält pro Client eine dünne
Dauerverbindung; der Server schickt „aktualisieren"-Events.

Auth (in dieser Reihenfolge): Bearer-Header (APK) · httpOnly-Session-Cookie
(Web). Bewusst KEIN ``?token=`` mehr — ein voller JWT in der URL landet sonst
in Server-/Proxy-Logs. Das Web nutzt das Cookie, die APK den Bearer-Header."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from ..core.config import get_settings
from ..core.db import engine
from ..core.security import decode_token
from ..events import bus
from ..models import User

router = APIRouter(prefix="/api/v1/events", tags=["events"])


def _stream_user(request: Request) -> User:
    raw: str | None = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        raw = auth[7:]
    if not raw:
        raw = request.cookies.get(get_settings().cookie_name)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
    payload = decode_token(raw)
    if not payload or payload.get("stage") == "mfa":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token ungueltig")
    # Kurzlebige Session nur fuer den Auth-Lookup (keine Dauer-Connection halten).
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == payload.get("sub"))).first()
        if user is None or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Konto inaktiv/unbekannt")
        _ = user.id  # sicherstellen, dass id geladen ist (nach Session-Ende nutzbar)
        return user


@router.get("/stream")
async def stream(request: Request, user: User = Depends(_stream_user)) -> StreamingResponse:
    user_id = user.id
    q = bus.subscribe(user_id)

    async def gen():
        try:
            yield ": ok\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # Heartbeat hält die Verbindung offen
        finally:
            bus.unsubscribe(user_id, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
