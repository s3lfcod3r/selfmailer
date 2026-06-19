"""SMTP-Versand via aiosmtplib (async)."""
from __future__ import annotations

import base64
import binascii
from email.message import EmailMessage

import aiosmtplib

from ..models import MailAccount


def _decode_b64(raw: str) -> bytes:
    """Dekodiert base64; entfernt einen optionalen data:-URL-Praefix."""
    if "," in raw and raw.lstrip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError) as exc:  # pragma: no cover - defensiv
        raise ValueError(f"Ungueltiger Anhang-Inhalt: {exc}") from exc


async def send_message(
    account: MailAccount,
    password: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    in_reply_to: str = "",
    attachments: list[dict] | None = None,
) -> None:
    msg = EmailMessage()
    msg["From"] = account.email
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    if in_reply_to:
        # Verknuepft die Antwort mit dem Originalthread (Threading in Clients).
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)

    for att in attachments or []:
        data = _decode_b64(att["content_b64"])
        ctype = att.get("content_type") or "application/octet-stream"
        maintype, _, subtype = ctype.partition("/")
        msg.add_attachment(
            data,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=att.get("filename") or "anhang",
        )

    recipients = list(to) + list(cc or []) + list(bcc or [])
    login = account.auth_user or account.email

    await aiosmtplib.send(
        msg,
        hostname=account.smtp_host,
        port=account.smtp_port,
        username=login,
        password=password,
        start_tls=account.smtp_starttls,
        recipients=recipients,
    )
