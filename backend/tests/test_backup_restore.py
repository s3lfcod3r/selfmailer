"""Backups muessen sich auch wirklich wieder oeffnen lassen.

core/backup.py zieht Snapshots ueber die SQLite-Online-Backup-API. Dass dabei
eine Datei entsteht, war bisher die einzige Zusicherung - ob diese Datei eine
gueltige, vollstaendige Datenbank ist, hat niemand geprueft. Genau das ist der
Unterschied zwischen einem Backup und einer Datei, die aussieht wie eins.

Diese Tests spielen den Snapshot deshalb wirklich an:
  - PRAGMA integrity_check auf der Kopie,
  - dieselben Tabellen und Zeilen wie in der Quelle lesen,
  - und das Ganze einmal mit einem offenen, noch nicht committeten Schreiber
    auf der Quelle (WAL) - dort trennt sich File-Copy von echtem Online-Backup.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core import backup as backup_mod


def _quelle_anlegen(pfad: Path, zeilen: int = 50) -> None:
    """Kleine WAL-Datenbank mit bekanntem Inhalt."""
    con = sqlite3.connect(str(pfad))
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE post (id INTEGER PRIMARY KEY, betreff TEXT NOT NULL)")
        con.executemany(
            "INSERT INTO post (id, betreff) VALUES (?, ?)",
            [(i, f"Nachricht {i}") for i in range(zeilen)],
        )
        con.commit()
    finally:
        con.close()


class _Settings:
    """Nur die Felder, die create_backup() liest."""

    def __init__(self, db_path: str, keep: int = 7, enabled: bool = True) -> None:
        self.db_path = db_path
        self.backup_keep = keep
        self.backup_enabled = enabled


@pytest.fixture()
def db(tmp_path, monkeypatch):
    pfad = tmp_path / "selfmailer.db"
    _quelle_anlegen(pfad)
    monkeypatch.setattr(backup_mod, "get_settings", lambda: _Settings(str(pfad)))
    return pfad


def _oeffnen_und_pruefen(snapshot: Path) -> list[tuple[int, str]]:
    """Snapshot oeffnen, Integritaet pruefen, Inhalt zurueckgeben.

    Read-only geoeffnet: ein Test darf das Backup nicht nachtraeglich
    reparieren (SQLite wuerde beim Schreiben ggf. das WAL abspielen und so
    einen Mangel verdecken).
    """
    con = sqlite3.connect(f"file:{snapshot.as_posix()}?mode=ro", uri=True)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return con.execute("SELECT id, betreff FROM post ORDER BY id").fetchall()
    finally:
        con.close()


def test_backup_laesst_sich_oeffnen_und_hat_denselben_inhalt(db):
    snapshot = backup_mod.create_backup()
    assert snapshot is not None and snapshot.exists()

    con = sqlite3.connect(str(db))
    try:
        erwartet = con.execute("SELECT id, betreff FROM post ORDER BY id").fetchall()
    finally:
        con.close()

    assert _oeffnen_und_pruefen(snapshot) == erwartet


def test_backup_bleibt_gueltig_waehrend_ein_schreiber_offen_ist(db):
    """Der eigentliche Grund fuer die Online-Backup-API.

    Waehrend der Snapshot laeuft, haengt eine nicht committete Transaktion im
    WAL. Ein blosses Kopieren der .db-Datei koennte hier eine halbe Zeile oder
    eine kaputte Seite mitnehmen; der Snapshot darf das nicht.
    """
    schreiber = sqlite3.connect(str(db))
    try:
        schreiber.execute("BEGIN")
        schreiber.executemany(
            "INSERT INTO post (id, betreff) VALUES (?, ?)",
            [(1000 + i, f"nicht committet {i}") for i in range(20)],
        )
        snapshot = backup_mod.create_backup()
        assert snapshot is not None
        zeilen = _oeffnen_und_pruefen(snapshot)
    finally:
        schreiber.rollback()
        schreiber.close()

    # Nur der committete Stand, keine haengende Transaktion.
    assert len(zeilen) == 50
    assert all(not b.startswith("nicht committet") for _id, b in zeilen)


def test_rotation_behaelt_die_juengsten_und_die_bleiben_lesbar(db, monkeypatch):
    """Rotation darf nicht die falschen Dateien wegwerfen.

    Die Zeitstempel im Dateinamen haben Sekundenaufloesung; drei Backups in
    derselben Sekunde wuerden dieselbe Datei ueberschreiben. Deshalb wird die
    Uhr hier gestellt statt gewartet.
    """
    monkeypatch.setattr(backup_mod, "get_settings", lambda: _Settings(str(db), keep=2))

    zeiten = ["20260101-000001", "20260101-000002", "20260101-000003"]

    class _Uhr:
        def __init__(self) -> None:
            self.i = 0

        def now(self):
            self.i += 1
            return self

        def strftime(self, _fmt):
            return zeiten[self.i - 1]

    monkeypatch.setattr(backup_mod, "datetime", _Uhr())

    erzeugt = [backup_mod.create_backup() for _ in zeiten]
    assert all(p is not None for p in erzeugt)

    verzeichnis = erzeugt[0].parent
    uebrig = sorted(p.name for p in verzeichnis.glob("selfmailer-*.db"))
    assert uebrig == ["selfmailer-20260101-000002.db", "selfmailer-20260101-000003.db"]

    # Und die ueberlebenden Snapshots sind weiterhin gueltige Datenbanken.
    for name in uebrig:
        assert len(_oeffnen_und_pruefen(verzeichnis / name)) == 50


def test_backup_abgeschaltet_erzeugt_nichts(db, monkeypatch):
    monkeypatch.setattr(backup_mod, "get_settings", lambda: _Settings(str(db), enabled=False))
    assert backup_mod.create_backup() is None


def test_fehlende_quell_db_ist_kein_absturz(tmp_path, monkeypatch):
    fehlt = tmp_path / "gibtsnicht.db"
    monkeypatch.setattr(backup_mod, "get_settings", lambda: _Settings(str(fehlt)))
    assert backup_mod.create_backup() is None
