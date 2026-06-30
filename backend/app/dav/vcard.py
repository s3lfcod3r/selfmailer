"""vCard (RFC 6350, Version 3.0) bauen und parsen — beschränkt auf die Felder
des SelfMailer-Adressbuchs (Name, E-Mail, Telefon, Organisation, Notiz).

build_vcards() erzeugt den Export-Feed; parse_vcards() liest fremde Adressbücher
beim CardDAV-Pull. Gemeinsames dict-Schema, damit api/contacts.py es direkt in
Contact überführen kann.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Iterable

from . import rfc


def _parse_bday(value: str) -> dt.date | None:
    """Liest ein BDAY-Feld (``YYYY-MM-DD`` oder ``YYYYMMDD``) als Datum.

    Zeit-/Zonenanteile nach einem ``T`` werden ignoriert; unlesbares -> None.
    """
    raw = value.strip().split("T", 1)[0]
    if not raw:
        return None
    digits = raw.replace("-", "")
    if len(digits) >= 8 and digits[:8].isdigit():
        try:
            return dt.date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            return None
    return None


def _contact_uid(ct: Any) -> str:
    ext = getattr(ct, "external_uid", "") or ""
    if ext:
        return ext
    return f"selfmailer-contact-{getattr(ct, 'id', 'x')}@selfmailer"


def _full_name(first: str, last: str, org: str) -> str:
    name = " ".join(p for p in (first, last) if p).strip()
    return name or org or "Unbenannt"


def build_vcards(contacts: Iterable[Any]) -> str:
    """Serialisiert Contact-ähnliche Objekte zu aneinandergereihten VCARDs."""
    lines: list[str] = []
    for ct in contacts:
        first = getattr(ct, "first_name", "") or ""
        last = getattr(ct, "last_name", "") or ""
        org = getattr(ct, "organization", "") or ""
        lines.append("BEGIN:VCARD")
        lines.append("VERSION:3.0")
        lines.append(f"UID:{_contact_uid(ct)}")
        # N: Family;Given;Additional;Prefix;Suffix
        lines.append(f"N:{rfc.escape_text(last)};{rfc.escape_text(first)};;;")
        lines.append(f"FN:{rfc.escape_text(_full_name(first, last, org))}")
        if getattr(ct, "email", ""):
            lines.append(f"EMAIL;TYPE=INTERNET:{rfc.escape_text(ct.email)}")
        if getattr(ct, "phone", ""):
            lines.append(f"TEL;TYPE=HOME:{rfc.escape_text(ct.phone)}")
        if getattr(ct, "mobile", ""):
            lines.append(f"TEL;TYPE=CELL:{rfc.escape_text(ct.mobile)}")
        if getattr(ct, "work_phone", ""):
            lines.append(f"TEL;TYPE=WORK:{rfc.escape_text(ct.work_phone)}")
        if org:
            lines.append(f"ORG:{rfc.escape_text(org)}")
        if getattr(ct, "title", ""):
            lines.append(f"TITLE:{rfc.escape_text(ct.title)}")
        if getattr(ct, "website", ""):
            lines.append(f"URL:{rfc.escape_text(ct.website)}")
        street = getattr(ct, "street", "") or ""
        city = getattr(ct, "city", "") or ""
        postal = getattr(ct, "postal_code", "") or ""
        country = getattr(ct, "country", "") or ""
        if street or city or postal or country:
            # ADR: PObox;ext;street;locality;region;postal;country
            lines.append(
                "ADR;TYPE=HOME:;;"
                f"{rfc.escape_text(street)};{rfc.escape_text(city)};;"
                f"{rfc.escape_text(postal)};{rfc.escape_text(country)}"
            )
        if getattr(ct, "notes", ""):
            lines.append(f"NOTE:{rfc.escape_text(ct.notes)}")
        bday = getattr(ct, "birthday", None)
        if bday:
            lines.append(f"BDAY:{bday.isoformat()}")
        lines.append("END:VCARD")
    return rfc.CRLF.join(rfc.fold_line(ln) for ln in lines) + (rfc.CRLF if lines else "")


def parse_vcards(text: str) -> list[dict[str, Any]]:
    """Liest VCARDs aus vCard-Text in dicts.

    Schluessel: uid, first_name, last_name, email, phone, organization, notes.
    """
    cards: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in rfc.unfold(text):
        upper = line.upper()
        if upper == "BEGIN:VCARD":
            current = {
                "uid": "",
                "first_name": "",
                "last_name": "",
                "email": "",
                "phone": "",
                "mobile": "",
                "work_phone": "",
                "organization": "",
                "title": "",
                "website": "",
                "street": "",
                "postal_code": "",
                "city": "",
                "country": "",
                "notes": "",
                "birthday": None,
            }
            continue
        if upper == "END:VCARD":
            if current is not None:
                cards.append(current)
            current = None
            continue
        if current is None:
            continue
        name, params, value = rfc.split_property(line)
        types = ",".join(params.values()).upper()
        if name == "UID":
            current["uid"] = value.strip()
        elif name == "N":
            # Family;Given;Additional;Prefix;Suffix (an un-escapten ; trennen)
            comps = rfc.split_components(value, ";")
            current["last_name"] = rfc.unescape_text(comps[0]) if len(comps) > 0 else ""
            current["first_name"] = rfc.unescape_text(comps[1]) if len(comps) > 1 else ""
        elif name == "FN" and not (current["first_name"] or current["last_name"]):
            current["first_name"] = rfc.unescape_text(value)
        elif name == "EMAIL" and not current["email"]:
            current["email"] = rfc.unescape_text(value)
        elif name == "TEL":
            tel = rfc.unescape_text(value)
            if "CELL" in types and not current["mobile"]:
                current["mobile"] = tel
            elif "WORK" in types and not current["work_phone"]:
                current["work_phone"] = tel
            elif not current["phone"]:
                current["phone"] = tel
        elif name == "ORG":
            current["organization"] = rfc.unescape_text(rfc.split_components(value, ";")[0])
        elif name == "TITLE" and not current["title"]:
            current["title"] = rfc.unescape_text(value)
        elif name == "URL" and not current["website"]:
            current["website"] = rfc.unescape_text(value)
        elif name == "ADR" and not (current["street"] or current["city"]):
            # PObox;ext;street;locality;region;postal;country
            comps = rfc.split_components(value, ";")
            def _c(i: int) -> str:
                return rfc.unescape_text(comps[i]) if len(comps) > i else ""
            current["street"] = _c(2)
            current["city"] = _c(3)
            current["postal_code"] = _c(5)
            current["country"] = _c(6)
        elif name == "NOTE":
            current["notes"] = rfc.unescape_text(value)
        elif name == "BDAY":
            current["birthday"] = _parse_bday(value)
    return cards
