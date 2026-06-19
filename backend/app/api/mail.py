"""Mail lesen/senden ueber ein hinterlegtes Konto des Users."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from ..core.crypto import decrypt
from ..core.db import get_session
from ..mail import imap as imap_mod
from ..mail import smtp as smtp_mod
from ..models import MailAccount, User
from ..schemas import MessageDetail, MessageHeader, SendRequest
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/mail", tags=["mail"])


def _account(account_id: int, user: User, session: Session) -> MailAccount:
    acc = session.get(MailAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Konto nicht gefunden")
    return acc


@router.get("/{account_id}/folders")
def folders(
    account_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[str]:
    acc = _account(account_id, user, session)
    return imap_mod.list_folders(acc, decrypt(acc.secret_enc))


@router.get("/{account_id}/messages", response_model=list[MessageHeader])
def messages(
    account_id: int,
    folder: str = "INBOX",
    limit: int = 50,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    acc = _account(account_id, user, session)
    return imap_mod.list_messages(acc, decrypt(acc.secret_enc), folder=folder, limit=limit)


@router.get("/{account_id}/messages/{uid}", response_model=MessageDetail)
def message(
    account_id: int,
    uid: str,
    folder: str = "INBOX",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    acc = _account(account_id, user, session)
    msg = imap_mod.get_message(acc, decrypt(acc.secret_enc), uid, folder=folder)
    if msg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nachricht nicht gefunden")
    return msg


@router.post("/{account_id}/messages/{uid}/flags")
def set_flags(
    account_id: int,
    uid: str,
    folder: str = "INBOX",
    seen: bool | None = None,
    flagged: bool | None = None,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    acc = _account(account_id, user, session)
    try:
        imap_mod.set_flags(acc, decrypt(acc.secret_enc), uid, folder=folder, seen=seen, flagged=flagged)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Aktion fehlgeschlagen: {exc}")
    return {"ok": True}


@router.post("/{account_id}/messages/{uid}/move")
def move_message(
    account_id: int,
    uid: str,
    dest: str,
    folder: str = "INBOX",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    acc = _account(account_id, user, session)
    try:
        imap_mod.move_message(acc, decrypt(acc.secret_enc), uid, dest, folder=folder)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Verschieben fehlgeschlagen: {exc}")
    return {"ok": True}


@router.delete("/{account_id}/messages/{uid}")
def delete_message(
    account_id: int,
    uid: str,
    folder: str = "INBOX",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    acc = _account(account_id, user, session)
    try:
        result = imap_mod.delete_message(acc, decrypt(acc.secret_enc), uid, folder=folder)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Loeschen fehlgeschlagen: {exc}")
    return {"ok": True, "result": result}


@router.post("/{account_id}/send", status_code=status.HTTP_202_ACCEPTED)
async def send(
    account_id: int,
    data: SendRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    acc = _account(account_id, user, session)
    try:
        await smtp_mod.send_message(
            acc,
            decrypt(acc.secret_enc),
            to=[str(x) for x in data.to],
            subject=data.subject,
            body=data.body,
            cc=[str(x) for x in data.cc],
            bcc=[str(x) for x in data.bcc],
            in_reply_to=data.in_reply_to,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Versand fehlgeschlagen: {exc}")
    return {"sent": True}
