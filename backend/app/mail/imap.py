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
    account: MailAccount, password: str, folder: str = "INBOX", limit: int = 50, offset: int = 0
) -> list[dict]:
    out: list[dict] = []
    # bulk=True bündelt den Abruf in EINEN IMAP-FETCH statt eines pro Nachricht.
    # Auf entfernten Servern (z. B. web.de) ist die Round-Trip-Zeit der dominierende
    # Kostenfaktor: ~50 Einzel-Fetches → 1 Sammel-Fetch. headers_only bleibt aus,
    # damit Vorschau (snippet) und Anhang-Indikator erhalten bleiben.
    # offset/limit als Slice (auf die nach Datum absteigende Liste) = Paginierung
    # zum Weiterblättern bei grossen Postfaechern.
    page = slice(offset, offset + limit)
    with _mailbox(account, password, folder=folder) as box:
        for msg in box.fetch(AND(all=True), reverse=True, limit=page, mark_seen=False, bulk=True):
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
    """Findet den Entwürfe-Ordner (SPECIAL-USE \\Drafts oder Namensheuristik).

    Bevorzugt den SERVER-eigenen Ordner vor einem evtl. von uns frueher angelegten
    "INBOX/Drafts", damit Entwuerfe dort landen, wo der Anbieter sie erwartet.
    """
    delim = _delimiter(box)
    ours = {f"INBOX{delim}{s}" for s in DEFAULT_SUBFOLDERS}
    matches: list[str] = []
    for f in box.folder.list():
        flags = " ".join(getattr(f, "flags", ()) or ()).lower()
        if "\\drafts" in flags:
            return f.name
        low = f.name.lower()
        if any(k in low for k in ("drafts", "entwurf", "entwürfe", "entwuerfe")):
            matches.append(f.name)
    for name in matches:  # Server-eigenen Ordner bevorzugen
        if name not in ours:
            return name
    return matches[0] if matches else ""


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


def move_folder(account: MailAccount, password: str, name: str, new_parent: str) -> str:
    """Verschiebt einen Ordner unter new_parent (leer = oberste Ebene) per IMAP-
    RENAME. Der Blattname bleibt; nur der Eltern-Pfad aendert sich."""
    with _mailbox(account, password) as box:
        delim = _delimiter(box)
        leaf = name.rsplit(delim, 1)[-1] if delim in name else name
        new_path = f"{new_parent}{delim}{leaf}" if new_parent else leaf
        if new_path == name:
            return name
        box.folder.rename(name, new_path)
        return new_path


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


def apply_rules(account: MailAccount, password: str, rules: list) -> dict:
    """Wendet Filterregeln auf den Posteingang an (Modus A). Erste passende Regel je
    Mail gewinnt. rules: Objekte mit .field/.value/.target_folder/.mark_read/.star/
    .enabled. Liefert {affected, matched, errors} — affected = erfolgreich
    bearbeitete Mails, matched = passende Mails, errors = erste Fehlermeldungen
    (z. B. wenn der Zielordner nicht beschreibbar ist).
    """
    affected = 0
    errors: list[str] = []
    with _mailbox(account, password, folder="INBOX") as box:
        matches: list[tuple[str, object]] = []
        # Regeln prüfen nur Header (from/to/subject) → headers_only spart den
        # Body-Download; bulk=True bündelt die Round-Trips. reverse=True ist
        # ENTSCHEIDEND: ohne es holt imap-tools die AELTESTEN Mails — bei grossen
        # Postfaechern werden so die neuen (zu sortierenden) Mails nie gesehen.
        for msg in box.fetch(AND(all=True), reverse=True, mark_seen=False, limit=500, headers_only=True, bulk=True):
            for rule in rules:
                if not getattr(rule, "enabled", True) or not rule.value:
                    continue
                # Mehrere Begriffe kommagetrennt → trifft, wenn EINER vorkommt
                # (z. B. "slot, casino, bonus"). Einzelwert = Teilstring wie bisher.
                terms = [t.strip().lower() for t in rule.value.split(",") if t.strip()]
                if rule.field == "from":
                    # Adresse UND Anzeigename pruefen (vorher nur die Adresse —
                    # darum trafen Regeln auf den Klarnamen nicht).
                    fv = getattr(msg, "from_values", None)
                    name = getattr(fv, "name", "") if fv else ""
                    hay = f"{msg.from_ or ''} {name}".lower()
                elif rule.field == "from_domain":
                    # Nur die Absender-Domain (Teil nach dem letzten @), damit eine
                    # Regel auf die ganze (Haupt-)Domain matcht und verschiebt.
                    hay = (msg.from_ or "").rsplit("@", 1)[-1].lower()
                elif rule.field == "to":
                    hay = " ".join(msg.to or ()).lower()
                elif rule.field == "subject":
                    hay = (msg.subject or "").lower()
                else:
                    hay = ""
                if msg.uid and any(term in hay for term in terms):
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
            except Exception as exc:  # noqa: BLE001 - einzelne Aktion darf scheitern
                if len(errors) < 3:
                    errors.append(f"{getattr(rule, 'target_folder', '')}: {type(exc).__name__}: {exc}")
    return {"affected": affected, "matched": len(matches), "errors": errors}


def ensure_default_folders(account: MailAccount, password: str) -> None:
    """Bringt die Sonderordner in Ordnung (best effort).

    1. Frueher von UNS angelegte LEERE Doppel (INBOX/Sent|Drafts|Trash|Spam|Archive)
       werden geloescht, sobald der Server einen eigenen Ordner derselben Art mit-
       bringt. Server-Ordner haben immer Vorrang.
    2. Nur KOMPLETT fehlende Arten werden neu angelegt (z. B. Server mit nur INBOX),
       damit Entwuerfe/Papierkorb-Funktionen einen Zielordner haben.
    Es wird nie ein nicht-leerer Ordner geloescht.
    """
    with _mailbox(account, password) as box:
        delim = _delimiter(box)
        names = [f.name for f in box.folder.list()]
        ours = {f"INBOX{delim}{s}": _DEFAULT_KIND[s] for s in DEFAULT_SUBFOLDERS}

        def kind_of(name: str) -> str | None:
            return _special_kind(re.split(r"[/.]", name)[-1])

        # 1) Unsere Doppel auf den Server-Ordner gleicher Art zusammenfuehren:
        #    evtl. Inhalt dorthin VERSCHIEBEN (kein Verlust), dann unseren Ordner loeschen.
        for path, kind in ours.items():
            if path not in names:
                continue
            others = [n for n in names if n != path and kind_of(n) == kind]
            if not others:
                continue
            target = others[0]  # Server-eigener Ordner
            try:
                box.folder.set(path)
                uids = box.uids()
                if uids:
                    box.move(uids, target)  # Inhalt in den Server-Ordner
                box.folder.set("INBOX")
                box.folder.delete(path)
                names.remove(path)
            except Exception:  # noqa: BLE001 - best effort
                try:
                    box.folder.set("INBOX")
                except Exception:
                    pass

        # 2) Nur komplett fehlende Arten anlegen.
        present = {kind_of(n) for n in names if kind_of(n)}
        for sub in DEFAULT_SUBFOLDERS:
            kind = _DEFAULT_KIND[sub]
            if kind in present:
                continue
            full = f"INBOX{delim}{sub}"
            if full not in names:
                try:
                    box.folder.create(full)
                    names.append(full)
                    present.add(kind)
                except Exception:  # noqa: BLE001 - Server darf einzelne Namen ablehnen
                    continue
