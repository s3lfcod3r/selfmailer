"""Abwesenheitsnotiz (Auto-Responder) — bewusst DEFENSIV.

Grundsätze:
- Standardmäßig AUS; beantwortet NUR Mails, die NACH dem Einschalten ankommen
  (last_uid==0 → erster Lauf initialisiert nur, antwortet nichts).
- Je Absender höchstens eine Antwort pro ``interval_days`` (VacationReply-Log).
- Niemals antworten auf: automatische Mails (Auto-Submitted != no), Listen/
  Newsletter (List-Id/Precedence bulk|list|junk), leeren Return-Path (Bounces),
  noreply/mailer-daemon/postmaster-Absender, eigene Adressen, fremde
  Auto-Antworten (Betreff-Muster) — verhindert Antwort-Schleifen (Ping-Pong).
- Eigene Antworten tragen Auto-Submitted: auto-replied + X-Auto-Response-Suppress,
  damit Gegenstellen nicht automatisch zurückantworten.
- Notbremse: höchstens _MAX_PER_RUN Antworten pro Lauf und Konto.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re

from sqlmodel import Session, select

from ..core.db import engine
from ..models import MailAccount, VacationReply, VacationSetting
from . import imap as imap_mod
from . import smtp as smtp_mod

logger = logging.getLogger(__name__)

_MAX_PER_RUN = 20          # Antworten pro Lauf/Konto (Notbremse)
_HEADER_CAP = 50           # geprüfte neue Mails pro Lauf

# Absender, die nie eine Abwesenheitsnotiz bekommen (Lokalteil-Muster).
_NOREPLY_RE = re.compile(
    r"(no-?reply|do-?not-?reply|mailer-daemon|postmaster|bounce|notification|newsletter)",
    re.IGNORECASE,
)
# Betreff-Muster fremder Auto-Antworten (keine Schleife mit anderen Respondern).
_AUTO_SUBJECT_RE = re.compile(
    r"(abwesenheit|out of office|automatische antwort|auto[- ]?reply|autoreply)",
    re.IGNORECASE,
)


def _addr_of(frm: str) -> str:
    m = re.search(r"<([^>]+)>", frm or "")
    return (m.group(1) if m else (frm or "")).strip().lower()


def _in_period(vs: VacationSetting, today: dt.date) -> bool:
    try:
        if vs.start_date and today < dt.date.fromisoformat(vs.start_date):
            return False
        if vs.end_date and today > dt.date.fromisoformat(vs.end_date):
            return False
    except ValueError:
        # Kaputtes Datum in den Einstellungen → lieber NICHT senden.
        return False
    return True


def _should_reply(cand: dict, own_addrs: set[str]) -> bool:
    """Alle defensiven Filter — True nur, wenn eine Antwort wirklich angebracht ist."""
    sender = _addr_of(cand.get("from", ""))
    if not sender or "@" not in sender:
        return False
    if sender in own_addrs:
        return False
    if _NOREPLY_RE.search(sender):
        return False
    auto = (cand.get("auto_submitted") or "").strip().lower()
    if auto and auto != "no":
        return False
    prec = (cand.get("precedence") or "").strip().lower()
    if prec in ("bulk", "list", "junk", "auto_reply"):
        return False
    if (cand.get("list_id") or "").strip():
        return False
    if (cand.get("suppress") or "").strip():
        return False
    rp = (cand.get("return_path") or "").strip()
    if rp == "<>":
        return False
    if _AUTO_SUBJECT_RE.search(cand.get("subject") or ""):
        return False
    return True


def process_account(acc: MailAccount, password: str) -> None:
    """Ein Konto prüfen und ggf. Abwesenheitsantworten senden (Scheduler-Aufruf,
    läuft im Worker-Thread; eigene DB-Session)."""
    with Session(engine) as s:
        vs = s.exec(select(VacationSetting).where(VacationSetting.account_id == acc.id)).first()
        if vs is None or not vs.enabled or not vs.body.strip():
            return
        if not _in_period(vs, dt.date.today()):
            return
        own_addrs = {
            (e or "").strip().lower()
            for e in s.exec(select(MailAccount.email).where(MailAccount.user_id == acc.user_id)).all()
        }
        last_uid = vs.last_uid

    cands, max_uid = imap_mod.fetch_new_headers(acc, password, last_uid, cap=_HEADER_CAP)

    if last_uid <= 0:
        # Frisch eingeschaltet: NUR den Startpunkt setzen, nichts beantworten —
        # so bekommen ausschließlich künftige Mails eine Antwort.
        with Session(engine) as s:
            row = s.exec(select(VacationSetting).where(VacationSetting.account_id == acc.id)).first()
            if row is not None and row.enabled and row.last_uid <= 0:
                row.last_uid = max(max_uid, 1)
                s.add(row)
                s.commit()
        return

    if max_uid <= last_uid:
        return

    sent = 0
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    with Session(engine) as s:
        vs = s.exec(select(VacationSetting).where(VacationSetting.account_id == acc.id)).first()
        if vs is None or not vs.enabled:
            return
        window = now - dt.timedelta(days=max(1, vs.interval_days))
        for cand in cands:
            if sent >= _MAX_PER_RUN:
                break
            if not _should_reply(cand, own_addrs):
                continue
            sender = _addr_of(cand.get("from", ""))
            # Schon innerhalb des Fensters beantwortet? → überspringen.
            prev = s.exec(
                select(VacationReply).where(
                    VacationReply.account_id == acc.id,
                    VacationReply.sender == sender,
                    VacationReply.sent_at > window,
                )
            ).first()
            if prev is not None:
                continue
            try:
                asyncio.run(smtp_mod.send_message(
                    acc, password,
                    to=[sender],
                    subject=vs.subject or "Abwesenheitsnotiz",
                    body=vs.body,
                    in_reply_to=cand.get("message_id", ""),
                    extra_headers={
                        "Auto-Submitted": "auto-replied",
                        "X-Auto-Response-Suppress": "All",
                        "Precedence": "auto_reply",
                    },
                ))
            except Exception:  # noqa: BLE001 - ein Fehlversand kippt nicht den Lauf
                logger.warning("Abwesenheit: Versand fehlgeschlagen (account_id=%s → %s)", acc.id, sender, exc_info=True)
                continue
            s.add(VacationReply(account_id=acc.id, sender=sender, sent_at=now))
            sent += 1
            logger.info("Abwesenheit: Antwort gesendet (account_id=%s → %s)", acc.id, sender)
        # Zeiger IMMER auf die höchste gesehene UID vorrücken — Übersprungenes
        # wird bewusst nicht später nachbeantwortet (keine Nachzügler-Flut).
        vs.last_uid = max_uid
        s.add(vs)
        s.commit()
