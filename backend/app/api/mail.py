"""Mail lesen/senden ueber ein hinterlegtes Konto des Users."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel import Session

from ..core.crypto import decrypt
from ..core.db import get_session
from ..mail import imap as imap_mod
from ..mail import migrate as migrate_mod
from ..mail import smtp as smtp_mod
from ..models import MailAccount, User
from ..schemas import MessageDetail, MessageHeader, MigrateRequest, SendRequest
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


@router.get("/{account_id}/folders/counts")
def folder_counts(
    account_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    """Ordner mit Ungelesen-/Gesamt-Zaehler (fuer die Thunderbird-Ansicht)."""
    acc = _account(account_id, user, session)
    return imap_mod.folder_counts(acc, decrypt(acc.secret_enc))


@router.post("/{account_id}/folders")
def create_folder(
    account_id: int,
    name: str,
    parent: str = "",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    acc = _account(account_id, user, session)
    try:
        full = imap_mod.create_folder(acc, decrypt(acc.secret_enc), name, parent=parent)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Ordner anlegen fehlgeschlagen: {exc}")
    return {"ok": True, "folder": full}


@router.post("/{account_id}/folders/defaults")
def ensure_default_folders(
    account_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    acc = _account(account_id, user, session)
    try:
        imap_mod.ensure_default_folders(acc, decrypt(acc.secret_enc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Standard-Ordner anlegen fehlgeschlagen: {exc}")
    return {"ok": True}


@router.post("/{account_id}/folders/rename")
def rename_folder(
    account_id: int,
    name: str,
    new_name: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    acc = _account(account_id, user, session)
    try:
        new_path = imap_mod.rename_folder(acc, decrypt(acc.secret_enc), name, new_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Umbenennen fehlgeschlagen: {exc}")
    return {"ok": True, "folder": new_path}


@router.delete("/{account_id}/folders")
def delete_folder(
    account_id: int,
    name: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    acc = _account(account_id, user, session)
    try:
        imap_mod.delete_folder(acc, decrypt(acc.secret_enc), name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Ordner löschen fehlgeschlagen: {exc}")
    return {"ok": True}


@router.get("/{account_id}/messages", response_model=list[MessageHeader])
def messages(
    account_id: int,
    folder: str = "INBOX",
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    acc = _account(account_id, user, session)
    return imap_mod.list_messages(acc, decrypt(acc.secret_enc), folder=folder, limit=limit, offset=offset)


@router.post("/{account_id}/migrate")
def migrate_account(
    account_id: int,
    data: MigrateRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Migriert Mails aus diesem Quellkonto (z. B. Synology) in die uebrigen
    Konten des Users — pro Mail anhand des Empfaengers ins passende Postfach.
    dry_run=True (Default) zeigt nur die Vorschau, schreibt nichts."""
    source = _account(account_id, user, session)
    dest = _account(data.dest_account_id, user, session)  # prueft Eigentuemer
    if dest.id == source.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Quelle und Ziel sind identisch")
    try:
        return migrate_mod.migrate_folders(
            source, decrypt(source.secret_enc), dest, decrypt(dest.secret_enc),
            target_prefix=data.target_prefix, dry_run=data.dry_run, limit_per_folder=data.limit,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Migration fehlgeschlagen: {exc}")


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


@router.get("/{account_id}/messages/{uid}/attachments/{index}")
def attachment(
    account_id: int,
    uid: str,
    index: int,
    folder: str = "INBOX",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    acc = _account(account_id, user, session)
    result = imap_mod.get_attachment(acc, decrypt(acc.secret_enc), uid, index, folder=folder)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Anhang nicht gefunden")
    filename, content_type, data = result
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(content=data, media_type=content_type, headers={"Content-Disposition": disposition})


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


@router.post("/{account_id}/draft")
def save_draft(
    account_id: int,
    data: SendRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    acc = _account(account_id, user, session)
    try:
        ok = imap_mod.save_draft(
            acc,
            decrypt(acc.secret_enc),
            to=", ".join(str(x) for x in data.to),
            cc=", ".join(str(x) for x in data.cc),
            subject=data.subject,
            body=data.body,
            html=data.html,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Entwurf speichern fehlgeschlagen: {exc}")
    return {"ok": ok}


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
            attachments=[a.model_dump() for a in data.attachments],
            html=data.html,
            read_receipt=data.read_receipt,
            delivery_receipt=data.delivery_receipt,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Versand fehlgeschlagen: {exc}")
    return {"sent": True}
