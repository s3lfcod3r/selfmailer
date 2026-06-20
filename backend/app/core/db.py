"""SQLite-Engine und Session-Handling."""
from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import text
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
    connect_args={"check_same_thread": False},
)


# Additive Spalten, die ggf. in einer aelteren DB fehlen (SQLite kennt kein
# automatisches Hinzufuegen ueber create_all). Tabelle -> [(Spalte, DDL-Typ)].
_ADDITIVE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "user": [
        ("totp_secret", "VARCHAR"),
        ("totp_enabled", "INTEGER DEFAULT 0"),
        ("totp_last_step", "INTEGER DEFAULT 0"),
    ],
    "mailaccount": [("signature", "VARCHAR")],
    "cachedmessage": [("detail_json", "VARCHAR")],
    "calendarevent": [("dav_account_id", "INTEGER"), ("external_uid", "VARCHAR")],
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


def init_db() -> None:
    # Modelle importieren, damit SQLModel sie kennt.
    from .. import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _ensure_columns()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
