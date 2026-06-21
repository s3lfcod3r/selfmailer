"""Mail lesen/senden ueber ein hinterlegtes Konto des Users."""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel import Session

from ..core.crypto import decrypt
from ..core.db import get_session
from ..events import bus
from ..mail import cache as cache_mod
from ..mail import imap as imap_mod
from ..mail import migrate as migrate_mod
from ..mail import smtp as smtp_mod
from ..models import MailAccount, User
from ..schemas import BatchRequest, MessageDetail, MessageHeader, MigrateRequest, SendRequest, TransferRequest
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/mail", tags=["mail"])

logger = logging.getLogger(__name__)


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
    live: bool = False,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    """Ordner mit Ungelesen-/Gesamt-Zaehler (fuer die Thunderbird-Ansicht).

    Cache-first: ohne ?live=1 kommt die Liste SOFORT aus dem DB-Cache (kein
    IMAP). Ist der Cache leer (erster Aufruf), wird einmal live geholt. Das
    Frontend zeigt erst den Cache und ruft dann ?live=1 im Hintergrund zum
    Auffrischen. Faellt der Live-Abruf aus, bleibt der Cache stehen.
    """
    acc = _account(account_id, user, session)
    if not live:
        cached = cache_mod.read_folder_counts(session, account_id)
        if cached:
            return cached
    out = imap_mod.folder_counts(acc, decrypt(acc.secret_enc))
    try:
        cache_mod.write_folder_counts(session, account_id, out)
    except Exception:  # noqa: BLE001 - Cache-Pflege darf den Abruf nie kippen
        pass
    return out


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
    except Exception:  # noqa: BLE001
        logger.warning("Ordner anlegen fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Ordner anlegen fehlgeschlagen")
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
    except Exception:  # noqa: BLE001
        logger.warning("Standard-Ordner anlegen fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Standard-Ordner anlegen fehlgeschlagen")
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
    except Exception:  # noqa: BLE001
        logger.warning("Ordner umbenennen fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Umbenennen fehlgeschlagen")
    return {"ok": True, "folder": new_path}


@router.post("/{account_id}/folders/move")
def move_folder(
    account_id: int,
    name: str,
    parent: str = "",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Verschiebt einen Ordner unter einen anderen Eltern-Ordner (parent leer =
    oberste Ebene) — Reorganisation der Ordnerhierarchie im selben Konto."""
    acc = _account(account_id, user, session)
    try:
        new_path = imap_mod.move_folder(acc, decrypt(acc.secret_enc), name, parent)
    except Exception:  # noqa: BLE001
        logger.warning("Ordner verschieben fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Ordner verschieben fehlgeschlagen")
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
    except Exception:  # noqa: BLE001
        logger.warning("Ordner loeschen fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Ordner löschen fehlgeschlagen")
    return {"ok": True}


@router.get("/{account_id}/messages", response_model=list[MessageHeader])
def messages(
    account_id: int,
    folder: str = "INBOX",
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    acc = _account(account_id, user, session)
    # Cache-first: ist der Ordner schon gecacht, kommt die Liste SOFORT aus der DB.
    # Beim ersten Mal wird nur die erste Seite live nachgeladen (schnell), den Rest
    # holt der Hintergrund-Sync (/sync). Faellt der Cache aus → ganz normal live.
    try:
        pw = decrypt(acc.secret_enc)
        if not cache_mod.has_cache(session, account_id, folder):
            cache_mod.sync_folder(session, acc, pw, folder, cap=max(limit, 50))
        msgs = cache_mod.read_messages(session, account_id, folder, limit=limit, offset=offset)
        # Self-heal: 1. Seite leer, obwohl der Ordner Mails hat (z. B. nach dem
        # Loeschen der einzigen gecachten Seite) -> live nachsyncen und erneut lesen.
        if not msgs and offset == 0:
            cache_mod.sync_folder(session, acc, pw, folder, cap=max(limit, 50))
            msgs = cache_mod.read_messages(session, account_id, folder, limit=limit, offset=offset)
            if not msgs:
                msgs = imap_mod.list_messages(acc, pw, folder=folder, limit=limit, offset=offset)
        return msgs
    except Exception:  # noqa: BLE001 - Cache ist nur Beschleunigung
        return imap_mod.list_messages(acc, decrypt(acc.secret_enc), folder=folder, limit=limit, offset=offset)


@router.get("/{account_id}/folder-uids", response_model=list[str])
def folder_uids(
    account_id: int,
    folder: str = "INBOX",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[str]:
    """Alle UIDs eines Ordners (neueste zuerst) — fuer "Alle im Ordner auswaehlen"
    ueber Seitengrenzen hinweg.

    LIVE-first: hier zaehlt VOLLSTAENDIGKEIT (wirklich ALLE Mails auswaehlen, z. B.
    um 614 Mails zu loeschen). Der Cache ist oft nur teilweise gefuellt und wuerde
    zu wenige UIDs liefern. ``box.uids()`` ist billig (nur UIDs, keine Inhalte) und
    durch das IMAP-Timeout gebunden. Faellt der Live-Abruf aus -> Cache als Fallback."""
    acc = _account(account_id, user, session)
    try:
        return imap_mod.list_uids(acc, decrypt(acc.secret_enc), folder)
    except Exception:  # noqa: BLE001 - Live fehlgeschlagen -> wenigstens den Cache
        return cache_mod.folder_uids(session, account_id, folder)


@router.post("/{account_id}/sync")
def sync_messages(
    account_id: int,
    folder: str = "INBOX",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Delta-Sync: holt neue Mails in den Cache, entfernt geloeschte, gleicht Flags
    ab. Das Frontend ruft das im Hintergrund auf, nachdem es den Cache gezeigt hat."""
    acc = _account(account_id, user, session)
    try:
        return cache_mod.sync_folder(session, acc, decrypt(acc.secret_enc), folder)
    except Exception:  # noqa: BLE001
        logger.warning("Sync fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Sync fehlgeschlagen")


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
    except Exception:  # noqa: BLE001
        logger.warning("Migration fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Migration fehlgeschlagen")


@router.post("/{account_id}/transfer")
def transfer(
    account_id: int,
    data: TransferRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Kopiert/verschiebt einzelne Mails oder einen ganzen Ordner aus diesem Konto
    in den Ordner eines ANDEREN Kontos des Users."""
    source = _account(account_id, user, session)
    dest = _account(data.dest_account_id, user, session)
    try:
        return migrate_mod.transfer_messages(
            source, decrypt(source.secret_enc), data.source_folder, data.uids,
            dest, decrypt(dest.secret_enc), data.dest_folder,
            move=data.move, limit=data.limit,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Uebertragen fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Übertragen fehlgeschlagen")


@router.get("/{account_id}/messages/{uid}", response_model=MessageDetail)
def message(
    account_id: int,
    uid: str,
    folder: str = "INBOX",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    acc = _account(account_id, user, session)
    # Cache-first: schon einmal geoeffnet → Body kommt SOFORT aus der DB (kein IMAP).
    try:
        cached = cache_mod.read_detail(session, account_id, folder, uid)
        # Alten Cache OHNE Echtheits-Analyse verwerfen -> live neu holen (mit auth).
        if cached is not None and cached.get("auth"):
            return cached
    except Exception:  # noqa: BLE001 - Cache ist nur Beschleunigung
        pass
    msg = imap_mod.get_message(acc, decrypt(acc.secret_enc), uid, folder=folder)
    if msg is None:
        # Die Mail ist serverseitig weg (verschoben/geloescht) — etwaigen stale
        # Cache-Eintrag entfernen, damit die Liste sich selbst heilt.
        try:
            cache_mod.remove_uids(session, account_id, folder, [uid])
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nachricht nicht gefunden")
    try:
        cache_mod.write_detail(session, account_id, folder, uid, msg)
    except Exception:  # noqa: BLE001
        pass
    return msg


@router.get("/{account_id}/messages/{uid}/raw")
def message_raw(
    account_id: int,
    uid: str,
    folder: str = "INBOX",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    """Rohe RFC822-Quelle (Header + Body) — „Original anzeigen"."""
    acc = _account(account_id, user, session)
    raw = imap_mod.get_raw(acc, decrypt(acc.secret_enc), uid, folder=folder)
    if raw is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nachricht nicht gefunden")
    return Response(content=raw, media_type="text/plain; charset=utf-8")


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
    except Exception:  # noqa: BLE001
        logger.warning("Flags setzen fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Aktion fehlgeschlagen")
    try:
        cache_mod.update_flags(session, account_id, folder, uid, seen=seen, flagged=flagged)
    except Exception:  # noqa: BLE001 - Cache-Pflege darf nie die Aktion kippen
        pass
    bus.publish(user.id, {"type": "mail", "account_id": account_id, "folder": folder})
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
    except Exception:  # noqa: BLE001
        logger.warning("Nachricht verschieben fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Verschieben fehlgeschlagen")
    try:
        cache_mod.remove_uids(session, account_id, folder, [uid])
    except Exception:  # noqa: BLE001
        pass
    bus.publish(user.id, {"type": "mail", "account_id": account_id, "folder": folder})
    return {"ok": True}


@router.post("/{account_id}/messages/prefetch")
def prefetch_bodies(
    account_id: int,
    data: BatchRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Waermt den Body-Cache fuer eine Liste von UIDs in EINER IMAP-Session vor.

    Holt nur die noch nicht gecachten Bodies (ein Login + ein Sammel-Fetch) und
    legt sie ab. Danach kommt jedes Oeffnen dieser Mails sofort aus der DB.
    Best-effort: Fehler werden geschluckt (Cache ist reine Beschleunigung).
    """
    acc = _account(account_id, user, session)
    try:
        missing = cache_mod.uncached_detail_uids(session, account_id, data.folder, data.uids)
        if not missing:
            return {"ok": True, "cached": 0}
        details = imap_mod.get_messages(acc, decrypt(acc.secret_enc), missing, folder=data.folder)
        for d in details:
            cache_mod.write_detail(session, account_id, data.folder, d.get("uid", ""), d)
        return {"ok": True, "cached": len(details)}
    except Exception:  # noqa: BLE001 - Vorwaermen darf nie eine Anfrage kippen
        return {"ok": False, "cached": 0}


@router.post("/{account_id}/messages/batch-delete")
def delete_messages_batch(
    account_id: int,
    data: BatchRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Loescht mehrere Mails in EINER IMAP-Session (statt N Einzel-Requests/Logins)."""
    acc = _account(account_id, user, session)
    try:
        result = imap_mod.delete_messages(acc, decrypt(acc.secret_enc), data.uids, folder=data.folder)
    except Exception:  # noqa: BLE001
        logger.warning("Batch-Loeschen fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Loeschen fehlgeschlagen")
    try:
        cache_mod.remove_uids(session, account_id, data.folder, data.uids)
    except Exception:  # noqa: BLE001
        pass
    bus.publish(user.id, {"type": "mail", "account_id": account_id, "folder": data.folder})
    return {"ok": True, "result": result}


@router.post("/{account_id}/messages/batch-flags")
def set_flags_batch(
    account_id: int,
    data: BatchRequest,
    seen: bool | None = None,
    flagged: bool | None = None,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Setzt Seen/Flagged fuer VIELE Mails in EINER IMAP-Session (z. B. "alles als
    gelesen" bei tausenden Mails) — statt N Einzel-Requests/Logins."""
    acc = _account(account_id, user, session)
    try:
        n = imap_mod.set_flags_many(
            acc, decrypt(acc.secret_enc), data.uids, folder=data.folder, seen=seen, flagged=flagged,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Batch-Markieren fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Markieren fehlgeschlagen")
    try:
        cache_mod.set_flags_bulk(session, account_id, data.folder, data.uids, seen=seen, flagged=flagged)
    except Exception:  # noqa: BLE001 - Cache-Pflege darf die Aktion nie kippen
        pass
    bus.publish(user.id, {"type": "mail", "account_id": account_id, "folder": data.folder})
    return {"ok": True, "count": n}


@router.post("/{account_id}/messages/batch-move")
def move_messages_batch(
    account_id: int,
    data: BatchRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Verschiebt mehrere Mails in EINER IMAP-Session (statt N Einzel-Requests/Logins)."""
    if not data.dest:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Zielordner fehlt")
    acc = _account(account_id, user, session)
    try:
        result = imap_mod.move_messages(acc, decrypt(acc.secret_enc), data.uids, data.dest, folder=data.folder)
    except Exception:  # noqa: BLE001
        logger.warning("Batch-Verschieben fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Verschieben fehlgeschlagen")
    try:
        cache_mod.remove_uids(session, account_id, data.folder, data.uids)
    except Exception:  # noqa: BLE001
        pass
    bus.publish(user.id, {"type": "mail", "account_id": account_id, "folder": data.folder})
    return {"ok": True, "result": result}


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
    except Exception:  # noqa: BLE001
        logger.warning("Nachricht loeschen fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Loeschen fehlgeschlagen")
    try:
        cache_mod.remove_uids(session, account_id, folder, [uid])
    except Exception:  # noqa: BLE001
        pass
    bus.publish(user.id, {"type": "mail", "account_id": account_id, "folder": folder})
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
    except Exception:  # noqa: BLE001
        logger.warning("Entwurf speichern fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Entwurf speichern fehlgeschlagen")
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
    except Exception:  # noqa: BLE001
        logger.warning("Versand fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Versand fehlgeschlagen")
    return {"sent": True}
