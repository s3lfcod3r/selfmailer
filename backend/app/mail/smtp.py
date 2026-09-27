"""SMTP-Versand via aiosmtplib (async)."""
from __future__ import annotations

import base64
import binascii
import logging
import os
import socket
from email.message import EmailMessage, Message
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid, parseaddr

import aiosmtplib

from ..models import MailAccount
from ..dav.client import DavUrlError, resolve_pinned_host

logger = logging.getLogger(__name__)


class SMTPZielNichtErlaubt(RuntimeError):
    """Der konfigurierte SMTP-Host zeigt auf eine gesperrte Adresse (Befund 10)."""


# Zeitlimit fuer den TCP-Connect zum SMTP-Server (aiosmtplib-Default ist 60 s
# fuer den ganzen Dialog; hier geht es nur um das Aufbauen der Verbindung).
_SMTP_CONNECT_TIMEOUT = float(os.getenv("SELFMAILER_SMTP_TIMEOUT", "20") or 20)


def _socket_schliessen(sock: socket.socket) -> None:
    """Gepinnten Socket schliessen. aiosmtplib schliesst den Transport selbst;
    ein zweites close() auf einem bereits geschlossenen Socket ist harmlos."""
    try:
        sock.close()
    except OSError:  # pragma: no cover - defensiv
        pass


def _gepinnter_socket(account: MailAccount) -> socket.socket:
    """Socket, der zur vorab GEPRUEFTEN IP des SMTP-Hosts verbunden ist.

    Durchsicht 2026-09-27, Befund 10: api/accounts.py prueft smtp_host beim
    Speichern (SSRF/Loopback/Cloud-Metadata), verbunden wurde danach aber wieder
    per Hostname - ein zweites DNS konnte also auf eine interne Adresse zeigen.
    Jetzt wird EINMAL aufgeloest und genau zu dieser IP verbunden. Der Socket
    geht an aiosmtplib (sock=), hostname bleibt der echte Name, damit TLS-SNI
    und Zertifikatspruefung unveraendert gegen den Hostnamen laufen.
    """
    try:
        ip = resolve_pinned_host(account.smtp_host, account.smtp_port)
    except DavUrlError as exc:
        raise SMTPZielNichtErlaubt(f"SMTP-Server nicht erlaubt: {exc}") from exc
    sock = socket.create_connection((ip, account.smtp_port), timeout=_SMTP_CONNECT_TIMEOUT)
    # asyncio uebernimmt nur nicht-blockierende Sockets.
    sock.setblocking(False)
    return sock


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
    from_addr: str = "",
    from_name: str = "",
    extra_headers: dict[str, str] | None = None,
) -> bytes:
    """Versendet die Mail und gibt die ROHE Nachricht (Bytes) zurück — damit der
    Aufrufer eine Kopie in den Gesendet-Ordner legen kann (IMAP APPEND).

    ``from_addr``/``from_name``: optionale Absender-Identität/Alias. Der Aufrufer
    (send-Endpoint) MUSS vorher prüfen, dass die Adresse zu einer konfigurierten
    Identität des Kontos gehört — hier keine Spoofing-Prüfung mehr."""
    # Absenderadresse: Alias falls gesetzt, sonst die Konto-Adresse. CR/LF strippen
    # (Header-Injection-Schutz), Anzeigename optional.
    _from_email = (from_addr or account.email).replace("\r", " ").replace("\n", " ").strip()
    _from_disp = (from_name or "").replace("\r", " ").replace("\n", " ").strip()
    msg = EmailMessage()
    msg["From"] = formataddr((_from_disp, _from_email)) if _from_disp else _from_email
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    # Betreff defensiv von CR/LF befreien (Header-Injection-Schutz; EmailMessage faltet
    # ohnehin robust, aber ein explizites Strippen ist billig und eindeutig).
    msg["Subject"] = (subject or "").replace("\r", " ").replace("\n", " ")
    # Date + Message-ID explizit setzen: sonst fehlt der Kopie im Gesendet-Ordner
    # das Datum (Liste zeigt sonst keine Uhrzeit) und eine eindeutige ID.
    msg["Date"] = formatdate(localtime=True)
    _domain = _from_email.rsplit("@", 1)[-1] if "@" in _from_email else "selfmailer"
    msg["Message-ID"] = make_msgid(domain=_domain)
    if in_reply_to:
        # Verknüpft die Antwort mit dem Originalthread (Threading in Clients).
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    if read_receipt:
        # Bittet den Empfänger-Client, beim Öffnen eine Lesebestätigung zu schicken.
        msg["Disposition-Notification-To"] = _from_email
    if delivery_receipt:
        # Alt-Header (kaum ein Server wertet ihn aus) — die echte Zustellbestätigung
        # läuft über SMTP-DSN (NOTIFY), siehe _send unten. Header bleibt als Fallback.
        msg["Return-Receipt-To"] = _from_email
    # Zusatz-Header (z. B. Auto-Submitted für die Abwesenheitsnotiz) — CR/LF strippen.
    for k, v in (extra_headers or {}).items():
        msg[k] = str(v).replace("\r", " ").replace("\n", " ")
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
    # sock= statt host/port: die Verbindung steht schon auf der geprueften IP
    # (Befund 10). hostname bleibt gesetzt, weil aiosmtplib daraus den
    # TLS-server_hostname nimmt - Zertifikatspruefung wie vorher.
    sock = _gepinnter_socket(account)
    try:
        await aiosmtplib.send(
            msg,
            hostname=account.smtp_host,
            sock=sock,  # port bewusst NICHT setzen: aiosmtplib verbietet sock+port
            username=account.auth_user or account.email,
            password=password,
            use_tls=use_tls,
            start_tls=start_tls,
            recipients=recipients,
        )
    finally:
        _socket_schliessen(sock)


async def _send_with_dsn(account: MailAccount, password: str, msg, recipients: list[str]) -> None:
    """Versand mit SMTP-DSN (NOTIFY=SUCCESS,DELAY,FAILURE). Kann der Server keine
    DSN, wird ganz normal ohne NOTIFY gesendet (kein Fehler für den Nutzer)."""
    use_tls, start_tls = _tls_mode(account)
    sock = _gepinnter_socket(account)  # Befund 10: gepinnte IP
    client = aiosmtplib.SMTP(
        hostname=account.smtp_host,
        sock=sock,  # port bewusst NICHT setzen: aiosmtplib verbietet sock+port
        use_tls=use_tls,
        start_tls=start_tls,
    )
    try:
        await _dsn_dialog(client, account, password, msg, recipients)
    finally:
        _socket_schliessen(sock)


async def _dsn_dialog(client, account: MailAccount, password: str, msg, recipients: list[str]) -> None:
    """Der eigentliche DSN-Dialog - herausgezogen, damit der gepinnte Socket in
    jedem Fall geschlossen wird (Befund 10)."""
    async with client:
        await client.login(account.auth_user or account.email, password)
        try:
            dsn_ok = client.supports_extension("dsn")
        except Exception:  # noqa: BLE001 - defensiv: Ältere/abweichende Server
            dsn_ok = False
        mail_options = ["RET=HDRS"] if dsn_ok else []
        rcpt_options = ["NOTIFY=SUCCESS,DELAY,FAILURE"] if dsn_ok else []
        # Envelope-Sender = Adresse aus dem From-Header (ggf. Alias) — sonst gehen
        # DSN-Bounces an die Konto-Adresse statt an den Alias und SPF/DMARC-
        # Alignment kann beim Empfänger kippen.
        envelope_from = parseaddr(str(msg["From"] or ""))[1] or account.email
        await client.sendmail(
            envelope_from,
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
