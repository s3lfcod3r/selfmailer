"""Zentrale Konfiguration. Werte kommen aus Environment-Variablen.

Sicherheits-Hinweis: SELFMAILER_SECRET ist das Master-Secret. Daraus werden per
HKDF (siehe core/crypto.py) zwei kryptografisch UNABHÄNGIGE Unterschlüssel
abgeleitet — einer für die JWT-Signatur, einer für die At-Rest-Verschlüsselung
der Mailkonto-Passwörter. Niemals ins Image oder in die DB schreiben.
"""
from __future__ import annotations

from functools import lru_cache
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Mindestlänge des Master-Secrets. Kürzer = zu wenig Entropie für JWT-Signatur
# und Fernet-Schlüsselableitung.
_MIN_SECRET_LEN = 32
_ALLOWED_JWT_ALGS = {"HS256", "HS384", "HS512"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SELFMAILER_", extra="ignore")

    app_name: str = "SelfMailer"
    # Master-Secret: PFLICHT. Kein Default mehr — ein bekannter Default-Key würde
    # JWTs fälschbar und alle Fernet-verschlüsselten Konto-Passwörter lesbar
    # machen. Ohne gültiges SELFMAILER_SECRET startet die App nicht.
    secret: str = ""
    db_path: str = "./data/selfmailer.db"
    base_url: str = ""
    # Pfad zur FCM-Service-Account-JSON (Google-Push). Leer/fehlend = FCM aus.
    fcm_credentials: str = ""
    # First-Run: optionaler Admin-Token, sonst Web-Setup beim ersten Start.
    admin_token: str = ""

    # Übersetzung (LibreTranslate, self-hosted). Leer = Funktion aus.
    # z. B. http://192.168.1.10:5050  (eigener LibreTranslate-Container)
    translate_url: str = ""
    translate_api_key: str = ""   # nur falls die Instanz einen Key verlangt

    # JWT
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 Tage

    # Session-Cookie (Web-Login). HttpOnly = JavaScript kann das Token NICHT lesen
    # (Schutz gegen Token-Diebstahl per XSS). Die native APK nutzt weiterhin den
    # Bearer-Header — das Backend akzeptiert beides.
    # cookie_secure NUR aktivieren, wenn die WebUI ausschließlich über HTTPS
    # laeuft: über LAN-http (http://192.168.x:8090) würde ein Secure-Cookie
    # vom Browser NICHT gesendet → Login-Schleife.
    cookie_name: str = "sm_session"
    cookie_secure: bool = False

    # DAV-Pull SSRF-Schutz: link-local/loopback/metadata werden IMMER blockiert.
    # Private/LAN-Ziele (10/8, 172.16/12, 192.168/16, fc00::/7, 100.64/10 CGNAT)
    # bleiben standardmäßig erlaubt, damit Server hinter WireGuard/Tailscale
    # erreichbar sind. Für untrusted Multi-User auf True setzen → strikt LAN aus.
    #
    # BEWUSST NUR für die DAV-Pull-Ziele (dav/client.py). Der IMAP/SMTP-Host-Check
    # in api/accounts.py (_check_mail_host) wird hierüber NICHT verschärft:
    # sehr viele self-hosted Mailserver stehen im LAN (192.168.x/10.x). Würde man
    # private Adressen auch dort blockieren, ließen sich legitime LAN-Mailserver
    # nicht mehr einrichten. Die SSRF-Härtung bleibt daher gezielt auf DAV-Pull
    # beschränkt; wer strikte Isolation braucht, setzt dav_block_private=True und
    # betreibt Mailserver hinter einem erreichbaren (öffentlichen/VPN-)Hostnamen.
    dav_block_private: bool = False

    # CORS (Dev: Vite-Devserver)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Automatisches DB-Backup (nächtlich, via Scheduler). Konsistentes Online-
    # Backup der SQLite-DB nach data/backups/. backup_keep = Anzahl der zu
    # behaltenden Snapshots (Rotation löscht ältere). 0 oder negativ = keine
    # Rotation (alle behalten).
    backup_enabled: bool = True
    backup_keep: int = 7

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if len(self.secret) < _MIN_SECRET_LEN:
            raise ValueError(
                "SELFMAILER_SECRET fehlt oder ist zu kurz "
                f"(min. {_MIN_SECRET_LEN} Zeichen). Erzeugen mit: "
                'python -c "import secrets; print(secrets.token_hex(32))"'
            )
        if self.jwt_algorithm not in _ALLOWED_JWT_ALGS:
            raise ValueError(f"SELFMAILER_JWT_ALGORITHM muss in {_ALLOWED_JWT_ALGS} liegen")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
