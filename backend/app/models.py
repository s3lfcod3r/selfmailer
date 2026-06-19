"""Datenbankmodelle (SQLModel). Eine Tabelle pro Klasse.

Sicherheits-Hinweis: MailAccount.secret_enc enthaelt die VERSCHLUESSELTEN
Zugangsdaten des fremden Postfachs. Niemals im Klartext speichern oder ausgeben.
"""
from __future__ import annotations

import datetime as dt
from enum import Enum

from sqlmodel import Field, SQLModel


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Role(str, Enum):
    admin = "admin"
    user = "user"


class Protocol(str, Enum):
    imap = "imap"
    pop3 = "pop3"


class DavKind(str, Enum):
    caldav = "caldav"
    carddav = "carddav"


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)  # i. d. R. E-Mail
    display_name: str = ""
    password_hash: str
    role: Role = Field(default=Role.user)
    is_active: bool = True
    created_at: dt.datetime = Field(default_factory=_now)


class MailAccount(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True, foreign_key="user.id")
    label: str = ""                     # Anzeigename des Kontos
    email: str                          # Absender-/Login-Adresse
    protocol: Protocol = Field(default=Protocol.imap)

    imap_host: str = ""
    imap_port: int = 993
    imap_ssl: bool = True

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_starttls: bool = True

    auth_user: str = ""                 # falls abweichend von email
    secret_enc: str                     # VERSCHLUESSELT (Fernet)
    created_at: dt.datetime = Field(default_factory=_now)


class Note(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True, foreign_key="user.id")
    title: str = ""
    body: str = ""
    color: str = ""                     # optionaler Akzent (Brand-Token-Name)
    pinned: bool = False
    created_at: dt.datetime = Field(default_factory=_now)
    updated_at: dt.datetime = Field(default_factory=_now)


class CalendarEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True, foreign_key="user.id")
    title: str = ""
    description: str = ""
    location: str = ""
    start: dt.datetime
    end: dt.datetime
    all_day: bool = False
    # Herkunft: gesetzt, wenn der Termin aus einem externen CalDAV-Konto stammt.
    dav_account_id: int | None = Field(default=None, index=True, foreign_key="davaccount.id")
    external_uid: str = Field(default="", index=True)
    created_at: dt.datetime = Field(default_factory=_now)
    updated_at: dt.datetime = Field(default_factory=_now)


class Contact(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True, foreign_key="user.id")
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    organization: str = ""
    notes: str = ""
    # Herkunft: gesetzt, wenn der Kontakt aus einem externen CardDAV-Konto stammt.
    dav_account_id: int | None = Field(default=None, index=True, foreign_key="davaccount.id")
    external_uid: str = Field(default="", index=True)
    created_at: dt.datetime = Field(default_factory=_now)
    updated_at: dt.datetime = Field(default_factory=_now)


class DavAccount(SQLModel, table=True):
    """Externes CalDAV/CardDAV-Konto eines Users (read-only Pull-Quelle).

    secret_enc enthaelt das VERSCHLUESSELTE Server-Passwort (Fernet), analog zu
    MailAccount. Niemals im Klartext speichern oder ausgeben.
    """

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True, foreign_key="user.id")
    kind: DavKind = Field(default=DavKind.caldav)
    label: str = ""
    url: str                              # direkte Collection-URL
    username: str = ""
    secret_enc: str                       # VERSCHLUESSELT (Fernet)
    last_sync: dt.datetime | None = None
    last_status: str = ""                 # "ok" oder Fehlertext
    created_at: dt.datetime = Field(default_factory=_now)


class FeedToken(SQLModel, table=True):
    """Geheimer Token fuer abonnierbare Export-Feeds (ICS/vCard).

    Abo-Clients (Handy-Kalender) koennen keinen Bearer-Header senden, daher
    authentifiziert ein Token in der URL. Pro User genau ein Token; rotierbar.
    """

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True, unique=True, foreign_key="user.id")
    token: str = Field(index=True, unique=True)
    created_at: dt.datetime = Field(default_factory=_now)
