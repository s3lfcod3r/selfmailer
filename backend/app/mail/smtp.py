"""SMTP-Versand via aiosmtplib (async)."""
from __future__ import annotations

import base64
import binascii
from email.message import EmailMessage, Message
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid, parseaddr

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
        # Bittet den Empfänger-Client, beim Öffnen eine Lesebestätigung zu schicken.
        msg["Disposition-Notification-To"] = account.email
    if delivery_receipt:
        # Alt-Header (kaum ein Server wertet ihn aus) — die echte Zustellbestätigung
        # läuft über SMTP-DSN (NOTIFY), siehe _send unten. Header bleibt als Fallback.
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

    if delivery_receipt:
        # Echte Zustellbestätigung: SMTP-DSN mit NOTIFY. Der EMPFANGENDE Server
        # schickt dann eine Statusmeldung zurück (zugestellt/verzögert/gescheitert).
        await _send_with_dsn(account, password, msg, recipients)
    else:
        await _send(account, password, msg, recipients)
    return msg.as_bytes()


def _tls_mode(account: MailAccount) -> tuple[bool, bool]:
    """(use_tls, start_tls) — Port 465 = SMTPS (implizit TLS); 587/25 = STARTTLS.
    use_tls und start_tls schließen sich gegenseitig aus."""
    use_implicit_tls = account.smtp_port == 465
    return use_implicit_tls, (account.smtp_starttls and not use_implicit_tls)


async def _send(account: MailAccount, password: str, msg, recipients: list[str]) -> None:
    """Standard-Versand ohne DSN (der überwiegende Fall)."""
    use_tls, start_tls = _tls_mode(account)
    await aiosmtplib.send(
        msg,
        hostname=account.smtp_host,
        port=account.smtp_port,
        username=account.auth_user or account.email,
        password=password,
        use_tls=use_tls,
        start_tls=start_tls,
        recipients=recipients,
    )


async def _send_with_dsn(account: MailAccount, password: str, msg, recipients: list[str]) -> None:
    """Versand mit SMTP-DSN (NOTIFY=SUCCESS,DELAY,FAILURE). Kann der Server keine
    DSN, wird ganz normal ohne NOTIFY gesendet (kein Fehler für den Nutzer)."""
    use_tls, start_tls = _tls_mode(account)
    client = aiosmtplib.SMTP(
        hostname=account.smtp_host,
        port=account.smtp_port,
        use_tls=use_tls,
        start_tls=start_tls,
    )
    async with client:
        await client.login(account.auth_user or account.email, password)
        try:
            dsn_ok = client.supports_extension("dsn")
        except Exception:  # noqa: BLE001 - defensiv: Ältere/abweichende Server
            dsn_ok = False
        mail_options = ["RET=HDRS"] if dsn_ok else []
        rcpt_options = ["NOTIFY=SUCCESS,DELAY,FAILURE"] if dsn_ok else []
        await client.sendmail(
            account.email,
            recipients,
            msg.as_bytes(),
            mail_options=mail_options,
            rcpt_options=rcpt_options,
        )


async def send_mdn(
    account: MailAccount,
    password: str,
    *,
    to: str,
    original_message_id: str = "",
    original_subject: str = "",
    original_date: str = "",
) -> None:
    """Sendet eine Lesebestätigung (MDN, RFC 8098) an die anfordernde Adresse.

    Wird ausgelöst, wenn der Nutzer eine empfangene Mail, die eine Lesebestätigung
    anfordert, bestätigt. Format: multipart/report mit menschlich lesbarem Teil und
    maschinenlesbarem message/disposition-notification-Teil."""
    addr = parseaddr(to)[1]
    if not addr or "@" not in addr:
        raise ValueError("Keine gültige Empfängeradresse für die Lesebestätigung")

    root = MIMEMultipart("report", report_type="disposition-notification")
    root["From"] = account.email
    root["To"] = addr
    subj = original_subject or ""
    root["Subject"] = f"Gelesen: {subj}" if subj else "Lesebestätigung"
    root["Date"] = formatdate(localtime=True)
    _domain = account.email.rsplit("@", 1)[-1] if "@" in account.email else "selfmailer"
    root["Message-ID"] = make_msgid(domain=_domain)
    if original_message_id:
        root["In-Reply-To"] = original_message_id
        root["References"] = original_message_id

    human = (
        f"Dies ist eine Lesebestätigung für die Nachricht, die Sie an {account.email} "
        "gesendet haben"
        + (f" (Betreff: „{subj}“)" if subj else "")
        + (f" am {original_date}" if original_date else "")
        + ".\n\n"
        "Sie bestätigt lediglich, dass die Nachricht auf dem Rechner des Empfängers "
        "angezeigt wurde. Es ist nicht garantiert, dass der Inhalt gelesen oder "
        "verstanden wurde.\n"
    )
    root.attach(MIMEText(human, "plain", "utf-8"))

    fields = [
        "Reporting-UA: SelfMailer; SelfMailer",
        f"Final-Recipient: rfc822;{account.email}",
    ]
    if original_message_id:
        fields.append(f"Original-Message-ID: {original_message_id}")
    fields.append("Disposition: manual-action/MDN-sent-manually; displayed")
    mdn_part = Message()
    mdn_part.set_type("message/disposition-notification")
    mdn_part.set_payload("\r\n".join(fields) + "\r\n")
    root.attach(mdn_part)

    await _send(account, password, root, [addr])
