"""SQLite-Engine und Session-Handling."""
from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine

from .config import get_settings

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
    laufen weiter, waehrend der Hintergrund-Sync schreibt). synchronous=NORMAL ist
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
    # Mit erzwungenen FKs wuerde z. B. das Loeschen eines Kontos mit Cache-Zeilen
    # an einer Constraint scheitern. Kinder werden im Code aufgeraeumt.
    cur.close()


# Additive Spalten, die ggf. in einer aelteren DB fehlen (SQLite kennt kein
# automatisches Hinzufuegen ueber create_all). Tabelle -> [(Spalte, DDL-Typ)].
_ADDITIVE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "user": [
        ("totp_secret", "VARCHAR"),
        ("totp_enabled", "INTEGER DEFAULT 0"),
        ("totp_last_step", "INTEGER DEFAULT 0"),
        ("bday_cal_account_id", "INTEGER"),
        ("bday_cal_id", "VARCHAR"),
        ("hidden_cals", "VARCHAR"),
    ],
    "mailaccount": [
        ("signature", "VARCHAR"),
        ("last_notified_unseen", "INTEGER DEFAULT -1"),
        ("spam_purge_days", "INTEGER DEFAULT -1"),
        ("trash_purge_days", "INTEGER DEFAULT -1"),
    ],
    "mailrule": [("delete_msg", "INTEGER DEFAULT 0")],
    "cachedmessage": [("detail_json", "VARCHAR")],
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
    ],
}


def _ensure_columns() -> None:
    """Fuegt fehlende additive Spalten in bestehenden Tabellen nach.

    Idempotent: vorhandene Spalten werden uebersprungen. Neue Tabellen legt
    create_all bereits vollstaendig an, daher hier nur Bestands-Tabellen.
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
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))
                # Text-Spalten duerfen nicht NULL sein: Bestandszeilen, die ueber
                # ADD COLUMN (ohne DEFAULT) NULL bekamen, wuerden sonst die
                # Response-Schemas (str) brechen. Idempotenter Backfill.
                if ddl_type == "VARCHAR":
                    conn.execute(
                        text(f"UPDATE {table} SET {name} = '' WHERE {name} IS NULL")
                    )


# Zusammengesetzte Indizes fuer die Hot-Path-Queries. Die einzelnen
# Field(index=True) decken Mehrspalten-Filter+Sortierung nicht gut ab; diese
# Composite-Indizes machen die Listen-, Detail- und Zaehler-Abfragen schnell —
# besonders bei grossen Ordnern (mehrere tausend Mails).
_INDEXES: list[str] = [
    # Listenanzeige + recent_unseen: WHERE account_id, folder ORDER BY sort_date DESC
    "CREATE INDEX IF NOT EXISTS ix_cm_acc_folder_sort "
    "ON cachedmessage (account_id, folder, sort_date DESC)",
    # Einzelmail (Detail/Flags/Loeschen): WHERE account_id, folder, uid
    "CREATE INDEX IF NOT EXISTS ix_cm_acc_folder_uid "
    "ON cachedmessage (account_id, folder, uid)",
    # FolderSync-Zaehler: WHERE account_id (+ folder)
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


def _run_one_time_backfills() -> None:
    """Einmalige Daten-Reparaturen, gesteuert ueber PRAGMA user_version (jeder Schritt
    laeuft nur einmal pro DB)."""
    with engine.begin() as conn:
        ver = int(conn.execute(text("PRAGMA user_version")).scalar() or 0)
    if ver < 1:
        # v1: falsch sortierte sort_date (gemischte Zeitzonen) aus date_str neu setzen.
        from ..mail.cache import backfill_sort_dates
        try:
            with Session(engine) as s:
                backfill_sort_dates(s)
        except Exception:  # noqa: BLE001 - Reparatur darf den Start nie blockieren
            pass
        with engine.begin() as conn:
            conn.execute(text("PRAGMA user_version = 1"))


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
