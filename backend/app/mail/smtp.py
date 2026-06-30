"""SMTP-Versand via aiosmtplib (async)."""
from __future__ import annotations

import base64
import binascii
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import aiosmtplib

from ..models import MailAccount


def _decode_b64(raw: str) -> bytes:
    """Dekodiert base64; entfernt einen optionalen data:-URL-Präfix."""
    if "," in raw and raw.lstrip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError) as exc:  # pragma: no cover - defensiv
        raise ValueError(f"Ungültiger Anhang-Inhalt: {exc}") from exc


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
    html: str = "",
    read_receipt: bool = False,
    delivery_receipt: bool = False,
) -> bytes:
    """Versendet die Mail und gibt die ROHE Nachricht (Bytes) zurück — damit der
    Aufrufer eine Kopie in den Gesendet-Ordner legen kann (IMAP APPEND)."""
    msg = EmailMessage()
    msg["From"] = account.email
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    # Date + Message-ID explizit setzen: sonst fehlt der Kopie im Gesendet-Ordner
    # das Datum (Liste zeigt sonst keine Uhrzeit) und eine eindeutige ID.
    msg["Date"] = formatdate(localtime=True)
    _domain = account.email.rsplit("@", 1)[-1] if "@" in account.email else "selfmailer"
    msg["Message-ID"] = make_msgid(domain=_domain)
    if in_reply_to:
        # Verknüpft die Antwort mit dem Originalthread (Threading in Clients).
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    if read_receipt:
        msg["Disposition-Notification-To"] = account.email
    if delivery_receipt:
        msg["Return-Receipt-To"] = account.email
    msg.set_content(body)
    if html:
        # HTML-Variante als Alternative (Clients zeigen bevorzugt HTML).
        msg.add_alternative(html, subtype="html")

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

    # Port 465 = SMTPS (implizit TLS ab Verbindungsaufbau); 587/25 = STARTTLS.
    # use_tls und start_tls schließen sich gegenseitig aus.
    use_implicit_tls = account.smtp_port == 465
    await aiosmtplib.send(
        msg,
        hostname=account.smtp_host,
        port=account.smtp_port,
        username=login,
        password=password,
        use_tls=use_implicit_tls,
        start_tls=account.smtp_starttls and not use_implicit_tls,
        recipients=recipients,
    )
    return msg.as_bytes()
