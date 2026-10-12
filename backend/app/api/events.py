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
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from ..core.db import engine
from ..core.sessions import token_user
from ..events import bus
from ..models import User
from .deps import request_token

router = APIRouter(prefix="/api/v1/events", tags=["events"])


def _stream_user(request: Request) -> User:
    raw = request_token(request)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
    # Kurzlebige Session nur für den Auth-Lookup (keine Dauer-Connection halten).
    with Session(engine) as session:
        user = token_user(raw, session)
        if user is None:
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
                except asyncio.TimeoutError:
                    ev = None
                try:
                    # Prüfen vor jeder Datenlieferung und jedem Heartbeat;
                    # keine DB-Verbindung über die Wartezeit offen halten.
                    await run_in_threadpool(_stream_user, request)
                except HTTPException:
                    break
                yield ": ping\n\n" if ev is None else f"data: {json.dumps(ev)}\n\n"
        finally:
            bus.unsubscribe(user_id, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
