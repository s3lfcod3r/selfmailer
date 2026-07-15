"""Minimale In-Process-Job-Registry für schwere IMAP-Operationen.

Schwere Endpunkte (Postfach-Migration, Ordner-Transfer, Regeln anwenden,
Papierkorb/Spam leeren, Geburtstags-Sync) blockieren sonst minutenlang einen
Worker-Thread des FastAPI-Threadpools. Diese Registry führt so eine Operation
auf einem eigenen Daemon-Thread aus; der Endpunkt liefert sofort eine ``job_id``,
den Fortschritt holt das Frontend per ``GET /mail/jobs/{job_id}``.

Bewusst minimal (Single-Container, ein Prozess): ein Dict im Prozessspeicher,
per Lock geschützt, mit Deckel gegen unbegrenztes Wachstum. Kein externer Broker,
keine Persistenz — ein Neustart verwirft laufende Jobs. Bei mehreren Workern/
Replicas wäre ein geteilter Store nötig; für SelfMailer (ein Prozess) genügt das.
"""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, Callable

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
# job_id -> {id, user_id, kind, status, result, error}
# status: "pending" -> "running" -> "done" | "error"
_JOBS: dict[str, dict[str, Any]] = {}
# Deckel gegen unbegrenztes Wachstum: nur die jüngsten N Jobs behalten.
_MAX_JOBS = 200


def create_job(user_id: int, kind: str) -> str:
    """Legt einen Job (Status ``pending``) an und liefert seine ID."""
    job_id = uuid.uuid4().hex
    with _LOCK:
        _prune_locked()
        _JOBS[job_id] = {
            "id": job_id,
            "user_id": user_id,
            "kind": kind,
            "status": "pending",
            "result": None,
            "error": "",
        }
    return job_id


def start(job_id: str, fn: Callable[[], Any]) -> None:
    """Führt ``fn`` auf einem Daemon-Thread aus und legt Ergebnis/Fehler im Job ab."""

    def _worker() -> None:
        _set(job_id, status="running")
        try:
            result = fn()
            _set(job_id, status="done", result=result)
        except Exception as exc:  # noqa: BLE001 - Fehler wird im Job-Status hinterlegt
            logger.warning("Job %s (%s) fehlgeschlagen", job_id, _kind(job_id), exc_info=True)
            _set(job_id, status="error", error=type(exc).__name__)

    threading.Thread(target=_worker, name=f"job-{job_id[:8]}", daemon=True).start()


def get_job(job_id: str, user_id: int) -> dict[str, Any] | None:
    """Job-Status — nur für den Eigentümer. Kopie, damit außen nichts mutiert."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None or job["user_id"] != user_id:
            return None
        return dict(job)


def _set(job_id: str, **fields: Any) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def _kind(job_id: str) -> str:
    with _LOCK:
        job = _JOBS.get(job_id)
        return job["kind"] if job else "?"


def _prune_locked() -> None:
    """Älteste Jobs verwerfen, sobald der Deckel erreicht ist (Lock gehalten).

    Dict behält Einfüge-Reihenfolge → die vordersten Einträge sind die ältesten."""
    while len(_JOBS) >= _MAX_JOBS:
        oldest = next(iter(_JOBS))
        _JOBS.pop(oldest, None)
