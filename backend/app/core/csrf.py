"""CSRF-Schutz fuer Cookie-Sitzungen ueber Fetch-Metadata und Origin.

Warum SameSite=Lax nicht reicht (Durchsicht 2026-10-24): "Site" meint fuer den
Browser Schema + registrierbare Domain - bei einer IP also die IP selbst, egal
welcher Port. Jede Seite auf 192.168.1.10:<anderer Port> (ein anderer Container
mit XSS-Luecke, ein kompromittiertes Dashboard) ist damit "same-site" und darf
ein Formular an SelfMailer schicken - mit Session-Cookie. Lesen kann sie die
Antwort nicht (CORS), ausloesen aber schon (logout-all, Feed-Token rotieren ...).

Regel fuer schreibende Anfragen, die sich per Session-Cookie anmelden:
  1. Sec-Fetch-Site same-origin/none -> erlaubt (der Browser bestaetigt es).
  2. sonst muss Origin vertrauenswuerdig sein: eigener Host, BASE_URL oder ein
     Eintrag aus CORS_ORIGINS.
  3. weder Sec-Fetch-Site noch Origin -> kein Browser (curl, Skripte) -> erlaubt.

Nicht geprueft wird, was ein fremder Browser-Tab gar nicht mitschicken kann:
Bearer-Token (APK) und Anfragen ohne Session-Cookie (Feed-/Schreib-Token der
Dashboard-Kachel - die muss der Angreifer ohnehin kennen).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_TRUSTED_FETCH_SITES = frozenset({"same-origin", "none"})


def normalize_origin(value: str | None) -> str:
    """'https://Mail.Example.org/pfad' -> 'https://mail.example.org'. Leer bei Unsinn."""
    raw = (value or "").strip()
    if not raw or raw.lower() == "null":
        return ""
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}".lower()


def trusted_origins(*sources: Iterable[str] | str) -> frozenset[str]:
    """Vertrauenswuerdige Origins aus Einzelwerten und Listen bauen (leere fallen weg)."""
    out: set[str] = set()
    for src in sources:
        for value in ([src] if isinstance(src, str) else src):
            origin = normalize_origin(value)
            if origin:
                out.add(origin)
    return frozenset(out)


def _bearer(headers: Mapping[str, str]) -> bool:
    scheme, _, token = (headers.get("authorization") or "").partition(" ")
    return scheme.lower() == "bearer" and bool(token.strip())


def check_request(
    method: str,
    headers: Mapping[str, str],
    cookies: Mapping[str, str],
    *,
    cookie_name: str,
    trusted: frozenset[str],
) -> str | None:
    """None = erlaubt, sonst ein kurzer Grund fuer das Log.

    ``headers`` muss Gross-/Kleinschreibung ignorieren (Starlette-Headers tun das).
    """
    if method.upper() in _SAFE_METHODS:
        return None
    # Bearer gewinnt in deps.request_token vor dem Cookie - und ein fremder
    # Browser-Tab kann keinen Authorization-Header setzen (CORS-Preflight).
    if _bearer(headers) or not cookies.get(cookie_name):
        return None

    fetch_site = (headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site in _TRUSTED_FETCH_SITES:
        return None

    raw_origin = headers.get("origin")
    if raw_origin is None and not fetch_site:
        return None  # kein Browser

    origin = normalize_origin(raw_origin)
    if origin and origin in trusted:
        return None
    # Eigener Host: vergleicht nur host[:port], nicht das Schema - hinter einem
    # TLS-Proxy spricht der Browser https, beim Backend kommt http an.
    host = (headers.get("host") or "").strip().lower()
    if origin and host and urlsplit(origin).netloc == host:
        return None
    return f"Origin {raw_origin!r}, Sec-Fetch-Site {fetch_site or '-'}"
