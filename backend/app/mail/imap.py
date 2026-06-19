"""IMAP-Zugriff via imap-tools (synchron; FastAPI faehrt sync-Endpunkte im
Threadpool). Verbindungen sind kurzlebig: oeffnen, lesen, schliessen.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

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


def list_messages(
    account: MailAccount, password: str, folder: str = "INBOX", limit: int = 50
) -> list[dict]:
    out: list[dict] = []
    with _mailbox(account, password, folder=folder) as box:
        for msg in box.fetch(AND(all=True), reverse=True, limit=limit, mark_seen=False, headers_only=True):
            out.append(
                {
                    "uid": msg.uid or "",
                    "subject": msg.subject,
                    "from": msg.from_,
                    "date": msg.date_str,
                    "seen": SEEN in msg.flags,
                    "flagged": FLAGGED in msg.flags,
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
