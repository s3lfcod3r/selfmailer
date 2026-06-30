"""In-Memory-Event-Bus für Live-Sync zwischen offenen Clients (Web/App).

Single-Process-Design: pro User eine Menge asyncio-Queues (eine je offener
SSE-Verbindung). Mutierende Aktionen (Flags/Move/Delete/Send) und der
Hintergrund-Sync veröffentlichen ein kleines Event; alle anderen offenen
Clients desselben Users frischen daraufhin auf.

`publish` ist thread-sicher (wird aus Sync-Handlern und dem Scheduler-Thread
aufgerufen) und schiebt die Zustellung in die Event-Loop.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, user_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subs[user_id].add(q)
        return q

    def unsubscribe(self, user_id: int, q: asyncio.Queue) -> None:
        subs = self._subs.get(user_id)
        if subs is not None:
            subs.discard(q)
            if not subs:
                self._subs.pop(user_id, None)

    def publish(self, user_id: int, event: dict) -> None:
        loop = self._loop
        if loop is None:
            return
        for q in list(self._subs.get(user_id, ())):
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except Exception:  # noqa: BLE001 - Zustellung ist best-effort
                # QueueFull o. ae.: verworfenes Event sichtbar machen (z. B. ein
                # Client, der nicht schnell genug konsumiert).
                logger.debug("EventBus: Event für user_id=%s verworfen", user_id, exc_info=True)


bus = EventBus()
