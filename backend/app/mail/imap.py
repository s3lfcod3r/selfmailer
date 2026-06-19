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
    with _mailbox(account, password) as box:
        return [f.name for f in box.folder.list()]


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
            }
    return None
