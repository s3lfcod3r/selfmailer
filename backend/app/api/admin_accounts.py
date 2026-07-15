"""Admin: Mailkonten FÜR einen bestimmten User vorkonfigurieren.

Spiegelt accounts.py, aber adressiert ein fremdes User-Konto (admin-only).
Zugangsdaten werden ebenfalls verschlüsselt gespeichert (secret_enc).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete as sa_delete
from sqlmodel import Session, select

from ..core.crypto import encrypt
from ..core.db import get_session
from ..models import CachedFolder, CachedMessage, FolderSync, MailAccount, MailRule, User
from ..schemas import AccountCreate, AccountOut
from .accounts import _check_mail_host
from .deps import require_admin

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _require_user(user_id: int, session: Session) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User nicht gefunden")
    return user


@router.get("/users/{user_id}/accounts", response_model=list[AccountOut])
def list_user_accounts(
    user_id: int,
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[MailAccount]:
    _require_user(user_id, session)
    return list(session.exec(select(MailAccount).where(MailAccount.user_id == user_id)).all())


@router.post("/users/{user_id}/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def add_user_account(
    user_id: int,
    data: AccountCreate,
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> MailAccount:
    _require_user(user_id, session)
    # SSRF-Schutz wie im Self-Service (accounts.add_account): der Server verbindet
    # sich später serverseitig zu diesen Hosts — interne Ziele (Loopback, LAN,
    # Cloud-Metadata) vorher ausschließen.
    _check_mail_host("IMAP-Host", data.imap_host, data.imap_port)
    _check_mail_host("SMTP-Host", data.smtp_host, data.smtp_port)
    acc = MailAccount(
        user_id=user_id,
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


@router.delete("/users/{user_id}/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_account(
    user_id: int,
    account_id: int,
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> None:
    acc = session.get(MailAccount, account_id)
    if acc is None or acc.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Konto nicht gefunden")
    # Kinder-Zeilen zuerst entfernen (sonst verwaiste Cache-/Regel-Daten) —
    # identisch zu accounts.delete_account.
    for model in (CachedMessage, FolderSync, CachedFolder, MailRule):
        session.execute(sa_delete(model).where(model.account_id == account_id))
    session.delete(acc)
    session.commit()
