"""Schlanker CalDAV/CardDAV-Lesezugriff (Client-Proxy, Variante B).

Bewusst minimal: kein Discovery-Tanz (well-known, current-user-principal).
Der User hinterlegt die direkte Collection-URL seines Servers (z. B.
Nextcloud ``.../remote.php/dav/calendars/<user>/personal/``). Wir machen ein
PROPFIND Depth:1, sammeln die Member-Hrefs und holen jede Ressource per GET.
Das genuegt fuer einen read-only Pull und bleibt serveruebergreifend robust.

Sicherheit: TLS-Zertifikate werden geprueft (httpx-Default). Credentials nur
transient im Speicher; persistiert wird ausschliesslich Fernet-verschluesselt.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx

from ..core.config import get_settings

_ALLOWED_SCHEMES = {"http", "https"}
# RFC 6598 Shared Address Space (CGNAT, z. B. Tailscale 100.64.0.0/10) — von
# ipaddress je nach Version nicht als is_private gefuehrt, daher explizit.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_CGNAT6 = ipaddress.ip_network("100::/64")  # IPv6 Discard-Only (RFC 6666)


class DavUrlError(ValueError):
    """Die Ziel-URL ist aus Sicherheitsgruenden nicht erlaubt (SSRF-Schutz)."""


def _ip_blocked(ip: ipaddress._BaseAddress, block_private: bool) -> bool:
    # IMMER blockieren: loopback (127/8, ::1), link-local inkl. Cloud-Metadata
    # (169.254.169.254, fe80::/10), multicast, unspecified, reserved.
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return True
    if ip in _CGNAT or ip in _CGNAT6:
        return block_private
    # Privat (10/8, 172.16/12, 192.168/16, fc00::/7) nur im strikten Modus.
    if ip.is_private:
        return block_private
    return False


def _validate_dav_url(url: str) -> None:
    """SSRF-Schutz: Schema pruefen und alle aufgeloesten IPs gegen die Blockliste.

    link-local/loopback/metadata werden immer abgelehnt; private LAN-Ziele nur,
    wenn ``SELFMAILER_DAV_BLOCK_PRIVATE=true`` gesetzt ist (untrusted Multi-User).
    """
    block_private = get_settings().dav_block_private
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise DavUrlError(f"Schema nicht erlaubt: {parsed.scheme or '(leer)'!r}")
    host = parsed.hostname
    if not host:
        raise DavUrlError("URL ohne Host")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 0, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise DavUrlError(f"Host nicht aufloesbar: {host}") from exc
    for *_rest, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if _ip_blocked(ip, block_private):
            raise DavUrlError(f"Interne/gesperrte Adresse blockiert: {host} → {ip}")

_PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:">'
    "<d:prop><d:getcontenttype/><d:getetag/><d:resourcetype/></d:prop>"
    "</d:propfind>"
)

_TIMEOUT = httpx.Timeout(20.0)


def _is_member(href: str, collection_path: str, content_type: str, is_collection: bool) -> bool:
    """Entscheidet, ob ein PROPFIND-Eintrag eine abrufbare Ressource ist."""
    if is_collection:
        return False
    if href.rstrip("/") == collection_path.rstrip("/"):
        return False
    if content_type:
        ct = content_type.lower()
        if "calendar" in ct or "vcard" in ct:
            return True
    return href.lower().endswith((".ics", ".vcf"))


def _parse_propfind(xml_text: str, collection_path: str) -> list[str]:
    """Extrahiert Member-Hrefs aus einer PROPFIND-Multistatus-Antwort."""
    ns = {"d": "DAV:"}
    root = ET.fromstring(xml_text)
    hrefs: list[str] = []
    for resp in root.findall("d:response", ns):
        href_el = resp.find("d:href", ns)
        if href_el is None or not href_el.text:
            continue
        href = href_el.text.strip()
        ctype_el = resp.find(".//d:getcontenttype", ns)
        content_type = (ctype_el.text or "") if ctype_el is not None else ""
        is_collection = resp.find(".//d:resourcetype/d:collection", ns) is not None
        if _is_member(href, collection_path, content_type, is_collection):
            hrefs.append(href)
    return hrefs


def fetch_collection(url: str, username: str, password: str) -> list[tuple[str, str]]:
    """Liest alle Ressourcen einer CalDAV/CardDAV-Collection.

    Returns eine Liste ``(href, body)``. Wirft httpx.HTTPError bei Netz-/Status-
    Fehlern, damit der aufrufende Endpoint sie als Sync-Fehler melden kann.
    """
    _validate_dav_url(url)
    auth = httpx.BasicAuth(username, password)
    collection_path = httpx.URL(url).path
    # follow_redirects=False: ein boesartiger/uebernommener Server koennte sonst
    # per 3xx auf eine interne Adresse umleiten und damit die Vorab-Pruefung
    # umgehen (SSRF via Redirect).
    with httpx.Client(auth=auth, timeout=_TIMEOUT, follow_redirects=False) as client:
        pf = client.request(
            "PROPFIND",
            url,
            content=_PROPFIND_BODY,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        )
        pf.raise_for_status()
        hrefs = _parse_propfind(pf.text, collection_path)

        results: list[tuple[str, str]] = []
        for href in hrefs:
            resource_url = urljoin(str(pf.url), href)
            # Ein boeser Server koennte absolute hrefs auf interne Ziele liefern.
            _validate_dav_url(resource_url)
            r = client.get(resource_url)
            r.raise_for_status()
            results.append((href, r.text))
    return results
