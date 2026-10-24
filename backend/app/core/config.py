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
# cookie_secure war bis 1.96.9 ein bool - alte .env-Werte gelten weiter.
_TRUE_WORDS = {"true", "1", "yes", "on"}
_FALSE_WORDS = {"false", "0", "no", "off"}


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
    # Explizite authserv-id-Allowlist (kommagetrennt). Leer = keine positive
    # Echtheitsbehauptung aus untrusted Mail-Headern. Nur Empfangs-MTAs eintragen,
    # die fremde Header mit ihrer eigenen Kennung am Eingang entfernen.
    trusted_authserv_ids: str = ""
    # First-Run: optionaler Admin-Token, sonst Web-Setup beim ersten Start.
    admin_token: str = ""

    # Übersetzung (LibreTranslate, self-hosted). Leer = Funktion aus.
    # z. B. http://192.168.1.10:5050  (eigener LibreTranslate-Container)
    translate_url: str = ""
    translate_api_key: str = ""   # nur falls die Instanz einen Key verlangt
    # Spam- & Virenschutz (1.97.3). Leer = Funktion nicht verfuegbar; die Nutzer
    # schalten sie dann unter Einstellungen -> Sicherheit ein und aus.
    # ClamAV (clamd, TCP), z. B. 192.168.1.10:3310 - NUR im Heimnetz erreichbar
    # machen: clamd hat weder Passwort noch Verschluesselung.
    clamav: str = ""
    # rspamd: Scanner-Adresse (Port 11333) fuer die Bewertung, z. B.
    # http://192.168.1.10:11333; Controller (Port 11334) + Passwort fuers Lernen.
    rspamd_url: str = ""
    rspamd_controller_url: str = ""
    rspamd_password: str = ""

    # JWT
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 Tage

    # Session-Cookie (Web-Login). HttpOnly = JavaScript kann das Token NICHT lesen
    # (Schutz gegen Token-Diebstahl per XSS). Die native APK nutzt weiterhin den
    # Bearer-Header — das Backend akzeptiert beides.
    # cookie_secure (seit 1.97.0 dreiwertig):
    #   auto  (Standard) = Secure-Flag genau dann, wenn die Anfrage per HTTPS
    #         kam (direkt oder per X-Forwarded-Proto von einem vertrauten Proxy,
    #         siehe trusted_proxies). HTTP im LAN und HTTPS von aussen gehen
    #         damit GLEICHZEITIG, ohne Login-Schleife.
    #   true  = immer Secure (nur sinnvoll, wenn die WebUI NIE per http laeuft).
    #   false = nie Secure (altes Verhalten).
    cookie_name: str = "sm_session"
    cookie_secure: str = "auto"

    # Reverse-Proxy (seit 1.97.0): kommagetrennte IPs/Netze, deren
    # X-Forwarded-For/-Proto uvicorn glauben darf - z. B. die IP von Nginx Proxy
    # Manager. Nur dann stimmen Client-IP (Login-Bremse) und Schema (Cookie
    # "auto", HSTS). Leer = nur 127.0.0.1. "*" nur, wenn der Container
    # AUSSCHLIESSLICH ueber den Proxy erreichbar ist.
    trusted_proxies: str = ""

    # Optional HTTPS direkt im Container (seit 1.97.0). Beide Pfade gesetzt =
    # zusaetzlich zu HTTP auf 8090 lauscht HTTPS auf tls_port. HTTP bleibt immer
    # an (Healthcheck, LAN); wer nur HTTPS will, gibt 8090 einfach nicht frei.
    tls_cert: str = ""
    tls_key: str = ""
    tls_port: int = 8443

    # HSTS (seit 1.97.0): Sekunden, 0 = aus. Wird NUR auf HTTPS-Antworten
    # gesendet - HTTP-Zugriff im LAN bleibt also moeglich, solange man ihn
    # unter einem anderen Hostnamen als dem HTTPS-Namen aufruft.
    hsts_max_age: int = 0

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
    # Zielordner der Backups. Leer = <DB-Ordner>/backups (wie bisher). Sinnvoll
    # ist ein ANDERER Datentraeger oder Share (eigenes Volume im Container), damit
    # ein Plattenschaden nicht DB und Backups zugleich trifft.
    backup_dir: str = ""

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
        mode = str(self.cookie_secure).strip().lower() or "auto"
        if mode in _TRUE_WORDS:
            mode = "true"
        elif mode in _FALSE_WORDS:
            mode = "false"
        if mode not in ("auto", "true", "false"):
            raise ValueError("SELFMAILER_COOKIE_SECURE muss auto, true oder false sein")
        self.cookie_secure = mode
        if bool(self.tls_cert.strip()) != bool(self.tls_key.strip()):
            raise ValueError("SELFMAILER_TLS_CERT und SELFMAILER_TLS_KEY nur zusammen setzen")
        if self.hsts_max_age < 0:
            raise ValueError("SELFMAILER_HSTS_MAX_AGE darf nicht negativ sein")
        return self

    @property
    def tls_enabled(self) -> bool:
        return bool(self.tls_cert.strip() and self.tls_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
