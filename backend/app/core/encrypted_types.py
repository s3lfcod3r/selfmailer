"""Transparent verschluesselte Text-Spalten (seit 1.97.2).

Fuer Inhalte, die nur gespeichert und wieder gelesen, aber NIE per SQL
durchsucht, verglichen oder sortiert werden - z. B. Betreff, Absender, Vorschau
und Volltext im Mail-Cache. Verschluesselt wird mit demselben Fernet-Schluessel
wie die Postfach-Passwoerter (core/crypto.py).

Regeln:
- Leere Werte bleiben leer (nicht verschluesselt). So funktionieren Bedingungen
  wie `spalte != ""` ("schon einmal geoeffnet") weiter.
- Alles andere wird bei JEDEM Schreiben neu verschluesselt; Vergleiche auf
  konkrete Inhalte sind dadurch bewusst unmoeglich.
- Lesen: Werte mit Praefix "enc:v1:" werden entschluesselt, alte Klartextwerte
  kommen unveraendert zurueck (Migration v5 in core/db.py stellt sie um). Ist
  ein Wert nicht entschluesselbar (Secret gewechselt), kommt "" zurueck - der
  Cache wird dann wie leer behandelt und neu befuellt.
"""
from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from .crypto import SEALED_PREFIX, decrypt, encrypt


def seal_text(value: str) -> str:
    """Nicht-leeren Text verschluesseln ("enc:v1:<fernet>"), leeren lassen."""
    return SEALED_PREFIX + encrypt(value) if value else value


def open_text(value: str | None) -> str | None:
    """Gegenstueck zu seal_text; Klartext-Altbestand bleibt unveraendert."""
    if not value or not value.startswith(SEALED_PREFIX):
        return value
    try:
        return decrypt(value[len(SEALED_PREFIX):])
    except ValueError:
        return ""


class EncryptedText(TypeDecorator):
    """VARCHAR-Spalte, die nicht-leere Werte Fernet-verschluesselt ablegt."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return seal_text(value)

    def process_result_value(self, value, dialect):
        return open_text(value)
