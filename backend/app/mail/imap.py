"""IMAP-Zugriff via imap-tools (synchron; FastAPI faehrt sync-Endpunkte im
Threadpool). Verbindungen sind kurzlebig: oeffnen, lesen, schliessen.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from collections.abc import Iterator
from email.message import EmailMessage

from imap_tools import MailBox, AND

from ..models import MailAccount

# IMAP-System-Flags (Backslash literal -> Raw-Strings).
SEEN = r"\Seen"
FLAGGED = r"\Flagged"


@contextmanager
def _mailbox(account: MailAccount, password: str, folder: str = "INBOX") -> Iterator[MailBox]:
    login = account.auth_user or account.email
    box = MailBox(account.imap_host, port=account.imap_port)
    box.login(login, password, initial_folder=folder)
    try:
        yield box
    finally:
        try:
            box.logout()
        except Exception:  # pragma: no cover - best effort
            pass


def list_folders(account: MailAccount, password: str) -> list[str]:
    """Listet Ordner robust: viele Server (z. B. web.de/Courier) zeigen INBOX-
    Unterordner nicht beim einfachen LIST "" "*". Daher mehrere Strategien
    kombinieren und deduplizieren.
    """
    seen: dict[str, None] = {}
    with _mailbox(account, password) as box:
        attempts = [
            lambda: box.folder.list("", "*"),            # alles ab Root
            lambda: box.folder.list("INBOX", "*"),        # INBOX-Unterordner explizit
            lambda: box.folder.list("", "*", subscribed_only=True),  # abonnierte
        ]
        for attempt in attempts:
            try:
                for f in attempt():
                    if f.name:
                        seen.setdefault(f.name, None)
            except Exception:  # noqa: BLE001 - einzelne LIST-Variante darf scheitern
                continue
    names = list(seen) or ["INBOX"]
    # INBOX immer zuerst, Rest alphabetisch (case-insensitiv).
    names.sort(key=lambda n: (n.upper() != "INBOX", n.lower()))
    return names


def folder_counts(account: MailAccount, password: str) -> list[dict]:
    """Wie list_folders, aber mit Ungelesen-/Gesamt-Zaehlern je Ordner (IMAP STATUS).

    Pro Ordner ein STATUS-Aufruf; bei sehr vielen Ordnern entsprechend langsamer.
    Fehler bei einzelnen Ordnern werden geschluckt (Zaehler dann 0).
    """
    seen: dict[str, None] = {}
    out: list[dict] = []
    with _mailbox(account, password) as box:
        attempts = [
            lambda: box.folder.list("", "*"),
            lambda: box.folder.list("INBOX", "*"),
            lambda: box.folder.list("", "*", subscribed_only=True),
        ]
        for attempt in attempts:
            try:
                for f in attempt():
                    if f.name:
                        seen.setdefault(f.name, None)
            except Exception:  # noqa: BLE001
                continue
        names = list(seen) or ["INBOX"]
        names.sort(key=lambda n: (n.upper() != "INBOX", n.lower()))
        for name in names:
            unseen = total = 0
            try:
                st = box.folder.status(name, ["MESSAGES", "UNSEEN"])
                total = int(st.get("MESSAGES", 0) or 0)
                unseen = int(st.get("UNSEEN", 0) or 0)
            except Exception:  # noqa: BLE001 - einzelner STATUS darf scheitern
                pass
            out.append({"name": name, "unseen": unseen, "total": total})
    return out


def _snippet(text: str, html: str) -> str:
    """Kurze 1-Zeilen-Vorschau aus Text- oder HTML-Body (Tags grob entfernt)."""
    src = text or re.sub(r"<[^>]+>", " ", html)
    return " ".join(src.split())[:160]


def list_messages(
    account: MailAccount, password: str, folder: str = "INBOX", limit: int = 50
) -> list[dict]:
    out: list[dict] = []
    # headers_only=False, damit Vorschau (snippet) und Anhang-Indikator verfügbar
    # sind (Synology-artige Liste). TODO: partial fetch für große Postfächer.
    with _mailbox(account, password, folder=folder) as box:
        for msg in box.fetch(AND(all=True), reverse=True, limit=limit, mark_seen=False):
            out.append(
                {
                    "uid": msg.uid or "",
                    "subject": msg.subject,
                    "from": msg.from_,
                    "date": msg.date_str,
                    "seen": SEEN in msg.flags,
                    "flagged": FLAGGED in msg.flags,
                    "snippet": _snippet(msg.text or "", msg.html or ""),
                    "has_attachments": bool(msg.attachments),
                }
            )
    return out


def get_message(account: MailAccount, password: str, uid: str, folder: str = "INBOX") -> dict | None:
    with _mailbox(account, password, folder=folder) as box:
        for msg in box.fetch(AND(uid=uid), mark_seen=False, limit=1):
            attachments = [
                {
                    "index": i,
                    "filename": att.filename or f"anhang-{i + 1}",
                    "content_type": att.content_type or "",
                    "size": att.size or len(att.payload or b""),
                }
                for i, att in enumerate(msg.attachments)
            ]
            return {
                "uid": msg.uid or "",
                "subject": msg.subject,
                "from": msg.from_,
                "to": list(msg.to),
                "message_id": msg.obj.get("Message-ID", "") or "",
                "date": msg.date_str,
                "seen": SEEN in msg.flags,
                "flagged": FLAGGED in msg.flags,
                "text": msg.text or "",
                "html": msg.html or "",
                "attachments": attachments,
            }
    return None


def get_attachment(
    account: MailAccount, password: str, uid: str, index: int, folder: str = "INBOX"
) -> tuple[str, str, bytes] | None:
    """Liefert (filename, content_type, bytes) des Anhangs mit gegebenem Index."""
    with _mailbox(account, password, folder=folder) as box:
        for msg in box.fetch(AND(uid=uid), mark_seen=False, limit=1):
            atts = list(msg.attachments)
            if 0 <= index < len(atts):
                att = atts[index]
                return (
                    att.filename or f"anhang-{index + 1}",
                    att.content_type or "application/octet-stream",
                    att.payload or b"",
                )
    return None


def set_flags(
    account: MailAccount,
    password: str,
    uid: str,
    folder: str = "INBOX",
    *,
    seen: bool | None = None,
    flagged: bool | None = None,
) -> None:
    """Setzt/entfernt \\Seen bzw. \\Flagged fuer eine Nachricht (nur uebergebene Flags)."""
    with _mailbox(account, password, folder=folder) as box:
        if seen is not None:
            box.flag(uid, SEEN, seen)
        if flagged is not None:
            box.flag(uid, FLAGGED, flagged)


def _trash_folder(box: MailBox, current: str) -> str | None:
    """Findet den Papierkorb: erst per SPECIAL-USE-Flag \\Trash, dann per Namensheuristik."""
    names: list[str] = []
    for f in box.folder.list():
        flags = " ".join(getattr(f, "flags", ()) or ()).lower()
        if "\\trash" in flags:
            return f.name
        names.append(f.name)
    for name in names:
        low = name.lower()
        if any(k in low for k in ("trash", "papierkorb", "deleted", "geloscht", "gelöscht")):
            return name
    return None


def delete_message(account: MailAccount, password: str, uid: str, folder: str = "INBOX") -> str:
    """In den Papierkorb verschieben; ist keiner da (oder schon im Papierkorb) -> hart loeschen."""
    with _mailbox(account, password, folder=folder) as box:
        trash = _trash_folder(box, folder)
        if trash and trash != folder:
            box.move(uid, trash)
            return "moved"
        box.delete(uid)
        return "deleted"


def move_message(account: MailAccount, password: str, uid: str, dest: str, folder: str = "INBOX") -> None:
    with _mailbox(account, password, folder=folder) as box:
        box.move(uid, dest)


def _delimiter(box: MailBox) -> str:
    """Server-Hierarchie-Trennzeichen (z. B. "/" oder ".") aus der Ordnerliste."""
    for f in box.folder.list():
        if getattr(f, "delim", None):
            return f.delim
    return "."


def create_folder(account: MailAccount, password: str, name: str, parent: str = "") -> str:
    """Legt einen (Unter-)Ordner an. parent leer = Top-Level. Liefert den vollen Namen."""
    with _mailbox(account, password) as box:
        full = f"{parent}{_delimiter(box)}{name}" if parent else name
        box.folder.create(full)
        return full


def delete_folder(account: MailAccount, password: str, name: str) -> None:
    with _mailbox(account, password) as box:
        box.folder.delete(name)


def _draft_folder(box: MailBox) -> str:
    """Findet den Entwürfe-Ordner (SPECIAL-USE \\Drafts oder Namensheuristik)."""
    names: list[str] = []
    for f in box.folder.list():
        flags = " ".join(getattr(f, "flags", ()) or ()).lower()
        if "\\drafts" in flags:
            return f.name
        names.append(f.name)
    for name in names:
        low = name.lower()
        if any(k in low for k in ("drafts", "entwurf", "entwürfe", "entwuerfe")):
            return name
    return ""


def save_draft(
    account: MailAccount,
    password: str,
    *,
    to: str = "",
    cc: str = "",
    subject: str = "",
    body: str = "",
    html: str = "",
) -> bool:
    """Legt eine ungesendete Nachricht als Entwurf im Entwürfe-Ordner ab (IMAP APPEND)."""
    msg = EmailMessage()
    msg["From"] = account.email
    if to:
        msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg.set_content(body or "")
    if html:
        msg.add_alternative(html, subtype="html")
    with _mailbox(account, password) as box:
        folder = _draft_folder(box)
        if not folder:
            return False
        box.append(msg.as_bytes(), folder, flag_set=["\\Draft"])
        return True


def rename_folder(account: MailAccount, password: str, old: str, new_name: str) -> str:
    """Benennt einen Ordner um (gleicher Eltern-Pfad, nur Anzeigename neu)."""
    with _mailbox(account, password) as box:
        delim = _delimiter(box)
        parent = old.rsplit(delim, 1)[0] if delim in old else ""
        new_path = f"{parent}{delim}{new_name}" if parent else new_name
        box.folder.rename(old, new_path)
        return new_path


# Standard-Unterordner unter INBOX (ASCII-Namen; Anzeige wird im Frontend lokalisiert).
DEFAULT_SUBFOLDERS = ["Sent", "Drafts", "Trash", "Spam", "Archive"]
_DEFAULT_KIND = {"Sent": "sent", "Drafts": "drafts", "Trash": "trash", "Spam": "spam", "Archive": "archive"}

# Sonderordner-Erkennung (DE+EN) — spiegelt frontend/src/lib/folders.ts.
_SPECIAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("inbox", re.compile(r"^inbox$", re.I)),
    ("drafts", re.compile(r"^(drafts?|entw[uü]rfe?|entwurf)$", re.I)),
    ("sent", re.compile(r"^(sent|sent items|gesendet|gesendete objekte)$", re.I)),
    ("spam", re.compile(r"^(spam|junk|junk[- ]?e-?mail|werbung)$", re.I)),
    ("trash", re.compile(r"^(trash|deleted|deleted items|papierkorb|gel[oö]schte? objekte)$", re.I)),
    ("archive", re.compile(r"^(archive|archiv|archiviert)$", re.I)),
]


def _special_kind(last_part: str) -> str | None:
    for kind, rx in _SPECIAL_PATTERNS:
        if rx.match(last_part):
            return kind
    return None


def apply_rules(account: MailAccount, password: str, rules: list) -> int:
    """Wendet Filterregeln auf den Posteingang an (Modus A). Erste passende Regel je
    Mail gewinnt. rules: Objekte mit .field/.value/.target_folder/.mark_read/.star/
    .enabled. Liefert die Anzahl betroffener Mails.
    """
    affected = 0
    with _mailbox(account, password, folder="INBOX") as box:
        matches: list[tuple[str, object]] = []
        for msg in box.fetch(AND(all=True), mark_seen=False, limit=200):
            for rule in rules:
                if not getattr(rule, "enabled", True) or not rule.value:
                    continue
                needle = rule.value.lower()
                if rule.field == "from":
                    hay = (msg.from_ or "").lower()
                elif rule.field == "to":
                    hay = " ".join(msg.to or ()).lower()
                elif rule.field == "subject":
                    hay = (msg.subject or "").lower()
                else:
                    hay = ""
                if needle in hay and msg.uid:
                    matches.append((msg.uid, rule))
                    break  # erste passende Regel gewinnt
        for uid, rule in matches:
            try:
                if getattr(rule, "star", False):
                    box.flag(uid, FLAGGED, True)
                if getattr(rule, "mark_read", False):
                    box.flag(uid, SEEN, True)
                if rule.target_folder:
                    box.move(uid, rule.target_folder)
                affected += 1
            except Exception:  # noqa: BLE001 - einzelne Aktion darf scheitern
                continue
    return affected


def ensure_default_folders(account: MailAccount, password: str) -> None:
    """Legt fehlende Standard-Ordner unter INBOX an (idempotent, best effort).

    Wichtig: Bringt der Server bereits einen Ordner DIESER Art mit (z. B. ein
    eigenes "Gesendet"/"Papierkorb"), wird KEIN zweiter angelegt — sonst stuenden
    Sonderordner doppelt in der Liste.
    """
    with _mailbox(account, password) as box:
        delim = _delimiter(box)
        existing = {f.name for f in box.folder.list()}
        # Welche Sonderarten gibt es schon (egal unter welchem Namen/Ebene)?
        present_kinds: set[str] = set()
        for name in existing:
            last = re.split(r"[/.]", name)[-1]
            k = _special_kind(last)
            if k:
                present_kinds.add(k)
        for sub in DEFAULT_SUBFOLDERS:
            kind = _DEFAULT_KIND.get(sub)
            if kind and kind in present_kinds:
                continue  # Server hat schon so einen Ordner -> kein Doppel
            full = f"INBOX{delim}{sub}"
            if full not in existing:
                try:
                    box.folder.create(full)
                    if kind:
                        present_kinds.add(kind)
                except Exception:  # noqa: BLE001 - Server darf einzelne Namen ablehnen
                    continue
