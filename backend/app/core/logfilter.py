"""Maskiert Geheimnisse in Logzeilen (vor allem im uvicorn-Zugriffslog).

Hintergrund (Durchsicht 2026-09-27, Befund 1): uvicorn loggt die komplette
Request-Line inklusive Query-String. Das Feed-Token wird per "?token=..."
uebergeben (die SelfDashboard-Kachel macht das heute so), deshalb stand es
16.366 Mal in 24 Stunden im Klartext im Container-Log. Wer das Log lesen
kann, hat damit Kalender und Adressbuch.

Der Filter haengt am LOGGER (nicht am Handler) und ersetzt die Werte, BEVOR
die Zeile formatiert wird. Wichtig: uvicorn konfiguriert sein Logging beim
Start selbst neu (logging.config.dictConfig). Dabei werden Handler ersetzt,
die am Logger haengenden FILTER aber nicht angetastet: common_logger_config
entfernt nur Handler. Deshalb wird install_secret_log_filter() sowohl beim
Import von app.main als auch noch einmal im Lifespan-Start aufgerufen - egal
in welcher Reihenfolge uvicorn und der App-Import laufen, der Filter sitzt
danach drin.
"""
from __future__ import annotations

import logging
import re

# Die Namen, deren Werte nie im Log stehen duerfen. Der optionale Praefix
# "[A-Za-z0-9_-]*_" deckt write_token, access_token, feed_token, csrf_token,
# api_secret usw. mit ab.
#
# Das Trennzeichen ist "=" ODER "%3D": in einem Log kann die URL url-kodiert
# auftauchen ("?token%3Dabc"), dann wuerde ein reines "=" nicht greifen. Der
# Wert laeuft bis zum naechsten "&", "#", Leerraum oder Anfuehrungszeichen -
# url-kodierte Werte sind damit automatisch mit erfasst.
_SECRET_PARAM = re.compile(
    r"(?<![A-Za-z0-9_-])((?:[A-Za-z0-9_-]*_)?(?:token|secret|password|passwd|code))"
    r"(=|%3[Dd])"
    r"[^&#\s\"'\\]*",
    re.IGNORECASE,
)

# Schnelltest, damit nicht fuer jede Logzeile die Regex laufen muss.
_TRIGGERS = ("token", "secret", "password", "passwd", "code")


def mask_secrets(text: str) -> str:
    """Ersetzt token=geheim durch token=*** (auch token%3Dgeheim)."""
    low = text.lower()
    if not any(t in low for t in _TRIGGERS):
        return text
    return _SECRET_PARAM.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)


class SecretMaskFilter(logging.Filter):
    """Maskiert Geheimnisse in record.msg UND record.args.

    record.args ist der eigentliche Punkt: uvicorn.access loggt mit dem Format
    '%s - "%s %s HTTP/%s" %d' und uebergibt den vollen Pfad inklusive Query
    als ARGUMENT. Nur msg zu maskieren wuerde im Zugriffslog gar nichts
    bewirken.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if isinstance(record.msg, str):
            record.msg = mask_secrets(record.msg)
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(
                mask_secrets(a) if isinstance(a, str) else a for a in args
            )
        elif isinstance(args, dict):
            record.args = {
                k: (mask_secrets(v) if isinstance(v, str) else v)
                for k, v in args.items()
            }
        return True


# Genau diese Logger koennen eine URL mit Query in eine Zeile schreiben. Der
# Root-Logger ist dabei, damit auch eigene logger.info("... %s", url)-Aufrufe
# erfasst werden.
_TARGET_LOGGERS = ("", "uvicorn", "uvicorn.access", "uvicorn.error", "gunicorn.access")

_FILTER = SecretMaskFilter()


def install_secret_log_filter() -> None:
    """Haengt den Filter (idempotent) an die relevanten Logger."""
    for name in _TARGET_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, SecretMaskFilter) for f in logger.filters):
            logger.addFilter(_FILTER)
