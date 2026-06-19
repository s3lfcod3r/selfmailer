"""Filterregeln pro Mailkonto: CRUD + Anwenden auf den Posteingang (Modus A)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..core.crypto import decrypt
from ..core.db import get_session
from ..mail import imap as imap_mod
from ..models import MailAccount, MailRule, User
from ..schemas import RuleCreate, RuleOut, RuleUpdate
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/mail", tags=["rules"])


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
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    acc = _account(account_id, user, session)
    rules = [r for r in _rules(account_id, session) if r.enabled]
    if not rules:
        return {"ok": True, "affected": 0}
    try:
        result = imap_mod.apply_rules(acc, decrypt(acc.secret_enc), rules)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Regeln anwenden fehlgeschlagen: {exc}")
    return {"ok": True, **result}
