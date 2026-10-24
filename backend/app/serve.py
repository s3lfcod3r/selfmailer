"""Server-Start fuer den Container (seit 1.97.0): ``python -m app.serve``.

HTTP auf 8090 laeuft IMMER - LAN-Zugriff, Healthcheck und Reverse-Proxys
bleiben damit wie bisher. Sind SELFMAILER_TLS_CERT und SELFMAILER_TLS_KEY
gesetzt, lauscht ZUSAETZLICH HTTPS auf SELFMAILER_TLS_PORT (Standard 8443).
Wer nur HTTPS will, gibt Port 8090 im Container-Mapping einfach nicht frei.

Beide Listener teilen sich EINE App-Instanz im selben Prozess. Nur der
HTTP-Server faehrt den Lifespan (DB-Init, Scheduler) - sonst liefe der
Hintergrund-Sync doppelt.

Proxy-Header (X-Forwarded-For/-Proto) glaubt uvicorn nur von
SELFMAILER_TRUSTED_PROXIES (Standard: 127.0.0.1). Davon haengen die echte
Client-IP der Login-Bremse und das Schema fuer Cookie "auto"/HSTS ab.
"""
from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from .core.config import Settings, get_settings

logger = logging.getLogger("selfmailer.serve")

HTTP_PORT = 8090
HOST = "0.0.0.0"


def _forwarded_allow_ips(settings: Settings) -> str:
    raw = ",".join(p.strip() for p in settings.trusted_proxies.split(",") if p.strip())
    return raw or "127.0.0.1"


def build_configs(settings: Settings, app: object = "app.main:app") -> list[uvicorn.Config]:
    """uvicorn-Konfigurationen: [HTTP] oder [HTTP, HTTPS]."""
    gemeinsam = dict(
        host=HOST,
        proxy_headers=True,
        forwarded_allow_ips=_forwarded_allow_ips(settings),
        server_header=False,
    )
    configs = [uvicorn.Config(app, port=HTTP_PORT, **gemeinsam)]
    if settings.tls_enabled:
        for pfad in (settings.tls_cert, settings.tls_key):
            if not os.path.isfile(pfad):
                raise SystemExit(f"TLS-Datei nicht gefunden: {pfad}")
        configs.append(uvicorn.Config(
            app, port=settings.tls_port, lifespan="off",
            ssl_certfile=settings.tls_cert, ssl_keyfile=settings.tls_key,
            **gemeinsam,
        ))
    return configs


async def _serve_all(configs: list[uvicorn.Config]) -> None:
    servers = [uvicorn.Server(c) for c in configs]
    tasks = [asyncio.create_task(s.serve()) for s in servers]
    # Jeder Server faengt SIGTERM; der zuletzt gestartete gewinnt den Handler.
    # Endet einer (Signal oder Fehler), werden alle anderen mit beendet - sonst
    # haengt "docker stop" bis zum SIGKILL.
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for s in servers:
        s.should_exit = True
    if pending:
        await asyncio.wait(pending)
    for t in done:
        t.result()  # Fehler eines Listeners nicht verschlucken


def main() -> None:
    settings = get_settings()
    configs = build_configs(settings)
    if len(configs) == 1:
        asyncio.run(uvicorn.Server(configs[0]).serve())
        return
    logger.info("HTTPS zusaetzlich auf Port %s aktiv", settings.tls_port)
    asyncio.run(_serve_all(configs))


if __name__ == "__main__":
    main()
