"""Gemeinsame Bausteine fuer RFC 5545 (iCalendar) und RFC 6350 (vCard).

Beide Formate teilen dieselben Regeln fuer Content-Line-Folding (max. 75
Oktette je Zeile, Fortsetzung mit fuehrendem Space) und Text-Escaping
(Backslash, Komma, Semikolon, Zeilenumbruch). Genau dieser Code wird sowohl
beim Export (Build) als auch beim Import (Parse) gebraucht.
"""
from __future__ import annotations

import datetime as dt

CRLF = "\r\n"
_MAX_OCTETS = 75


def escape_text(value: str) -> str:
    """Escaped einen TEXT-Wert gemaess RFC 5545 3.3.11 / RFC 6350 3.4."""
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def unescape_text(value: str) -> str:
    """Kehrt escape_text um. Tolerant gegenueber unbekannten Sequenzen."""
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": "\n", "N": "\n"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def split_components(value: str, sep: str = ";") -> list[str]:
    """Trennt einen strukturierten Wert (z. B. vCard ``N``) an UN-escapten
    Separatoren. ``Mueller\\; Test;Anna`` -> ['Mueller\\; Test', 'Anna'].

    Die Komponenten bleiben escaped; unescape_text wird vom Aufrufer angewandt.
    """
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            buf.append(ch)
            buf.append(value[i + 1])
            i += 2
            continue
        if ch == sep:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def fold_line(line: str) -> str:
    """Faltet eine Content-Line auf max. 75 Oktette (UTF-8) pro physischer Zeile.

    Gefaltet wird an Byte-Grenzen, ohne Multibyte-Zeichen zu zerschneiden.
    """
    raw = line.encode("utf-8")
    if len(raw) <= _MAX_OCTETS:
        return line
    chunks: list[bytes] = []
    start = 0
    limit = _MAX_OCTETS
    while start < len(raw):
        end = min(start + limit, len(raw))
        # Nicht mitten in ein Multibyte-Zeichen schneiden (Continuation 10xxxxxx).
        while end < len(raw) and (raw[end] & 0xC0) == 0x80:
            end -= 1
        chunks.append(raw[start:end])
        start = end
        limit = _MAX_OCTETS - 1  # Folgezeilen tragen ein Space-Praefix.
    head = chunks[0].decode("utf-8")
    tail = [" " + c.decode("utf-8") for c in chunks[1:]]
    return CRLF.join([head, *tail])


def unfold(text: str) -> list[str]:
    """Macht Line-Folding rueckgaengig und liefert logische Zeilen."""
    physical = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    logical: list[str] = []
    for raw in physical:
        if raw[:1] in (" ", "\t") and logical:
            logical[-1] += raw[1:]
        else:
            logical.append(raw)
    return [ln for ln in logical if ln != ""]


def split_property(line: str) -> tuple[str, dict[str, str], str]:
    """Zerlegt eine Content-Line in (NAME, PARAMS, VALUE).

    Beispiel: ``DTSTART;VALUE=DATE:20260701`` -> ("DTSTART", {"VALUE":"DATE"},
    "20260701"). Tolerant: kein Doppelpunkt -> leerer Value.
    """
    colon = line.find(":")
    if colon == -1:
        return line.upper(), {}, ""
    head, value = line[:colon], line[colon + 1 :]
    parts = head.split(";")
    name = parts[0].upper()
    params: dict[str, str] = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return name, params, value


def fmt_datetime_utc(value: dt.datetime) -> str:
    """Formatiert als UTC-Zeitstempel ``YYYYMMDDTHHMMSSZ`` (RFC 5545 form 2)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    value = value.astimezone(dt.timezone.utc)
    return value.strftime("%Y%m%dT%H%M%SZ")


def fmt_date(value: dt.datetime) -> str:
    """Formatiert als reines Datum ``YYYYMMDD`` (fuer Ganztags-Termine)."""
    return value.strftime("%Y%m%d")


def parse_dt(value: str) -> dt.datetime | None:
    """Parst iCalendar DATE/DATE-TIME-Werte tolerant zu einem datetime.

    Akzeptiert ``YYYYMMDDTHHMMSSZ`` (UTC), ``YYYYMMDDTHHMMSS`` (floating, als
    UTC interpretiert) und ``YYYYMMDD`` (Datum -> Mitternacht UTC).
    """
    value = value.strip()
    try:
        if value.endswith("Z"):
            naive = dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ")
            return naive.replace(tzinfo=dt.timezone.utc)
        if "T" in value:
            naive = dt.datetime.strptime(value, "%Y%m%dT%H%M%S")
            return naive.replace(tzinfo=dt.timezone.utc)
        naive = dt.datetime.strptime(value, "%Y%m%d")
        return naive.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None
