"""Schlanker CalDAV/CardDAV-Lesezugriff (Client-Proxy, Variante B).

Bewusst minimal: kein Discovery-Tanz (well-known, current-user-principal).
Der User hinterlegt die direkte Collection-URL seines Servers (z. B.
Nextcloud ``.../remote.php/dav/calendars/<user>/personal/``). Wir machen ein
PROPFIND Depth:1, sammeln die Member-Hrefs und holen jede Ressource per GET.
Das genügt für einen read-only Pull und bleibt serverübergreifend robust.

Sicherheit: TLS-Zertifikate werden geprüft (httpx-Default). Credentials nur
transient im Speicher; persistiert wird ausschließlich Fernet-verschlüsselt.
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
# ipaddress je nach Version nicht als is_private geführt, daher explizit.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_CGNAT6 = ipaddress.ip_network("100::/64")  # IPv6 Discard-Only (RFC 6666)


class DavUrlError(ValueError):
    """Die Ziel-URL ist aus Sicherheitsgründen nicht erlaubt (SSRF-Schutz)."""


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
    """SSRF-Schutz: Schema prüfen und alle aufgelösten IPs gegen die Blockliste.

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

def validate_external_url(url: str) -> None:
    """Öffentliche SSRF-Prüfung für beliebige user-konfigurierte Ziel-URLs.

    Wird neben DAV auch für den ntfy-Push genutzt. Blockt immer
    loopback/link-local/Cloud-Metadata; private LAN-Ziele nur bei
    ``SELFMAILER_DAV_BLOCK_PRIVATE=true``. Raises ``DavUrlError``.
    """
    _validate_dav_url(url)


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


def _local(tag: str) -> str:
    """XML-Tag ohne Namespace ('{DAV:}href' -> 'href')."""
    return tag.split("}", 1)[-1]


_PRINCIPAL_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>'
)
_HOME_BODY_CAL = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    "<d:prop><c:calendar-home-set/></d:prop></d:propfind>"
)
_HOME_BODY_CARD = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">'
    "<d:prop><c:addressbook-home-set/></d:prop></d:propfind>"
)
_LIST_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:displayname/></d:prop></d:propfind>'
)


def _propfind(http: httpx.Client, url: str, body: str, depth: str) -> httpx.Response:
    """PROPFIND mit MANUELLEM Redirect-Folgen (jede Ziel-URL SSRF-geprüft)."""
    cur = url
    for _ in range(5):
        _validate_dav_url(cur)
        r = http.request(
            "PROPFIND", cur, content=body,
            headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"},
        )
        if r.status_code in (301, 302, 307, 308) and "location" in r.headers:
            cur = urljoin(str(r.url), r.headers["location"])
            continue
        r.raise_for_status()
        return r
    raise httpx.HTTPError("Zu viele Weiterleitungen")


def _first_href(xml_text: str, prop_local: str) -> str | None:
    """href innerhalb des erstgenannten Property-Elements (z. B. calendar-home-set)."""
    root = ET.fromstring(xml_text)
    for el in root.iter():
        if _local(el.tag) == prop_local:
            for child in el.iter():
                if _local(child.tag) == "href" and child.text:
                    return child.text.strip()
    return None


def _parse_collections(xml_text: str, base: str, want_contacts: bool) -> list[dict]:
    """Sammelt Kalender- (bzw. Adressbuch-)Collections aus einer Depth:1-Antwort."""
    want = "addressbook" if want_contacts else "calendar"
    root = ET.fromstring(xml_text)
    out: list[dict] = []
    for resp in root.iter():
        if _local(resp.tag) != "response":
            continue
        href: str | None = None
        name = ""
        is_target = False
        for el in resp.iter():
            ln = _local(el.tag)
            if ln == "href" and el.text and href is None:
                href = el.text.strip()
            elif ln == "displayname" and el.text:
                name = el.text.strip()
            elif ln == "resourcetype":
                for c in el.iter():
                    if _local(c.tag) == want:
                        is_target = True
        if href and is_target:
            url = urljoin(base, href)
            try:
                _validate_dav_url(url)
            except DavUrlError:
                continue
            out.append({"url": url, "name": name or href.rstrip("/").rsplit("/", 1)[-1]})
    return out


def discover_collections(base_url: str, username: str, password: str, *, want_contacts: bool = False) -> list[dict]:
    """Findet automatisch die Kalender-/Adressbuch-Collections eines Servers.

    Standard-CalDAV-Discovery: (well-known →) current-user-principal →
    calendar-home-set → Collections auflisten. Gibt ``[{url, name}, …]`` zurück.
    Der Nutzer muss so nur Server + E-Mail + (App-)Passwort eingeben.
    """
    _validate_dav_url(base_url)
    well_known = urljoin(base_url, "/.well-known/carddav" if want_contacts else "/.well-known/caldav")
    auth = httpx.BasicAuth(username, password)
    with httpx.Client(auth=auth, timeout=_TIMEOUT, follow_redirects=False) as http:
        # 1) current-user-principal (erst well-known, dann die Basis-URL)
        principal: str | None = None
        for start in (well_known, base_url):
            try:
                r = _propfind(http, start, _PRINCIPAL_BODY, "0")
            except httpx.HTTPError:
                continue
            p = _first_href(r.text, "current-user-principal")
            if p:
                principal = urljoin(str(r.url), p)
                break
        if not principal:
            principal = base_url
        _validate_dav_url(principal)
        # 2) home-set
        r = _propfind(http, principal, _HOME_BODY_CARD if want_contacts else _HOME_BODY_CAL, "0")
        home_prop = "addressbook-home-set" if want_contacts else "calendar-home-set"
        home_href = _first_href(r.text, home_prop)
        home = urljoin(str(r.url), home_href) if home_href else principal
        _validate_dav_url(home)
        # 3) Collections unter dem Home-Set auflisten
        r = _propfind(http, home, _LIST_BODY, "1")
        return _parse_collections(r.text, str(r.url), want_contacts)


def fetch_ics(url: str, username: str = "", password: str = "") -> str:
    """Liest einen einzelnen iCal-Feed (eine .ics-Datei) per GET.

    Für read-only Abos wie Googles geheime iCal-URL (kein OAuth nötig). Folgt
    Redirects MANUELL und prüft jede Ziel-URL gegen die SSRF-Blockliste.
    BasicAuth nur, wenn ein Benutzer gesetzt ist (Google-Feed braucht keinen).
    """
    auth = httpx.BasicAuth(username, password) if username else None
    cur = url
    with httpx.Client(auth=auth, timeout=_TIMEOUT, follow_redirects=False) as http:
        for _ in range(5):
            _validate_dav_url(cur)
            r = http.get(cur)
            if r.status_code in (301, 302, 307, 308) and "location" in r.headers:
                cur = urljoin(str(r.url), r.headers["location"])
                continue
            r.raise_for_status()
            return r.text
    raise httpx.HTTPError("Zu viele Weiterleitungen")


def fetch_collection(url: str, username: str, password: str, *, token: str | None = None) -> list[tuple[str, str]]:
    """Liest alle Ressourcen einer CalDAV/CardDAV-Collection.

    Auth wahlweise per BasicAuth (username/password) ODER OAuth-Bearer (``token``,
    z. B. Google). Returns eine Liste ``(href, body)``. Wirft httpx.HTTPError bei
    Netz-/Status-Fehlern, damit der aufrufende Endpoint sie als Sync-Fehler melden kann.
    """
    _validate_dav_url(url)
    auth = None if token else httpx.BasicAuth(username, password)
    headers = {"Authorization": f"Bearer {token}"} if token else None
    collection_path = httpx.URL(url).path
    # follow_redirects=False: ein bösartiger/übernommener Server könnte sonst
    # per 3xx auf eine interne Adresse umleiten und damit die Vorab-Prüfung
    # umgehen (SSRF via Redirect).
    with httpx.Client(auth=auth, headers=headers, timeout=_TIMEOUT, follow_redirects=False) as client:
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
            # Ein böser Server könnte absolute hrefs auf interne Ziele liefern.
            _validate_dav_url(resource_url)
            r = client.get(resource_url)
            r.raise_for_status()
            results.append((href, r.text))
    return results
