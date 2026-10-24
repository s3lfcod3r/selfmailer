"""1.97.0: HTTP bleibt Standard, HTTPS ist waehlbar - und Betriebs-Haertung.

- Cookie "auto": ueber http ohne, ueber https mit Secure-Flag (beides parallel).
- Alte bool-Werte fuer SELFMAILER_COOKIE_SECURE gelten weiter.
- HSTS nur auf Wunsch und nur auf HTTPS-Antworten.
- app.serve: HTTP immer, HTTPS zusaetzlich mit Zertifikat; Proxy-Header nur
  von vertrauten Proxys; nur EIN Lifespan (kein doppelter Scheduler).
- Backup-Ordner frei waehlbar, DB-Datei 600.
"""
from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import main as main_mod
from app import serve as serve_mod
from app.core import backup as backup_mod
from app.core import db as db_mod
from app.core.config import Settings
from app.core.security import cookie_secure_for

SECRET = "x" * 40
ADMIN = {"username": "admin@self", "password": "supersecret-123"}


def _settings(**kw) -> Settings:
    return Settings(secret=SECRET, **kw)


# --- Cookie-Modus ---------------------------------------------------------------

@pytest.mark.parametrize("mode,scheme,erwartet", [
    ("auto", "http", False), ("auto", "https", True),
    ("true", "http", True), ("true", "https", True),
    ("false", "http", False), ("false", "https", False),
])
def test_cookie_secure_matrix(mode, scheme, erwartet):
    assert cookie_secure_for(mode, scheme) is erwartet


@pytest.mark.parametrize("roh,erwartet", [
    ("auto", "auto"), ("", "auto"), ("AUTO", "auto"),
    ("true", "true"), ("1", "true"), ("yes", "true"),
    ("false", "false"), ("0", "false"), ("False", "false"),
])
def test_alte_bool_werte_gelten_weiter(roh, erwartet):
    assert _settings(cookie_secure=roh).cookie_secure == erwartet


def test_cookie_secure_standard_ist_auto():
    assert _settings().cookie_secure == "auto"


def test_unsinniger_cookie_modus_wird_abgelehnt():
    with pytest.raises(ValidationError):
        _settings(cookie_secure="manchmal")


def test_tls_nur_mit_zertifikat_und_schluessel():
    with pytest.raises(ValidationError):
        _settings(tls_cert="/x.crt")
    with pytest.raises(ValidationError):
        _settings(tls_key="/x.key")
    assert _settings(tls_cert="/x.crt", tls_key="/x.key").tls_enabled is True
    assert _settings().tls_enabled is False


def _session_cookie(response) -> str:
    return next(h for h in response.headers.get_list("set-cookie") if h.startswith("sm_session="))


def test_login_ueber_http_ohne_secure_ueber_https_mit(client, admin):
    try:
        http = client.post("http://testserver/api/v1/auth/login", json=ADMIN)
        assert http.status_code == 200, http.text
        assert "secure" not in _session_cookie(http).lower()

        https = client.post("https://testserver/api/v1/auth/login", json=ADMIN)
        assert https.status_code == 200, https.text
        assert "secure" in _session_cookie(https).lower()
    finally:
        client.cookies.clear()


# --- HSTS -----------------------------------------------------------------------

def test_hsts_standardmaessig_aus(client):
    assert "strict-transport-security" not in client.get("https://testserver/api/health").headers


def test_hsts_nur_auf_https(client, monkeypatch):
    monkeypatch.setattr(main_mod.settings, "hsts_max_age", 31536000)
    assert client.get("https://testserver/api/health").headers["strict-transport-security"] == "max-age=31536000"
    assert "strict-transport-security" not in client.get("http://testserver/api/health").headers


# --- app.serve ------------------------------------------------------------------

def test_standard_nur_http_mit_proxy_headern_von_localhost():
    configs = serve_mod.build_configs(_settings())
    assert len(configs) == 1
    c = configs[0]
    assert c.port == 8090 and c.ssl_certfile is None
    assert c.proxy_headers is True and c.server_header is False
    assert c.forwarded_allow_ips == "127.0.0.1"


def test_vertraute_proxys_werden_durchgereicht():
    c = serve_mod.build_configs(_settings(trusted_proxies=" 10.0.0.5 , 172.17.0.0/16,"))[0]
    assert c.forwarded_allow_ips == "10.0.0.5,172.17.0.0/16"


def test_mit_zertifikat_zusaetzlich_https_ohne_zweiten_lifespan(tmp_path):
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    cert.write_text("x")
    key.write_text("x")
    configs = serve_mod.build_configs(_settings(tls_cert=str(cert), tls_key=str(key), tls_port=9443))
    assert [c.port for c in configs] == [8090, 9443]
    http, https = configs
    assert http.lifespan == "auto" and http.ssl_certfile is None
    assert https.lifespan == "off"  # Scheduler/DB-Init nur einmal
    assert https.ssl_certfile == str(cert) and https.ssl_keyfile == str(key)


def test_fehlende_zertifikatsdatei_bricht_mit_klarer_meldung_ab(tmp_path):
    with pytest.raises(SystemExit, match="TLS-Datei nicht gefunden"):
        serve_mod.build_configs(_settings(tls_cert=str(tmp_path / "fehlt.pem"), tls_key=str(tmp_path / "auch.pem")))


# --- Backup-Ordner + DB-Rechte ---------------------------------------------------

class _BackupSettings:
    def __init__(self, db_path: str, backup_dir: str) -> None:
        self.db_path = db_path
        self.backup_dir = backup_dir
        self.backup_keep = 7
        self.backup_enabled = True


def _db(pfad: Path) -> None:
    con = sqlite3.connect(str(pfad))
    con.execute("CREATE TABLE t (x INTEGER)")
    con.commit()
    con.close()


def test_backup_ordner_frei_waehlbar(tmp_path, monkeypatch):
    quelle = tmp_path / "data" / "selfmailer.db"
    quelle.parent.mkdir()
    _db(quelle)
    ziel = tmp_path / "anderer-datentraeger"
    monkeypatch.setattr(backup_mod, "get_settings", lambda: _BackupSettings(str(quelle), str(ziel)))
    snapshot = backup_mod.create_backup()
    assert snapshot is not None and snapshot.parent == ziel.resolve()
    assert not (quelle.parent / "backups").exists()


def test_backup_ordner_standard_neben_der_db(tmp_path, monkeypatch):
    quelle = tmp_path / "selfmailer.db"
    _db(quelle)
    monkeypatch.setattr(backup_mod, "get_settings", lambda: _BackupSettings(str(quelle), ""))
    assert backup_mod.create_backup().parent == (tmp_path / "backups").resolve()


@pytest.mark.skipif(os.name == "nt", reason="chmod-Rechte gibt es nur unter POSIX")
def test_db_datei_bekommt_600(tmp_path, monkeypatch):
    pfad = tmp_path / "selfmailer.db"
    _db(pfad)
    wal = Path(str(pfad) + "-wal")
    wal.write_bytes(b"")
    os.chmod(pfad, 0o644)
    os.chmod(wal, 0o644)
    monkeypatch.setattr(db_mod, "_db_path", str(pfad))
    db_mod._haerte_db_datei()
    assert stat.S_IMODE(pfad.stat().st_mode) == 0o600
    assert stat.S_IMODE(wal.stat().st_mode) == 0o600
