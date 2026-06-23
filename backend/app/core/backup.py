"""Konsistentes SQLite-Backup der SelfMailer-DB.

Warum die Online-Backup-API statt File-Copy: Ein einfaches Kopieren der
.db-Datei waehrend laufender Schreibvorgaenge (Hintergrund-Sync schreibt im WAL)
kann ein korruptes oder inkonsistentes Backup erzeugen. ``sqlite3.Connection.backup()``
nimmt einen transaktional konsistenten Snapshot — auch bei aktivem WAL und
gleichzeitigen Schreibern. Das Ergebnis ist eine einzelne, in sich geschlossene
.db-Datei (kein -wal/-shm noetig).

Ziel: ``<db-dir>/backups/selfmailer-YYYYMMDD-HHMMSS.db``.
Rotation: nur die letzten N Backups behalten (Default 7, per Env steuerbar).

Konfiguration (siehe core/config.py):
  SELFMAILER_BACKUP_ENABLED=1/0   -> Backup an/aus (Default an)
  SELFMAILER_BACKUP_KEEP=7        -> Anzahl behaltener Snapshots (<=0 = alle)
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from .config import get_settings

logger = logging.getLogger(__name__)

_BACKUP_DIR_NAME = "backups"
_FILE_PREFIX = "selfmailer-"
_FILE_SUFFIX = ".db"
_TIMESTAMP_FMT = "%Y%m%d-%H%M%S"


def _backup_dir(db_path: str) -> Path:
    """Verzeichnis fuer Backups neben der DB (``<db-dir>/backups``)."""
    parent = Path(db_path).resolve().parent
    return parent / _BACKUP_DIR_NAME


def _rotate(backup_dir: Path, keep: int) -> None:
    """Aeltere Backups loeschen, sodass nur die letzten ``keep`` bleiben.

    keep <= 0 deaktiviert die Rotation (alle behalten). Loeschfehler einzelner
    Dateien duerfen den Lauf nicht kippen — sie werden nur geloggt.
    """
    if keep <= 0:
        return
    snapshots = sorted(
        backup_dir.glob(f"{_FILE_PREFIX}*{_FILE_SUFFIX}"),
        key=lambda p: p.name,
    )
    # Nach Name sortiert == chronologisch (Zeitstempel im Dateinamen).
    excess = snapshots[:-keep] if len(snapshots) > keep else []
    for old in excess:
        try:
            old.unlink()
            logger.info("Altes DB-Backup entfernt: %s", old.name)
        except OSError:
            logger.warning("Altes DB-Backup konnte nicht geloescht werden: %s", old, exc_info=True)


def create_backup() -> Path | None:
    """Erzeugt EIN konsistentes Backup der DB und rotiert alte Snapshots.

    Returns: Pfad der erzeugten Backup-Datei oder ``None``, wenn Backup
    deaktiviert ist bzw. die Quell-DB (noch) nicht existiert. Wirft KEINE
    Exception fuer Routinefaelle; echte Fehler werden propagiert und vom
    Aufrufer (Scheduler) abgefangen/geloggt.
    """
    settings = get_settings()
    if not settings.backup_enabled:
        logger.debug("DB-Backup deaktiviert (SELFMAILER_BACKUP_ENABLED=0)")
        return None

    db_path = settings.db_path
    if not os.path.exists(db_path):
        logger.warning("DB-Backup uebersprungen: Quell-DB existiert nicht (%s)", db_path)
        return None

    backup_dir = _backup_dir(db_path)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime(_TIMESTAMP_FMT)
    target = backup_dir / f"{_FILE_PREFIX}{timestamp}{_FILE_SUFFIX}"

    # Online-Backup-API: konsistenter Snapshot trotz laufender Schreiber.
    # Read-only auf die Quelle (uri=...mode=ro) — wir veraendern die Live-DB nie.
    source_uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    source = sqlite3.connect(source_uri, uri=True, timeout=30)
    try:
        dest = sqlite3.connect(str(target), timeout=30)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()

    logger.info("DB-Backup erstellt: %s", target.name)
    _rotate(backup_dir, settings.backup_keep)
    return target
