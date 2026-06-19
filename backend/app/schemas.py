"""Pydantic-Schemas fuer Requests/Responses.

Wichtig: Response-Schemas geben NIE secret_enc oder Passwoerter aus.
"""
from __future__ import annotations

import datetime as dt

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


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


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
    smtp_host: str
    smtp_port: int


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


class MessageDetail(MessageHeader):
    to: list[str] = []
    message_id: str = ""
    text: str = ""
    html: str = ""
    attachments: list[AttachmentMeta] = []


class AttachmentIn(BaseModel):
    """Anhang im Sende-Request: Inhalt als base64 (ggf. mit data:-Praefix)."""
    filename: str
    content_type: str = "application/octet-stream"
    content_b64: str


class SendRequest(BaseModel):
    to: list[EmailStr]
    subject: str = ""
    body: str = ""
    cc: list[EmailStr] = []
    bcc: list[EmailStr] = []
    in_reply_to: str = ""
    attachments: list[AttachmentIn] = []


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
    organization: str = ""
    notes: str = ""


class ContactUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    organization: str | None = None
    notes: str | None = None


class ContactOut(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str
    phone: str
    organization: str
    notes: str


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


class SyncResult(BaseModel):
    ok: bool
    imported: int = 0      # neu angelegt
    updated: int = 0       # aktualisiert
    removed: int = 0       # lokal entfernt, weil in Quelle verschwunden
    error: str = ""


# ---- Export-Feed-Token --------------------------------------------------
class FeedTokenOut(BaseModel):
    token: str
    calendar_url: str
    contacts_url: str
