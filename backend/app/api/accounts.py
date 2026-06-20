"""Mailkonten des angemeldeten Users (Self-Service).

Zugangsdaten werden verschluesselt gespeichert (secret_enc) und nie zurueck-
gegeben. Klartext nur transient beim Verbindungsaufbau.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete as sa_delete
from sqlmodel import Session, select

from ..core.crypto import decrypt, encrypt
from ..core.db import get_session
from ..mail import imap as imap_mod
from ..models import (
    CachedFolder,
    CachedMessage,
    FolderSync,
    MailAccount,
    MailRule,
    Protocol,
    User,
)
from ..schemas import AccountCreate, AccountOut, AccountUpdate
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


def _owned(account_id: int, user: User, session: Session) -> MailAccount:
    acc = session.get(MailAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Konto nicht gefunden")
    return acc


@router.get("", response_model=list[AccountOut])
def list_accounts(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> list[MailAccount]:
    return list(session.exec(select(MailAccount).where(MailAccount.user_id == user.id)).all())


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def add_account(
    data: AccountCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MailAccount:
    acc = MailAccount(
        user_id=user.id,
        label=data.label or data.email,
        email=data.email,
        protocol=data.protocol,
        imap_host=data.imap_host,
        imap_port=data.imap_port,
        imap_ssl=data.imap_ssl,
        smtp_host=data.smtp_host,
        smtp_port=data.smtp_port,
        smtp_starttls=data.smtp_starttls,
        auth_user=data.auth_user,
        secret_enc=encrypt(data.password),
    )
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


@router.patch("/{account_id}", response_model=AccountOut)
def update_account(
    account_id: int,
    data: AccountUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MailAccount:
    """Aendert Felder des eigenen Kontos. Passwort wird verschluesselt abgelegt."""
    acc = _owned(account_id, user, session)
    fields = data.model_dump(exclude_unset=True)
    password = fields.pop("password", None)
    if password:  # leeres Passwort = Zugangsdaten nicht aendern
        acc.secret_enc = encrypt(password)
    for field, value in fields.items():
        setattr(acc, field, value)
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


@router.post("/{account_id}/test")
def test_account(
    account_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Prueft den IMAP-Login. Nuetzlich direkt nach dem Anlegen."""
    acc = _owned(account_id, user, session)
    if acc.protocol != Protocol.imap:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Test nur fuer IMAP")
    try:
        folders = imap_mod.list_folders(acc, decrypt(acc.secret_enc))
        return {"ok": True, "folders": folders}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    account_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    acc = _owned(account_id, user, session)
    # Kinder-Zeilen zuerst per Bulk-DELETE entfernen (sonst Waisen + langsames
    # Commit hinter laufendem Sync = "haengt"). account_id ist indiziert.
    for model in (CachedMessage, FolderSync, CachedFolder, MailRule):
        session.execute(sa_delete(model).where(model.account_id == account_id))
    session.delete(acc)
    session.commit()
