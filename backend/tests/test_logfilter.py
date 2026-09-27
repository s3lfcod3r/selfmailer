"""Befund 1: Geheimnisse duerfen nie in einer Logzeile stehen.

Der Feed-Token stand 16.366-mal in 24 Stunden im Klartext im Container-Log,
weil uvicorn die komplette Request-Line inklusive Query-String loggt. Hier
wird beides geprueft: das Maskieren selbst UND dass der Filter die
Logging-Neukonfiguration von uvicorn ueberlebt (uvicorn ruft beim Start
logging.config.dictConfig auf).
"""
from __future__ import annotations

import logging
import logging.config

from app.core.logfilter import (
    SecretMaskFilter,
    install_secret_log_filter,
    mask_secrets,
)

GEHEIM = "Sg7xK2pQwerty-nicht-im-log"


def test_maskiert_alle_heiklen_parameter():
    for name in ("token", "write_token", "access_token", "code", "secret",
                 "password", "passwd", "csrf_token"):
        zeile = "GET /api/v1/dashboard/summary?" + name + "=" + GEHEIM + " HTTP/1.1"
        maskiert = mask_secrets(zeile)
        assert GEHEIM not in maskiert, name
        assert name + "=***" in maskiert, name


def test_maskiert_auch_url_kodiert_und_mitten_in_der_query():
    zeile = "GET /x?live=true&token%3D" + GEHEIM + "&folders=all HTTP/1.1"
    maskiert = mask_secrets(zeile)
    assert GEHEIM not in maskiert
    assert "token%3D***" in maskiert
    # Harmlose Parameter bleiben lesbar - das Log soll noch brauchbar sein.
    assert "live=true" in maskiert
    assert "folders=all" in maskiert


def test_laesst_harmlose_namen_in_ruhe():
    # Nur der genaue Name zaehlt: "tokenizer" ist kein Geheimnis.
    assert mask_secrets("?tokenizer=bert") == "?tokenizer=bert"
    assert mask_secrets("GET /api/health HTTP/1.1") == "GET /api/health HTTP/1.1"


def test_filter_maskiert_uvicorn_access_argumente():
    """uvicorn.access uebergibt den Pfad als ARGUMENT, nicht im msg-Text."""
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1234", "GET", "/api/v1/dashboard/summary?token=" + GEHEIM,
         "1.1", 200),
        None,
    )
    assert SecretMaskFilter().filter(record) is True
    fertig = record.getMessage()
    assert GEHEIM not in fertig
    assert "token=***" in fertig


def test_filter_ueberlebt_uvicorn_logging_konfiguration():
    """uvicorn ruft beim Start dictConfig - Filter AM LOGGER bleiben dabei."""
    from uvicorn.config import LOGGING_CONFIG

    install_secret_log_filter()
    logging.config.dictConfig(LOGGING_CONFIG)
    zugriff = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, SecretMaskFilter) for f in zugriff.filters), (
        "Filter nach dictConfig verloren - Token stuende wieder im Log"
    )

    # Und er wirkt auch wirklich: Zeile durch einen eigenen Handler fangen.
    gefangen: list[str] = []

    class Sammler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            gefangen.append(self.format(record))

    handler = Sammler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    zugriff.addHandler(handler)
    try:
        zugriff.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:1234", "GET",
            "/api/v1/calendar/export.ics?token=" + GEHEIM, "1.1", 200,
        )
    finally:
        zugriff.removeHandler(handler)
    assert gefangen, "keine Logzeile erzeugt"
    assert GEHEIM not in gefangen[0]
    assert "token=***" in gefangen[0]


def test_install_ist_idempotent():
    install_secret_log_filter()
    install_secret_log_filter()
    zugriff = logging.getLogger("uvicorn.access")
    anzahl = sum(isinstance(f, SecretMaskFilter) for f in zugriff.filters)
    assert anzahl == 1