"""Schlanker In-Memory-Rate-Limiter (kein externer Dienst, kein Redis).

Bewusst minimal fuer das Single-Container-Deployment: ein gleitendes Zeit-
fenster pro (Schluessel) im Prozessspeicher. Genuegt, um Online-Brute-Force
gegen Login und TOTP-Codes praktisch zu unterbinden. Bei mehreren Workern/
Replicas waere ein geteilter Store noetig — fuer SelfMailer (ein Prozess) ok.

Verwendung::

    check_rate_limit(f"login:{client_ip}", limit=10, window_s=60)

wirft HTTP 429, sobald mehr als ``limit`` Treffer im ``window_s``-Fenster
liegen.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

_LOCK = threading.Lock()
# Schluessel -> Zeitstempel (monotonic) der juengsten Treffer.
_HITS: dict[str, deque[float]] = defaultdict(deque)
# Schutz gegen unbegrenztes Wachstum: ab dieser Schluesselzahl wird beim
# naechsten Aufruf leeres/altes Material aufgeraeumt.
_MAX_KEYS = 10_000


def client_ip(request: Request) -> str:
    """Beste Naeherung der Client-IP.

    Bewusst ``request.client.host`` und NICHT ``X-Forwarded-For``: XFF ist vom
    Client faelschbar und wuerde das Limit trivial umgehbar machen, solange kein
    vertrauenswuerdiger Proxy davorsteht. Wer hinter einem Reverse-Proxy mit
    gesetztem, bereinigtem XFF faehrt, kann das hier gezielt anpassen.
    """
    return request.client.host if request.client else "unknown"


def check_rate_limit(key: str, *, limit: int, window_s: float) -> None:
    """Zaehlt einen Treffer fuer ``key``; wirft 429 bei Ueberschreitung."""
    now = time.monotonic()
    cutoff = now - window_s
    with _LOCK:
        if len(_HITS) > _MAX_KEYS:
            _gc(cutoff)
        hits = _HITS[key]
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= limit:
            retry = max(1, int(hits[0] + window_s - now) + 1)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Zu viele Versuche. Bitte spaeter erneut versuchen.",
                headers={"Retry-After": str(retry)},
            )
        hits.append(now)


def _gc(cutoff: float) -> None:
    """Entfernt vollstaendig veraltete Schluessel (unter gehaltenem Lock)."""
    stale = [k for k, dq in _HITS.items() if not dq or dq[-1] < cutoff]
    for k in stale:
        del _HITS[k]
