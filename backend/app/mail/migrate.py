"""Postfach-Migration.

Kopiert Mails aus einem Quellkonto (z. B. Synology MailPlus per IMAP) in die
"richtigen" Zielkonten: pro Mail wird anhand des Empfaengers (To/Cc bzw. der
Delivered-To-/Envelope-To-Header, die POP3-Sammler setzen) das Zielkonto
gewaehlt, dessen E-Mail-Adresse passt. Die Mail wird per IMAP APPEND mit
Originaldatum und \\Seen-Flag in den Zielordner gelegt.

dry_run=True zaehlt nur, ohne etwas zu schreiben — fuer eine gefahrlose
Vorschau des Routings.
"""
from __future__ import annotations

from imap_tools import AND, MailBox

from ..models import MailAccount

SEEN = r"\Seen"
# POP3-Sammler (wie Synology) hinterlegen die urspruengliche Zustelladresse hier.
_DEST_HEADERS = ("delivered-to", "envelope-to", "x-envelope-to", "x-original-to")


def _open(acc: MailAccount, password: str, folder: str = "INBOX") -> MailBox:
    box = MailBox(acc.imap_host, port=acc.imap_port)
    box.login(acc.auth_user or acc.email, password, initial_folder=folder)
    return box


def _recipients(msg) -> set[str]:
    """Alle plausiblen Empfaenger-Adressen einer Mail (klein geschrieben)."""
    out: set[str] = set()
    for addr in list(msg.to or ()) + list(msg.cc or ()):
        if addr:
            out.add(addr.strip().lower())
    headers = getattr(msg, "headers", {}) or {}
    for key in _DEST_HEADERS:
        for val in headers.get(key, ()):  # imap-tools: Header-Keys sind klein
            v = val.strip().lower()
            if v:
                out.add(v)
    return out


def migrate(
    source: MailAccount,
    source_password: str,
    source_folder: str,
    dests: list[tuple[MailAccount, str]],
    target_folder: str = "INBOX",
    *,
    dry_run: bool = True,
    limit: int = 500,
) -> dict:
    """Routet bis zu ``limit`` Mails aus ``source_folder`` an das passende Zielkonto.

    dests: Liste aus (Zielkonto, Klartext-Passwort).
    Liefert {counts:{email:n}, unmatched, moved, scanned, errors, dry_run}.
    """
    by_email = {d[0].email.lower(): d for d in dests}
    counts: dict[str, int] = {}
    unmatched = 0
    moved = 0
    scanned = 0
    errors: list[str] = []
    dest_boxes: dict[int, MailBox] = {}

    src = _open(source, source_password, source_folder)
    try:
        for msg in src.fetch(AND(all=True), reverse=True, limit=limit, mark_seen=False, bulk=False):
            scanned += 1
            match = next((by_email[e] for e in _recipients(msg) if e in by_email), None)
            if match is None:
                unmatched += 1
                continue
            dest_acc, dest_pw = match
            counts[dest_acc.email] = counts.get(dest_acc.email, 0) + 1
            if dry_run:
                continue
            try:
                if dest_acc.id not in dest_boxes:
                    dest_boxes[dest_acc.id] = _open(dest_acc, dest_pw)
                flags = [SEEN] if SEEN in (msg.flags or ()) else None
                dest_boxes[dest_acc.id].append(
                    msg.obj.as_bytes(), target_folder, dt=msg.date, flag_set=flags
                )
                moved += 1
            except Exception as exc:  # noqa: BLE001 - einzelne Mail darf scheitern
                if len(errors) < 5:
                    errors.append(f"{dest_acc.email}: {type(exc).__name__}: {exc}")
    finally:
        try:
            src.logout()
        except Exception:  # pragma: no cover - best effort
            pass
        for box in dest_boxes.values():
            try:
                box.logout()
            except Exception:  # pragma: no cover - best effort
                pass

    return {
        "counts": counts,
        "unmatched": unmatched,
        "moved": moved,
        "scanned": scanned,
        "errors": errors,
        "dry_run": dry_run,
    }
