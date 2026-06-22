"""Pydantic-Schemas fuer Requests/Responses.

Wichtig: Response-Schemas geben NIE secret_enc oder Passwoerter aus.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from .models import DavKind, Protocol, Role


# ---- Auth ----------------------------------------------------------------
class SetupRequest(BaseModel):
    username: str
    password: str = Field(min_length=8)
    display_name: str = ""
    admin_token: str = ""  # falls per Env gesetzt


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class PasswordReset(BaseModel):
    """Admin setzt das Passwort eines Users zurueck. Im Body (NICHT als Query-
    Parameter), damit das Passwort nicht in Server-/Proxy-Logs landet."""
    new_password: str = Field(min_length=8)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginResponse(BaseModel):
    """Login-Antwort: entweder fertiges Token ODER 2FA-Anforderung.

    Bei aktiver 2FA bleibt access_token leer, needs_totp=True und mfa_token traegt
    den kurzlebigen Zwischen-Token fuer POST /auth/login/totp.
    """
    access_token: str = ""
    token_type: str = "bearer"
    needs_totp: bool = False
    mfa_token: str = ""


class TotpLoginRequest(BaseModel):
    mfa_token: str
    code: str  # 6-stelliger TOTP-Code ODER Backup-Code (XXXX-XXXX)


class TotpStatusOut(BaseModel):
    enabled: bool
    backup_codes_remaining: int = 0


class TotpSetupOut(BaseModel):
    """Antwort auf Einrichtungsstart: Secret (manuell) + otpauth-URI (QR)."""
    secret: str
    otpauth_uri: str


class TotpEnableRequest(BaseModel):
    code: str  # zur Bestaetigung, dass die App korrekt eingerichtet ist


class TotpEnableOut(BaseModel):
    backup_codes: list[str]  # nur EINMAL sichtbar – Nutzer muss sie sichern


class TotpDisableRequest(BaseModel):
    password: str  # zur Sicherheit erneute Passworteingabe


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: Role
    is_active: bool


# ---- Admin: User anlegen -------------------------------------------------
class UserCreate(BaseModel):
    username: str
    password: str = Field(min_length=8)
    display_name: str = ""
    role: Role = Role.user


# ---- Mailkonten ----------------------------------------------------------
class AccountCreate(BaseModel):
    label: str = ""
    email: EmailStr
    protocol: Protocol = Protocol.imap
    imap_host: str = ""
    imap_port: int = 993
    imap_ssl: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_starttls: bool = True
    auth_user: str = ""
    password: str  # Klartext nur im Request; wird verschluesselt gespeichert


class AccountOut(BaseModel):
    id: int
    label: str
    email: str
    protocol: Protocol
    imap_host: str
    imap_port: int
    imap_ssl: bool = True
    smtp_host: str
    smtp_port: int
    smtp_starttls: bool = True
    auth_user: str = ""
    signature: str = ""


class AccountUpdate(BaseModel):
    """Aenderbare Felder eines Kontos. Alles optional (Patch-Semantik).

    password: nur setzen, wenn die Zugangsdaten geaendert werden sollen –
    wird dann verschluesselt in secret_enc abgelegt (kein direktes Feld).
    """
    label: str | None = None
    email: EmailStr | None = None
    protocol: Protocol | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_ssl: bool | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_starttls: bool | None = None
    auth_user: str | None = None
    signature: str | None = None
    password: str | None = None


# ---- Mail ----------------------------------------------------------------
class MessageHeader(BaseModel):
    uid: str
    subject: str
    from_: str = Field(alias="from")
    date: str
    seen: bool
    flagged: bool
    snippet: str = ""
    has_attachments: bool = False

    model_config = {"populate_by_name": True}


class AttachmentMeta(BaseModel):
    """Metadaten eines empfangenen Anhangs (ohne Bytes; Download separat)."""
    index: int
    filename: str
    content_type: str = ""
    size: int = 0


class AuthInfo(BaseModel):
    """Echtheits-/Spoofing-Check aus den Authentifizierungs-Headern."""
    spf: str | None = None
    dkim: str | None = None
    dmarc: str | None = None
    verdict: str = "unknown"        # pass | fail | unknown
    self_spoof: bool = False        # gibt vor, vom eigenen Konto zu kommen, ist es aber nicht
    from_domain: str = ""
    reasons: list[str] = []


class MessageDetail(MessageHeader):
    to: list[str] = []
    message_id: str = ""
    text: str = ""
    html: str = ""
    attachments: list[AttachmentMeta] = []
    auth: AuthInfo | None = None


class AttachmentIn(BaseModel):
    """Anhang im Sende-Request: Inhalt als base64 (ggf. mit data:-Praefix)."""
    filename: str
    content_type: str = "application/octet-stream"
    content_b64: str


class SendRequest(BaseModel):
    to: list[EmailStr]
    subject: str = ""
    body: str = ""                 # Plaintext-Teil (immer)
    html: str = ""                 # optionaler HTML-Teil (Rich-Text)
    cc: list[EmailStr] = []
    bcc: list[EmailStr] = []
    in_reply_to: str = ""
    read_receipt: bool = False     # Lesebestätigung anfordern
    delivery_receipt: bool = False  # Empfangsbestätigung anfordern
    attachments: list[AttachmentIn] = []


# ---- Filterregeln --------------------------------------------------------
RuleField = Literal["from", "from_domain", "to", "subject"]


class RuleCreate(BaseModel):
    field: RuleField = "from"
    value: str
    target_folder: str = ""
    mark_read: bool = False
    star: bool = False


class MigrateRequest(BaseModel):
    """Postfach-Migration: komplettes Quellkonto (alle Ordner) → Zielkonto."""
    dest_account_id: int
    target_prefix: str = ""              # optionaler Ziel-Elternordner (z. B. "Synology")
    dry_run: bool = True
    limit: int = Field(default=5000, ge=1, le=50000)  # max. Mails pro Ordner/Lauf


class TransferRequest(BaseModel):
    """Einzelne Mails (uids) oder ganzen Ordner (uids=None) in ein ANDERES Konto
    kopieren/verschieben."""
    source_folder: str = "INBOX"
    uids: list[str] | None = None        # None = ganzer Ordner
    dest_account_id: int
    dest_folder: str
    move: bool = False                   # True = nach Kopie aus Quelle löschen
    limit: int = Field(default=2000, ge=1, le=50000)


class BatchRequest(BaseModel):
    """Mehrere Mails desselben Ordners in einem Rutsch loeschen/verschieben."""
    folder: str = "INBOX"
    uids: list[str]
    dest: str | None = None              # Zielordner (nur beim Verschieben)


class RuleUpdate(BaseModel):
    """Teil-Update einer Regel (Bearbeiten). Nur gesetzte Felder werden geaendert."""
    field: RuleField | None = None
    value: str | None = None
    target_folder: str | None = None
    mark_read: bool | None = None
    star: bool | None = None
    enabled: bool | None = None


class RuleOut(BaseModel):
    id: int
    field: str
    value: str
    target_folder: str
    mark_read: bool
    star: bool
    enabled: bool
    position: int


# ---- Notizen -------------------------------------------------------------
class NoteCreate(BaseModel):
    title: str = ""
    body: str = ""
    color: str = ""
    pinned: bool = False


class NoteUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    color: str | None = None
    pinned: bool | None = None


class NoteOut(BaseModel):
    id: int
    title: str
    body: str
    color: str
    pinned: bool
    created_at: dt.datetime
    updated_at: dt.datetime


# ---- Kalender -----------------------------------------------------------
class EventCreate(BaseModel):
    title: str = ""
    description: str = ""
    location: str = ""
    start: dt.datetime
    end: dt.datetime
    all_day: bool = False


class EventUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    location: str | None = None
    start: dt.datetime | None = None
    end: dt.datetime | None = None
    all_day: bool | None = None


class EventOut(BaseModel):
    id: int
    title: str
    description: str
    location: str
    start: dt.datetime
    end: dt.datetime
    all_day: bool


# ---- Kontakte -----------------------------------------------------------
class ContactCreate(BaseModel):
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    mobile: str = ""
    work_phone: str = ""
    organization: str = ""
    title: str = ""
    website: str = ""
    street: str = ""
    postal_code: str = ""
    city: str = ""
    country: str = ""
    notes: str = ""
    birthday: dt.date | None = None


class ContactUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    mobile: str | None = None
    work_phone: str | None = None
    organization: str | None = None
    title: str | None = None
    website: str | None = None
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str | None = None
    notes: str | None = None
    birthday: dt.date | None = None


class ContactOut(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str
    phone: str
    mobile: str = ""
    work_phone: str = ""
    organization: str
    title: str = ""
    website: str = ""
    street: str = ""
    postal_code: str = ""
    city: str = ""
    country: str = ""
    notes: str
    birthday: dt.date | None = None


# ---- Aufgaben / Tasks ----------------------------------------------------
class TaskCreate(BaseModel):
    title: str = ""
    notes: str = ""
    due: dt.date | None = None


class TaskUpdate(BaseModel):
    title: str | None = None
    notes: str | None = None
    due: dt.date | None = None
    done: bool | None = None
    position: int | None = None


class TaskOut(BaseModel):
    id: int
    title: str
    notes: str
    due: dt.date | None
    done: bool
    position: int


# ---- DAV-Konten (externe CalDAV/CardDAV-Quellen) ------------------------
class DavAccountCreate(BaseModel):
    kind: DavKind = DavKind.caldav
    label: str = ""
    url: str
    username: str = ""
    password: str  # Klartext nur im Request; wird verschluesselt gespeichert


class DavAccountOut(BaseModel):
    id: int
    kind: DavKind
    label: str
    url: str
    username: str
    last_sync: dt.datetime | None
    last_status: str


class DavDiscoverRequest(BaseModel):
    """Auto-Erkennung: Server-Basis-URL + Zugang → Liste der Collections."""
    kind: DavKind = DavKind.caldav
    url: str               # Server-Basis (z. B. https://caldav.web.de)
    username: str = ""
    password: str          # Klartext nur im Request, wird NICHT gespeichert


class DiscoveredCollection(BaseModel):
    url: str
    name: str


class GoogleCalCreate(BaseModel):
    """Google-Kalender via OAuth (refresh_token-Verfahren). Klartext nur im
    Request; client_secret + refresh_token werden verschluesselt gespeichert."""
    email: str
    client_id: str
    client_secret: str
    refresh_token: str
    label: str = ""


class SyncResult(BaseModel):
    ok: bool
    imported: int = 0      # neu angelegt
    updated: int = 0       # aktualisiert
    removed: int = 0       # lokal entfernt, weil in Quelle verschwunden
    error: str = ""


# ---- Push (ntfy) --------------------------------------------------------
class PushConfigIn(BaseModel):
    ntfy_url: str = ""
    topic: str = ""
    enabled: bool = True


class PushConfigOut(BaseModel):
    enabled: bool
    ntfy_url: str
    topic: str


class FolderNotifyIn(BaseModel):
    account_id: int
    folders: list[str] = []   # vollstaendige Liste der zu benachrichtigenden Ordner


class DeviceTokenIn(BaseModel):
    token: str
    platform: str = "android"


# ---- Export-Feed-Token --------------------------------------------------
class FeedTokenOut(BaseModel):
    token: str
    calendar_url: str
    contacts_url: str
    dashboard_url: str = ""   # gebuendelte Mail-Uebersicht fuer ein externes Dashboard
