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
    # 2FA (TOTP). secret ist Fernet-VERSCHLUESSELT, solange totp_enabled.
    # totp_last_step verhindert Replay (jeder Zeitschritt nur einmal nutzbar).
    totp_secret: str = ""
    totp_enabled: bool = False
    totp_last_step: int = 0
    created_at: dt.datetime = Field(default_factory=_now)


class BackupCode(SQLModel, table=True):
    """Einmal-Wiederherstellungscode fuer 2FA (Argon2-gehasht, nie Klartext).

    Wird beim Aktivieren von 2FA erzeugt; ein Code ist nach Nutzung verbraucht
    (used=True). Neugenerieren loescht die alten Codes des Users.
    """

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True, foreign_key="user.id")
    code_hash: str
    used: bool = False
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
    signature: str = ""                 # E-Mail-Signatur (Plaintext, beim Schreiben angehaengt)
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
    phone: str = ""                       # Festnetz / privat
    mobile: str = ""                      # Mobil
    work_phone: str = ""                  # Geschaeftlich
    organization: str = ""
    title: str = ""                       # Position / Jobtitel
    website: str = ""
    street: str = ""                      # Adresse: Strasse + Nr.
    postal_code: str = ""                 # PLZ
    city: str = ""                        # Ort
    country: str = ""                     # Land
    notes: str = ""
    birthday: dt.date | None = None       # Geburtstag -> jaehrlicher Kalender-Eintrag
    # Herkunft: gesetzt, wenn der Kontakt aus einem externen CardDAV-Konto stammt.
    dav_account_id: int | None = Field(default=None, index=True, foreign_key="davaccount.id")
    external_uid: str = Field(default="", index=True)
    created_at: dt.datetime = Field(default_factory=_now)
    updated_at: dt.datetime = Field(default_factory=_now)


class Task(SQLModel, table=True):
    """Aufgabe / To-do eines Users (lokal, eigenstaendig nutzbar).

    Optionales Faelligkeitsdatum (due); erledigte Aufgaben bleiben erhalten
    (done=True), Reihenfolge ueber position (kleiner = oben).
    """

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True, foreign_key="user.id")
    title: str = ""
    notes: str = ""
    due: dt.date | None = None
    done: bool = False
    position: int = 0
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


class MailRule(SQLModel, table=True):
    """Filterregel eines Mailkontos: Bedingung (Feld enthält Wert) -> Aktionen.

    Wird beim Abrufen auf den Posteingang angewandt (Modus A). field ist eines von
    "from" | "to" | "subject"; bei Treffer werden die gesetzten Aktionen ausgeführt.
    """

    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(index=True, foreign_key="mailaccount.id")
    field: str = "from"                 # from | to | subject
    value: str = ""                     # Suchwert (Teilstring, case-insensitiv)
    target_folder: str = ""             # Zielordner für "Verschieben" (leer = nicht verschieben)
    mark_read: bool = False
    star: bool = False
    enabled: bool = True
    position: int = 0                   # Reihenfolge (kleiner = früher geprüft)
    created_at: dt.datetime = Field(default_factory=_now)


class CachedMessage(SQLModel, table=True):
    """Lokaler Cache eines Mail-Kopfs (fuer die schnelle Listenanzeige).

    Pro (account_id, folder, uid) eine Zeile. Inhalt/Anhaenge werden NICHT
    gespeichert — nur was die Liste braucht. Der Body wird beim Oeffnen weiterhin
    live geholt. Der Cache ist reine Beschleunigung; bei Zweifel wird live geladen.
    """

    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(index=True, foreign_key="mailaccount.id")
    folder: str = Field(index=True)
    uid: str = ""                         # IMAP-UID (stabil je UIDVALIDITY)
    subject: str = ""
    from_addr: str = ""
    date_str: str = ""                    # Anzeige-Datum (wie vom Server)
    sort_date: dt.datetime | None = Field(default=None, index=True)  # zum Sortieren
    seen: bool = False
    flagged: bool = False
    snippet: str = ""
    has_attachments: bool = False


class FolderSync(SQLModel, table=True):
    """Sync-Zustand eines Ordners: ob/was schon im Cache liegt.

    UIDVALIDITY-Wechsel = Server hat die UID-Nummerierung neu vergeben → Cache des
    Ordners verwerfen und neu aufbauen.
    """

    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(index=True, foreign_key="mailaccount.id")
    folder: str = Field(index=True)
    uidvalidity: int = 0
    total: int = 0
    unseen: int = 0
    last_sync: dt.datetime | None = None


class FeedToken(SQLModel, table=True):
    """Geheimer Token fuer abonnierbare Export-Feeds (ICS/vCard).

    Abo-Clients (Handy-Kalender) koennen keinen Bearer-Header senden, daher
    authentifiziert ein Token in der URL. Pro User genau ein Token; rotierbar.
    """

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True, unique=True, foreign_key="user.id")
    token: str = Field(index=True, unique=True)
    created_at: dt.datetime = Field(default_factory=_now)
