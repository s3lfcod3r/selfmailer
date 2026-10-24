"""Spam- & Virenschutz (seit 1.97.3): gemeinsame Bausteine.

Alles hier ist per Nutzer-Schalter (Einstellungen -> Sicherheit) abschaltbar und
standardmaessig AUS. Die Schalter liegen in User.ui_settings (api/settings.py).

- Sperren fuer alle Konten (sec_block_all_accounts): Loesch-Regeln ("Absender
  blockieren") eines Kontos greifen auch in den anderen Konten desselben Nutzers.
- Trefferzaehler: wie oft eine Regel zugeschlagen hat.
- Ein-Klick-Abmeldung (sec_unsubscribe, RFC 8058).
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from urllib.parse import urlparse

from sqlalchemy import text
from sqlmodel import Session, select

from ..core.db import engine
from ..models import MailAccount, MailRule, User

logger = logging.getLogger(__name__)


def user_flags(user_id: int) -> dict:
    """Die Einstellungen eines Nutzers (Standardwerte, falls nichts gespeichert)."""
    from ..api.settings import user_settings  # lazy: kein Import-Zyklus api <-> mail

    with Session(engine) as s:
        user = s.get(User, user_id)
        return user_settings(user) if user is not None else {}


def flag(user_id: int, key: str) -> bool:
    return bool(user_flags(user_id).get(key, False))


# --- Sperren fuer alle Konten ------------------------------------------------------

def global_block_rules(acc: MailAccount, own_rules: list[MailRule]) -> list[MailRule]:
    """Loesch-Regeln der ANDEREN Konten des Nutzers, wenn er das eingeschaltet hat.

    Nur Loesch-Regeln: Verschiebe-Ziele und Markierungen gehoeren zu ihrem Konto
    (der Zielordner existiert anderswo vielleicht gar nicht). Doppelte Bedingungen
    (gleiches Feld + Wert wie eine eigene Regel) werden weggelassen.
    """
    if acc.user_id is None or not flag(acc.user_id, "sec_block_all_accounts"):
        return []
    eigene = {(r.field, (r.value or "").strip().lower()) for r in own_rules}
    with Session(engine) as s:
        fremde = s.exec(
            select(MailRule).join(MailAccount, MailRule.account_id == MailAccount.id).where(
                MailAccount.user_id == acc.user_id, MailAccount.id != acc.id,
                MailRule.enabled == True, MailRule.delete_msg == True,  # noqa: E712
            ).order_by(MailRule.position, MailRule.id)
        ).all()
        for r in fremde:
            s.expunge(r)
    out: list[MailRule] = []
    for r in fremde:
        key = (r.field, (r.value or "").strip().lower())
        if key in eigene or not key[1]:
            continue
        eigene.add(key)
        out.append(r)
    return out


def record_rule_hits(hits: dict | None) -> None:
    """Treffer zu den Regeln schreiben (nie fatal)."""
    if not hits:
        return
    jetzt = dt.datetime.now(dt.timezone.utc)
    try:
        with engine.begin() as conn:
            for rule_id, n in hits.items():
                if int(n) > 0:
                    conn.execute(
                        text("UPDATE mailrule SET hits = COALESCE(hits, 0) + :n, last_hit_at = :t WHERE id = :id"),
                        {"n": int(n), "t": jetzt, "id": int(rule_id)},
                    )
    except Exception:  # noqa: BLE001 - Statistik darf nichts kippen
        logger.info("Regel-Treffer nicht gespeichert", exc_info=True)


# --- Ein-Klick-Abmeldung (RFC 8058) -------------------------------------------------

_LINK_RE = re.compile(r"<\s*([^>\s]+)\s*>")


def unsubscribe_url(detail: dict) -> str | None:
    """Die https-Adresse fuer die Ein-Klick-Abmeldung - oder None.

    RFC 8058 verlangt beides: einen https-Link in List-Unsubscribe UND
    "List-Unsubscribe=One-Click" in List-Unsubscribe-Post. Nur mailto: oder nur ein
    Link ohne Post-Header zaehlt nicht (das waere ein Webseitenbesuch, kein
    Ein-Klick-Abmelden)."""
    post = str(detail.get("list_unsubscribe_post") or "")
    if "list-unsubscribe=one-click" not in post.replace(" ", "").lower():
        return None
    for link in _LINK_RE.findall(str(detail.get("list_unsubscribe") or "")):
        p = urlparse(link)
        if p.scheme.lower() == "https" and p.hostname:
            return link
    return None


def unsubscribe_info(detail: dict) -> dict | None:
    """Was die Oberflaeche anzeigt: verfuegbar, geprueft (DMARC pass), Zielhost."""
    url = unsubscribe_url(detail)
    if url is None:
        return None
    auth = detail.get("auth") if isinstance(detail.get("auth"), dict) else {}
    return {"available": True, "verified": auth.get("verdict") == "pass", "host": urlparse(url).hostname or ""}


def do_unsubscribe(detail: dict) -> int:
    """Den Abmelde-POST senden (RFC 8058: Formular "List-Unsubscribe=One-Click").

    Ueber post_pinned_form: SSRF-Pruefung, Verbindung zur geprueften IP, keine
    Weiterleitungen. Liefert den HTTP-Status; wirft bei nicht erlaubter Adresse."""
    from ..dav.client import post_pinned_form

    url = unsubscribe_url(detail)
    if url is None:
        raise ValueError("Keine Ein-Klick-Abmeldung")
    r = post_pinned_form(url, {"List-Unsubscribe": "One-Click"}, timeout=15.0)
    return r.status_code
