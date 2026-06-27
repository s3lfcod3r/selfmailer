"""Mailkonten des angemeldeten Users (Self-Service).

Zugangsdaten werden verschluesselt gespeichert (secret_enc) und nie zurueck-
gegeben. Klartext nur transient beim Verbindungsaufbau.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete as sa_delete
from sqlmodel import Session, select

from ..core.crypto import decrypt, encrypt
from ..core.db import get_session
from ..dav.client import DavUrlError, validate_external_url
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

logger = logging.getLogger(__name__)


def _owned(account_id: int, user: User, session: Session) -> MailAccount:
    acc = session.get(MailAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Konto nicht gefunden")
    return acc


def _check_mail_host(label: str, host: str, port: int) -> None:
    """SSRF-Schutz: der Server verbindet sich serverseitig zu imap_host/smtp_host.
    Ohne Pruefung koennte ein angemeldeter Nutzer interne Ziele (Loopback,
    link-local 169.254.169.254 Cloud-Metadata, LAN) ansteuern. Dieselbe
    Blockliste/DNS-Aufloesung wie bei DAV/ntfy wiederverwenden. Eine ``http``-
    URL ist nur Traeger fuer Host+Port — geprueft wird die aufgeloeste IP, nicht
    das Schema."""
    host = (host or "").strip()
    if not host:
        return
    try:
        validate_external_url(f"http://{host}:{port}/")
    except DavUrlError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"{label} nicht erlaubt: {exc}"
        ) from exc


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
    _check_mail_host("IMAP-Host", data.imap_host, data.imap_port)
    _check_mail_host("SMTP-Host", data.smtp_host, data.smtp_port)
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
    # SSRF: geaenderte Hosts/Ports gegen die effektiven Werte pruefen, bevor sie
    # gespeichert und spaeter serverseitig angesteuert werden.
    if "imap_host" in fields or "imap_port" in fields:
        _check_mail_host(
            "IMAP-Host",
            fields.get("imap_host", acc.imap_host),
            fields.get("imap_port", acc.imap_port),
        )
    if "smtp_host" in fields or "smtp_port" in fields:
        _check_mail_host(
            "SMTP-Host",
            fields.get("smtp_host", acc.smtp_host),
            fields.get("smtp_port", acc.smtp_port),
        )
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
    except Exception:  # noqa: BLE001
        logger.warning("IMAP-Login-Test fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        return {"ok": False, "error": "Login fehlgeschlagen"}


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
