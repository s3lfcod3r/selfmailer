"""Zentrale Konfiguration. Werte kommen aus Environment-Variablen.

Sicherheits-Hinweis: SELFMAILER_SECRET ist der Master-Key fuer JWT-Signatur
UND fuer die At-Rest-Verschluesselung der Mailkonto-Passwoerter. Niemals ins
Image oder in die DB schreiben.
"""
from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SELFMAILER_", extra="ignore")

    app_name: str = "SelfMailer"
    # Master-Secret: Pflicht in Produktion. Dev-Default nur fuer lokales Testen.
    secret: str = "dev-insecure-change-me-please-32chars"
    db_path: str = "./data/selfmailer.db"
    base_url: str = ""
    # First-Run: optionaler Admin-Token, sonst Web-Setup beim ersten Start.
    admin_token: str = ""

    # JWT
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 Tage

    # CORS (Dev: Vite-Devserver)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
