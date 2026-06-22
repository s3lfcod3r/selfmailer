"""Zentrale Konfiguration. Werte kommen aus Environment-Variablen.

Sicherheits-Hinweis: SELFMAILER_SECRET ist das Master-Secret. Daraus werden per
HKDF (siehe core/crypto.py) zwei kryptografisch UNABHAENGIGE Unterschluessel
abgeleitet — einer fuer die JWT-Signatur, einer fuer die At-Rest-Verschluesselung
der Mailkonto-Passwoerter. Niemals ins Image oder in die DB schreiben.
"""
from __future__ import annotations

from functools import lru_cache
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Mindestlaenge des Master-Secrets. Kuerzer = zu wenig Entropie fuer JWT-Signatur
# und Fernet-Schluesselableitung.
_MIN_SECRET_LEN = 32
_ALLOWED_JWT_ALGS = {"HS256", "HS384", "HS512"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SELFMAILER_", extra="ignore")

    app_name: str = "SelfMailer"
    # Master-Secret: PFLICHT. Kein Default mehr — ein bekannter Default-Key wuerde
    # JWTs faelschbar und alle Fernet-verschluesselten Konto-Passwoerter lesbar
    # machen. Ohne gueltiges SELFMAILER_SECRET startet die App nicht.
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
    # cookie_secure NUR aktivieren, wenn die WebUI ausschliesslich ueber HTTPS
    # laeuft: ueber LAN-http (http://192.168.x:8090) wuerde ein Secure-Cookie
    # vom Browser NICHT gesendet → Login-Schleife.
    cookie_name: str = "sm_session"
    cookie_secure: bool = False

    # DAV-Pull SSRF-Schutz: link-local/loopback/metadata werden IMMER blockiert.
    # Private/LAN-Ziele (10/8, 172.16/12, 192.168/16, fc00::/7, 100.64/10 CGNAT)
    # bleiben standardmaessig erlaubt, damit Server hinter WireGuard/Tailscale
    # erreichbar sind. Fuer untrusted Multi-User auf True setzen → strikt LAN aus.
    dav_block_private: bool = False

    # CORS (Dev: Vite-Devserver)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

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
