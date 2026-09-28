"""SQLite-Engine und Session-Handling."""
from __future__ import annotations

import logging
import os
from collections.abc import Iterator

from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine

from .config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()
_db_path = _settings.db_path
# Verzeichnis sicherstellen (z. B. ./data oder /data).
_dir = os.path.dirname(_db_path)
if _dir:
    os.makedirs(_dir, exist_ok=True)

engine = create_engine(
    f"sqlite:///{_db_path}",
    echo=False,
    # timeout = SQLite busy_timeout (Sek.): bei gleichzeitigem Schreiben warten
    # statt sofort "database is locked" zu werfen.
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record) -> None:
    """Performance-PRAGMAs pro Verbindung.

    WAL ist DER Hebel hier: Leser blockieren Schreiber nicht mehr (UI-Abfragen
    laufen weiter, während der Hintergrund-Sync schreibt). synchronous=NORMAL ist
    mit WAL crash-sicher und spart die meisten fsyncs. busy_timeout verhindert
    sofortige Lock-Fehler; temp_store/cache_size halten Sortierungen im RAM.
    """
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.execute("PRAGMA cache_size=-16000")  # ~16 MB Page-Cache je Verbindung
    # KEIN foreign_keys=ON: das Schema definiert keine ON DELETE CASCADE-Regeln.
    # Mit erzwungenen FKs würde z. B. das Löschen eines Kontos mit Cache-Zeilen
    # an einer Constraint scheitern. Kinder werden im Code aufgeräumt.
    cur.close()


# Additive Spalten, die ggf. in einer älteren DB fehlen (SQLite kennt kein
# automatisches Hinzufügen über create_all). Tabelle -> [(Spalte, DDL-Typ)].
_ADDITIVE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "devicetoken": [("session_id", "VARCHAR DEFAULT ''")],
    "foldersync": [
        ("highest_modseq", "INTEGER DEFAULT 0"),
    ],
    "user": [
        ("token_version", "INTEGER DEFAULT 0"),
        ("totp_secret", "VARCHAR"),
        ("totp_enabled", "INTEGER DEFAULT 0"),
        ("totp_last_step", "INTEGER DEFAULT 0"),
        ("bday_cal_account_id", "INTEGER"),
        ("bday_cal_id", "VARCHAR"),
        ("hidden_cals", "VARCHAR"),
        ("ui_settings", "VARCHAR"),
    ],
    "mailaccount": [
        ("signature", "VARCHAR"),
        ("last_notified_unseen", "INTEGER DEFAULT -1"),
        ("spam_purge_days", "INTEGER DEFAULT -1"),
        ("trash_purge_days", "INTEGER DEFAULT -1"),
    ],
    "mailrule": [("delete_msg", "INTEGER DEFAULT 0")],
    # TEXT (nicht VARCHAR) → kein ''-Backfill: write_token bleibt NULL, bis der
    # User ihn erzeugt (leerer Token darf niemals matchen).
    # expires_at/last_used_at bleiben bewusst NULL, bis die v3-Migration bzw.
    # der erste Zugriff sie setzt (siehe _run_one_time_backfills).
    "feedtoken": [
        ("write_token", "TEXT"),
        ("expires_at", "DATETIME"),
        ("last_used_at", "DATETIME"),
        ("write_expires_at", "DATETIME"),
        ("write_last_used_at", "DATETIME"),
    ],
    "cachedmessage": [
        ("uidvalidity", "INTEGER DEFAULT 0"),
        ("detail_json", "VARCHAR"),
        ("message_id", "VARCHAR"),
        ("in_reply_to", "VARCHAR"),
        ("refs", "VARCHAR"),
        ("keywords", "VARCHAR"),
        # Wie oft in Folge kein Server-Treffer mehr (gegen flatternde Cluster-Server):
        # erst nach mehreren Fehlläufen wird die Mail wirklich aus dem Cache entfernt.
        ("miss_count", "INTEGER DEFAULT 0"),
        # Nutzer hat den Gelesen-Status selbst gesetzt → Sync überschreibt ihn
        # kurzzeitig nicht (siehe _STICKY_SECS in mail/cache.py).
        ("seen_sticky", "INTEGER DEFAULT 0"),
        # Wann die Sperre gesetzt wurde. NULL = abgelaufen.
        ("seen_sticky_at", "DATETIME"),
        # Vom Nutzer gelöscht/verschoben → ausgeblendet (Anti-„kommt wieder"-Tombstone).
        ("hidden", "INTEGER DEFAULT 0"),
        # Wann ausgeblendet wurde. NULL = abgelaufen (siehe _HIDDEN_SECS).
        ("hidden_at", "DATETIME"),
    ],
    "cachedfolder": [("special", "VARCHAR")],
    "calendarevent": [
        ("dav_account_id", "INTEGER"), ("external_uid", "VARCHAR"),
        ("source_key", "VARCHAR"), ("source_name", "VARCHAR"), ("source_color", "VARCHAR"),
    ],
    "davaccount": [
        ("oauth_client_id", "VARCHAR"),
        ("oauth_secret_enc", "VARCHAR"),
        ("oauth_refresh_enc", "VARCHAR"),
    ],
    "contact": [
        ("dav_account_id", "INTEGER"),
        ("external_uid", "VARCHAR"),
        ("birthday", "DATE"),
        ("mobile", "VARCHAR"),
        ("work_phone", "VARCHAR"),
        ("title", "VARCHAR"),
        ("website", "VARCHAR"),
        ("street", "VARCHAR"),
        ("postal_code", "VARCHAR"),
        ("city", "VARCHAR"),
        ("country", "VARCHAR"),
        ("bday_event_id", "VARCHAR"),
        ("photo", "VARCHAR"),
    ],
}


def _ensure_columns() -> None:
    """Fügt fehlende additive Spalten in bestehenden Tabellen nach.

    Idempotent: vorhandene Spalten werden übersprungen. Neue Tabellen legt
    create_all bereits vollständig an, daher hier nur Bestands-Tabellen.
    """
    with engine.begin() as conn:
        existing_tables = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        for table, columns in _ADDITIVE_COLUMNS.items():
            if table not in existing_tables:
                continue
            present = {
                row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))
            }
            for name, ddl_type in columns:
                just_added = name not in present
                if just_added:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))
                # Text-Spalten dürfen nicht NULL sein: Bestandszeilen, die über
                # ADD COLUMN (ohne DEFAULT) NULL bekamen, würden sonst die
                # Response-Schemas (str) brechen. Idempotenter Backfill.
                if ddl_type == "VARCHAR":
                    conn.execute(
                        text(f"UPDATE {table} SET {name} = '' WHERE {name} IS NULL")
                    )
                # Hier stand bis 1.85.0 ein Backfill `SET seen_sticky = seen`, der
                # jede damals gelesene Mail dauerhaft gegen Server-Updates sperrte.
                # Am 03.09.2026 fiel auf, was das anrichtet: zwei Mails, die auf dem
                # Gmail-Server ungelesen waren, blieben im Cache für immer "gelesen"
                # und fehlten im Badge. Der Backfill ist weg; seit 1.86.0 laeuft die
                # Sperre ohnehin nach _STICKY_SECS ab, und Zeilen ohne
                # seen_sticky_at gelten sofort als abgelaufen — der Altbestand heilt
                # sich also selbst, ohne Daten-Reparatur.


# Zusammengesetzte Indizes für die Hot-Path-Queries. Die einzelnen
# Field(index=True) decken Mehrspalten-Filter+Sortierung nicht gut ab; diese
# Composite-Indizes machen die Listen-, Detail- und Zähler-Abfragen schnell —
# besonders bei großen Ordnern (mehrere tausend Mails).
_INDEXES: list[str] = [
    # Listenanzeige + recent_unseen: WHERE account_id, folder ORDER BY sort_date DESC
    "CREATE INDEX IF NOT EXISTS ix_cm_acc_folder_sort "
    "ON cachedmessage (account_id, folder, sort_date DESC)",
    # Einzelmail (Detail/Flags/Löschen): WHERE account_id, folder, uid — deckt der
    # UNIQUE-Index ux_cm_acc_folder_uid ab (in _run_one_time_backfills v2 angelegt,
    # NACH dem einmaligen Entdoppeln; ein UNIQUE-Index auf noch doppelten Alt-Zeilen
    # würde beim Anlegen scheitern). Darum hier bewusst KEIN separater Index mehr.
    # Cross-Folder-Gelesen-Propagation (Gmail-Kopien) + Dublettencheck: WHERE account_id, message_id.
    # Läuft bei JEDEM Gelesen-Markieren → ohne Index Full-Scan über alle gecachten Zeilen.
    "CREATE INDEX IF NOT EXISTS ix_cm_acc_mid "
    "ON cachedmessage (account_id, message_id)",
    # FolderSync-Zähler: WHERE account_id (+ folder)
    "CREATE INDEX IF NOT EXISTS ix_fs_acc_folder "
    "ON foldersync (account_id, folder)",
    # Gecachte Ordnerliste: WHERE account_id ORDER BY idx
    "CREATE INDEX IF NOT EXISTS ix_cf_acc_idx "
    "ON cachedfolder (account_id, idx)",
]


def _ensure_indexes() -> None:
    """Legt die Composite-Indizes an (idempotent)."""
    with engine.begin() as conn:
        for ddl in _INDEXES:
            conn.execute(text(ddl))


def init_db() -> None:
    # Modelle importieren, damit SQLModel sie kennt.
    from .. import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _ensure_columns()
    _ensure_indexes()
    _run_one_time_backfills()


# v2-Backfill: doppelte Cache-Zeilen entfernen, dann UNIQUE-Index. Als Konstanten,
# damit der Test genau dieselben Statements prüft wie der Startup-Pfad.
# Behalten wird pro (account_id, folder, uid) die Zeile mit Nutzer-Zustand
# (hidden/seen_sticky), sonst die kleinste id. Leere uid bleibt unangetastet
# (dürfte es nicht geben, sync überspringt sie) — nie echte Mails kollabieren.
_DEDUP_CACHE_SQL = (
    "DELETE FROM cachedmessage WHERE uid != '' AND id NOT IN ("
    "  SELECT keep_id FROM ("
    "    SELECT id AS keep_id, ROW_NUMBER() OVER ("
    "      PARTITION BY account_id, folder, uid "
    "      ORDER BY (COALESCE(hidden,0) + COALESCE(seen_sticky,0)) DESC, id ASC"
    "    ) AS rn FROM cachedmessage WHERE uid != ''"
    "  ) WHERE rn = 1"
    ")"
)
_UNIQUE_CM_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_cm_acc_folder_uid "
    "ON cachedmessage (account_id, folder, uid)"
)


def _run_one_time_backfills() -> None:
    """Einmalige Daten-Reparaturen, gesteuert über PRAGMA user_version (jeder Schritt
    läuft nur einmal pro DB)."""
    with engine.begin() as conn:
        ver = int(conn.execute(text("PRAGMA user_version")).scalar() or 0)
    if ver < 1:
        # v1: falsch sortierte sort_date (gemischte Zeitzonen) aus date_str neu setzen.
        from ..mail.cache import backfill_sort_dates
        try:
            with Session(engine) as s:
                backfill_sort_dates(s)
        except Exception:  # noqa: BLE001 - Reparatur darf den Start nie blockieren
            logger.warning("Sortier-Datum-Backfill (v1) fehlgeschlagen", exc_info=True)
        with engine.begin() as conn:
            conn.execute(text("PRAGMA user_version = 1"))
        ver = 1
    if ver < 2:
        # v2: Doppelte Cache-Zeilen entfernen und einen UNIQUE-Index auf
        # (account_id, folder, uid) anlegen. Duplikate konnten vor dem Fix
        # entstehen, wenn zwei Syncs denselben Ordner gleichzeitig abglichen
        # (kein UNIQUE-Constraint, kein Upsert). Reihenfolge ist zwingend: ERST
        # entdoppeln, DANN den UNIQUE-Index — auf noch doppelten Zeilen würde das
        # CREATE UNIQUE INDEX scheitern. Beim Entdoppeln die Zeile behalten, die
        # NUTZER-ZUSTAND trägt (hidden/seen_sticky), sonst die kleinste id — damit
        # kein selbst gesetzter Gelesen-/Ausgeblendet-Status verloren geht. Zeilen
        # mit leerer uid (dürfte es nicht geben, sync überspringt sie) bleiben
        # unangetastet, um nie echte Mails zu kollabieren.
        try:
            with engine.begin() as conn:
                conn.execute(text(_DEDUP_CACHE_SQL))
                conn.execute(text(_UNIQUE_CM_INDEX_SQL))
                # Alt-Index (nicht-unique) ist durch den UNIQUE-Index abgelöst.
                conn.execute(text("DROP INDEX IF EXISTS ix_cm_acc_folder_uid"))
                conn.execute(text("PRAGMA user_version = 2"))
        except Exception:  # noqa: BLE001 - Reparatur darf den Start nie blockieren
            logger.warning("Cache-Entdoppelung/UNIQUE-Index (v2) fehlgeschlagen", exc_info=True)
        with engine.begin() as conn:
            ver = int(conn.execute(text("PRAGMA user_version")).scalar() or 0)
    if ver < 3:
        # v3 (1.96.0, Durchsicht-Befund 3): Feed-Token nur noch als SHA-256-Hash
        # speichern. Bestehende Klartext-Token werden gehasht UEBERNOMMEN, nicht
        # neu erzeugt - jeder vorhandene Abo-Link und jede eingerichtete
        # SelfDashboard-Kachel funktioniert danach unveraendert weiter. Nur
        # ANZEIGEN kann man den Token nicht mehr (Hash ist einweg).
        #
        # Erkennung "schon gehasht": genau 64 Zeichen aus [0-9a-f]. Ein
        # secrets.token_urlsafe(24) ist 32 Zeichen lang und enthaelt fast immer
        # Grossbuchstaben/-/_ - eine Verwechslung ist praktisch ausgeschlossen,
        # und der Test deckt beide Faelle ab. Dadurch ist der Schritt auch
        # idempotent, falls user_version verloren geht.
        #
        # expires_at = Migrationszeit + FEED_TOKEN_TTL_DAYS: Bestandstoken laufen
        # also nicht sofort ab, sondern bekommen das volle Fenster.
        try:
            _migrate_feed_tokens_to_hash()
            with engine.begin() as conn:
                conn.execute(text("PRAGMA user_version = 3"))
        except Exception:  # noqa: BLE001 - Reparatur darf den Start nie blockieren
            logger.warning("Feed-Token-Hashing (v3) fehlgeschlagen", exc_info=True)


def _looks_hashed(value: str) -> bool:
    """True, wenn der Wert schon ein SHA-256-Hex-Hash ist (64 Zeichen 0-9a-f)."""
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _migrate_feed_tokens_to_hash() -> None:
    """Hasht vorhandene Klartext-Feed-Token in place und setzt das Ablaufdatum.

    Bewusst als reines SQL ohne SQLModel-Objekte: laeuft beim Start, soll keine
    Modell-Importe/Validierungen brauchen und nur die noetigen Zeilen anfassen.
    """
    import datetime as _dt
    import hashlib

    now = _dt.datetime.now(_dt.timezone.utc)
    # Muss zu api/feeds.FEED_TOKEN_TTL_DAYS passen; hier hart, damit core/db
    # nicht auf die API-Schicht zeigt.
    expiry = now + _dt.timedelta(days=180)
    with engine.begin() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        if "feedtoken" not in tables:
            return
        rows = list(conn.execute(text("SELECT id, token, write_token FROM feedtoken")))
        migrated = 0
        for row_id, tok, wtok in rows:
            new_tok = tok
            if tok and not _looks_hashed(tok):
                new_tok = hashlib.sha256(tok.encode("utf-8")).hexdigest()
            new_wtok = wtok
            if wtok and not _looks_hashed(wtok):
                new_wtok = hashlib.sha256(wtok.encode("utf-8")).hexdigest()
            conn.execute(
                text(
                    "UPDATE feedtoken SET token = :t, write_token = :w, "
                    "expires_at = COALESCE(expires_at, :exp), "
                    "write_expires_at = CASE WHEN :w IS NULL THEN write_expires_at "
                    "ELSE COALESCE(write_expires_at, :exp) END "
                    "WHERE id = :id"
                ),
                {"t": new_tok, "w": new_wtok, "exp": expiry, "id": row_id},
            )
            if new_tok != tok or new_wtok != wtok:
                migrated += 1
    if migrated:
        logger.info("Feed-Token auf SHA-256 umgestellt: %d Zeile(n)", migrated)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
