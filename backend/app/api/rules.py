"""Filterregeln pro Mailkonto: CRUD + Anwenden auf den Posteingang (Modus A)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..core import jobs
from ..core.crypto import decrypt
from ..core.db import get_session
from ..mail import imap as imap_mod
from ..models import MailAccount, MailRule, User
from ..schemas import (
    BlockSenderRequest,
    BlockSenderResult,
    RuleCreate,
    RuleOut,
    RuleUpdate,
)
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/mail", tags=["rules"])

logger = logging.getLogger(__name__)


def _account(account_id: int, user: User, session: Session) -> MailAccount:
    acc = session.get(MailAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Konto nicht gefunden")
    return acc


def _rules(account_id: int, session: Session) -> list[MailRule]:
    return list(
        session.exec(
            select(MailRule).where(MailRule.account_id == account_id).order_by(MailRule.position, MailRule.id)
        )
    )


@router.get("/{account_id}/rules", response_model=list[RuleOut])
def list_rules(
    account_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[MailRule]:
    _account(account_id, user, session)
    return _rules(account_id, session)


@router.post("/{account_id}/rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(
    account_id: int,
    data: RuleCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MailRule:
    _account(account_id, user, session)
    existing = _rules(account_id, session)
    pos = (existing[-1].position + 1) if existing else 0
    rule = MailRule(
        account_id=account_id,
        field=data.field,
        value=data.value,
        target_folder=data.target_folder,
        mark_read=data.mark_read,
        star=data.star,
        delete_msg=data.delete_msg,
        position=pos,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


@router.patch("/{account_id}/rules/{rule_id}", response_model=RuleOut)
def update_rule(
    account_id: int,
    rule_id: int,
    data: RuleUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MailRule:
    _account(account_id, user, session)
    rule = session.get(MailRule, rule_id)
    if rule is None or rule.account_id != account_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Regel nicht gefunden")
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(rule, key, val)
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


@router.delete("/{account_id}/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(
    account_id: int,
    rule_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    _account(account_id, user, session)
    rule = session.get(MailRule, rule_id)
    if rule is not None and rule.account_id == account_id:
        session.delete(rule)
        session.commit()
    return None


@router.post("/{account_id}/rules/apply")
def apply_rules_endpoint(
    account_id: int,
    background: bool = False,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Wendet die aktiven Regeln auf den Posteingang an.

    ``background=true`` führt den (bei großen Postfächern langen) Lauf als
    Hintergrund-Job aus und liefert sofort ``{job_id}`` (Status via
    ``GET /mail/jobs/{job_id}``); ohne den Parameter bleibt es synchron."""
    acc = _account(account_id, user, session)
    rules = [r for r in _rules(account_id, session) if r.enabled]
    if not rules:
        return {"ok": True, "affected": 0}
    pw = decrypt(acc.secret_enc)

    def _run() -> dict:
        return {"ok": True, **imap_mod.apply_rules(acc, pw, rules)}

    if background:
        job_id = jobs.create_job(user.id, "apply_rules")
        jobs.start(job_id, _run)
        return {"job_id": job_id}
    try:
        return _run()
    except HTTPException:
        raise  # z. B. 503 „Konto gerade beschäftigt" nicht als 502 verschleiern
    except Exception:  # noqa: BLE001
        logger.warning("Regeln anwenden fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Regeln anwenden fehlgeschlagen")


@router.post("/{account_id}/block-sender", response_model=BlockSenderResult)
def block_sender(
    account_id: int,
    data: BlockSenderRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> BlockSenderResult:
    """Absender dauerhaft blockieren: legt eine Lösch-Regel an und entfernt
    optional die bereits vorhandenen Mails dieses Absenders sofort endgültig."""
    acc = _account(account_id, user, session)
    sender = (data.sender or "").strip()
    if not sender:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Absender fehlt")

    # Doppelte Regel vermeiden: gleiche Bedingung -> nur Löschen erzwingen.
    field = "from_domain" if data.by_domain else "from"
    existing = next(
        (r for r in _rules(account_id, session) if r.field == field and r.value.lower() == sender.lower()),
        None,
    )
    if existing is not None:
        existing.delete_msg = True
        existing.enabled = True
        rule = existing
    else:
        pos = (_rules(account_id, session)[-1].position + 1) if _rules(account_id, session) else 0
        rule = MailRule(
            account_id=account_id, field=field, value=sender, delete_msg=True, position=pos,
        )
    # Reue-Fenster: beim ersten Blockieren den Papierkorb-Auto-Purge auf 7 Tage
    # stellen, falls noch nie konfiguriert. Geblockte Mails landen im Papierkorb und
    # verschwinden nach 7 Tagen von selbst — bis dahin sind sie wiederherstellbar.
    if acc.trash_purge_days < 0:
        acc.trash_purge_days = 7
        session.add(acc)
    session.add(rule)
    session.commit()
    session.refresh(rule)

    deleted = 0
    if data.delete_existing:
        try:
            res = imap_mod.delete_by_sender(
                acc, decrypt(acc.secret_enc), sender, by_domain=data.by_domain
            )
            deleted = int(res.get("deleted", 0) or 0)
        except Exception:  # noqa: BLE001 - Regel bleibt bestehen, Sofort-Löschung best effort
            logger.warning("Sofort-Löschung beim Blockieren fehlgeschlagen (account_id=%s)", account_id, exc_info=True)

    return BlockSenderResult(rule=RuleOut.model_validate(rule, from_attributes=True), deleted=deleted)


@router.post("/{account_id}/spam/purge")
def purge_spam_now(
    account_id: int,
    background: bool = False,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Spam-Ordner jetzt sofort endgültig leeren (alle Mails, unabhängig vom Alter).

    ``background=true`` erledigt das (bei vollem Ordner langsame) Leeren als
    Hintergrund-Job und liefert ``{job_id}``; sonst synchron ``{deleted}``."""
    acc = _account(account_id, user, session)
    pw = decrypt(acc.secret_enc)

    def _run() -> dict:
        res = imap_mod.purge_spam(acc, pw, 0)
        return {"deleted": int(res.get("deleted", 0) or 0)}

    if background:
        job_id = jobs.create_job(user.id, "purge_spam")
        jobs.start(job_id, _run)
        return {"job_id": job_id}
    try:
        return _run()
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        logger.warning("Spam leeren fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Spam leeren fehlgeschlagen")


@router.post("/{account_id}/trash/purge")
def purge_trash_now(
    account_id: int,
    background: bool = False,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Papierkorb jetzt sofort endgültig leeren (alle Mails, unabhängig vom Alter).

    ``background=true`` erledigt das Leeren als Hintergrund-Job und liefert
    ``{job_id}``; sonst synchron ``{deleted}``."""
    acc = _account(account_id, user, session)
    pw = decrypt(acc.secret_enc)

    def _run() -> dict:
        res = imap_mod.purge_trash(acc, pw, 0)
        return {"deleted": int(res.get("deleted", 0) or 0)}

    if background:
        job_id = jobs.create_job(user.id, "purge_trash")
        jobs.start(job_id, _run)
        return {"job_id": job_id}
    try:
        return _run()
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        logger.warning("Papierkorb leeren fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Papierkorb leeren fehlgeschlagen")
