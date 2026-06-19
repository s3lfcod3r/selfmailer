"""SMTP-Versand via aiosmtplib (async)."""
from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib

from ..models import MailAccount


async def send_message(
    account: MailAccount,
    password: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    in_reply_to: str = "",
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
