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

from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import httpx

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
    auth = httpx.BasicAuth(username, password)
    collection_path = httpx.URL(url).path
    with httpx.Client(auth=auth, timeout=_TIMEOUT, follow_redirects=True) as client:
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
            r = client.get(resource_url)
            r.raise_for_status()
            results.append((href, r.text))
    return results
