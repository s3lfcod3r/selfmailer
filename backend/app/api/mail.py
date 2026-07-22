"""Mail lesen/senden über ein hinterlegtes Konto des Users."""
from __future__ import annotations

import logging
from urllib.parse import quote

from aiosmtplib.errors import SMTPRecipientsRefused

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from starlette.concurrency import run_in_threadpool
from sqlmodel import Session

from ..core.crypto import decrypt
from ..core.db import get_session
from ..events import bus
from ..mail import cache as cache_mod
from ..mail import imap as imap_mod
from ..mail import migrate as migrate_mod
from ..mail import smtp as smtp_mod
from ..core import jobs
from ..models import MailAccount, User
from ..schemas import (
    BatchRequest,
    MAX_ATTACHMENTS_B64_BYTES,
    MessageDetail,
    MessageHeader,
    MigrateRequest,
    SendRequest,
    TransferRequest,
)
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/mail", tags=["mail"])

logger = logging.getLogger(__name__)


def _account(account_id: int, user: User, session: Session) -> MailAccount:
    acc = session.get(MailAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Konto nicht gefunden")
    return acc


def _reject_if_too_large(data: SendRequest) -> None:
    """Server-seitige Obergrenze für die Anhang-Gesamtgröße (base64). Schützt vor
    riesigen Uploads, die das Frontend-Limit umgehen — HTTP 413 statt 500/OOM."""
    total = sum(len(a.content_b64 or "") for a in data.attachments)
    if total > MAX_ATTACHMENTS_B64_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Anhänge zu groß (max. 25 MB)"
        )


def _account_secret(acc: MailAccount) -> str:
    """Entschlüsselt das gespeicherte Konto-Passwort. Schlägt die Entschlüsselung
    fehl (z. B. nach Schlüsselwechsel/Datenkorruption), wird ein sauberes 400
    statt eines unbehandelten 500 geliefert."""
    try:
        return decrypt(acc.secret_enc)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Zugangsdaten nicht entschlüsselbar")


@router.get("/jobs/{job_id}")
def job_status(
    job_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    """Status eines Hintergrund-Jobs (schwere IMAP-Operation). Nur der Eigentümer
    sieht seinen Job. Liefert ``{status, result, error}`` — status ist ``pending``/
    ``running``/``done``/``error``; bei ``done`` steht das Ergebnis in ``result``."""
    job = jobs.get_job(job_id, user.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job nicht gefunden")
    return job


@router.get("/{account_id}/folders")
def folders(
    account_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[str]:
    acc = _account(account_id, user, session)
    return imap_mod.list_folders(acc, _account_secret(acc))


@router.get("/{account_id}/folders/counts")
def folder_counts(
    account_id: int,
    live: bool = False,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    """Ordner mit Ungelesen-/Gesamt-Zähler (für die Thunderbird-Ansicht).

    Cache-first: ohne ?live=1 kommt die Liste SOFORT aus dem DB-Cache (kein
    IMAP). Ist der Cache leer (erster Aufruf), wird einmal live geholt. Das
    Frontend zeigt erst den Cache und ruft dann ?live=1 im Hintergrund zum
    Auffrischen. Fällt der Live-Abruf aus, bleibt der Cache stehen.
    """
    acc = _account(account_id, user, session)
    if not live:
        cached = cache_mod.read_folder_counts(session, account_id)
        if cached:
            return cached
    out = imap_mod.folder_counts(acc, _account_secret(acc))
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
        full = imap_mod.create_folder(acc, _account_secret(acc), name, parent=parent)
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
        imap_mod.ensure_default_folders(acc, _account_secret(acc))
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
        new_path = imap_mod.rename_folder(acc, _account_secret(acc), name, new_name)
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
        new_path = imap_mod.move_folder(acc, _account_secret(acc), name, parent)
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
        imap_mod.delete_folder(acc, _account_secret(acc), name)
    except Exception:  # noqa: BLE001
        logger.warning("Ordner löschen fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Ordner löschen fehlgeschlagen")
    return {"ok": True}


def _pin_flagged_first(msgs: list[dict], pin_flagged: bool) -> list[dict]:
    """Markierte Mails nach vorn — für die LIVE-Pfade, die der Cache-Sortierung
    (siehe cache.read_messages) sonst widersprechen würden. ``sorted`` ist stabil,
    die Datums-Reihenfolge innerhalb beider Gruppen bleibt also erhalten."""
    if not pin_flagged:
        return msgs
    return sorted(msgs, key=lambda m: not m.get("flagged"))


@router.get("/{account_id}/messages", response_model=list[MessageHeader])
def messages(
    account_id: int,
    folder: str = "INBOX",
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    pin_flagged: bool = Query(default=False),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    acc = _account(account_id, user, session)
    # Cache-first: ist der Ordner schon gecacht, kommt die Liste SOFORT aus der DB.
    # Beim ersten Mal wird nur die erste Seite live nachgeladen (schnell), den Rest
    # holt der Hintergrund-Sync (/sync). Fällt der Cache aus → ganz normal live.
    try:
        pw = _account_secret(acc)
        if not cache_mod.has_cache(session, account_id, folder):
            cache_mod.sync_folder(session, acc, pw, folder, cap=max(limit, 50))
        msgs = cache_mod.read_messages(session, account_id, folder, limit=limit, offset=offset, pin_flagged=pin_flagged)
        # Self-heal: 1. Seite leer, obwohl der Ordner Mails hat (z. B. nach dem
        # Löschen der einzigen gecachten Seite) -> live nachsyncen und erneut lesen.
        if not msgs and offset == 0:
            cache_mod.sync_folder(session, acc, pw, folder, cap=max(limit, 50))
            msgs = cache_mod.read_messages(session, account_id, folder, limit=limit, offset=offset, pin_flagged=pin_flagged)
            if not msgs:
                msgs = _pin_flagged_first(imap_mod.list_messages(acc, pw, folder=folder, limit=limit, offset=offset), pin_flagged)
        return msgs
    except Exception:  # noqa: BLE001 - Cache ist nur Beschleunigung
        return _pin_flagged_first(
            imap_mod.list_messages(acc, _account_secret(acc), folder=folder, limit=limit, offset=offset), pin_flagged
        )


@router.get("/{account_id}/search")
def search(
    account_id: int,
    q: str = Query(min_length=2, max_length=200),
    folder: str = "INBOX",
    all_folders: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Volltextsuche über IMAP — Kopfzeilen UND Mailtext, optional alle Ordner.

    Bewusst LIVE statt über den Cache: der Cache kennt nur die neuesten ~1000
    Kopfzeilen je Ordner und 160 Zeichen Vorschau. Wer eine zwei Jahre alte Mail
    sucht, findet sie nur so. Der Preis ist Wartezeit — deshalb hat die Suche im
    Backend eine Zeitgrenze und meldet dem Client, wenn sie abbrechen musste.
    """
    acc = _account(account_id, user, session)
    query = q.strip()
    if not query:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Suchbegriff fehlt")
    pw = _account_secret(acc)
    if all_folders:
        try:
            folders = imap_mod.list_folders(acc, pw)
        except Exception:  # noqa: BLE001 - dann wenigstens im gewählten Ordner suchen
            logger.warning("Ordnerliste für Suche nicht abrufbar (account_id=%s)", account_id, exc_info=True)
            folders = [folder]
    else:
        folders = [folder]
    try:
        return imap_mod.search_messages(acc, pw, query, folders, total_limit=limit)
    except imap_mod.ImapBusyError:
        raise
    except Exception:  # noqa: BLE001
        logger.warning("Suche fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Suche fehlgeschlagen")


@router.get("/{account_id}/folder-uids", response_model=list[str])
def folder_uids(
    account_id: int,
    folder: str = "INBOX",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[str]:
    """Alle UIDs eines Ordners (neueste zuerst) — für "Alle im Ordner auswählen"
    über Seitengrenzen hinweg.

    LIVE-first: hier zählt VOLLSTÄNDIGKEIT (wirklich ALLE Mails auswählen, z. B.
    um 614 Mails zu löschen). Der Cache ist oft nur teilweise gefüllt und würde
    zu wenige UIDs liefern. ``box.uids()`` ist billig (nur UIDs, keine Inhalte) und
    durch das IMAP-Timeout gebunden. Fällt der Live-Abruf aus -> Cache als Fallback."""
    acc = _account(account_id, user, session)
    try:
        return imap_mod.list_uids(acc, _account_secret(acc), folder)
    except Exception:  # noqa: BLE001 - Live fehlgeschlagen -> wenigstens den Cache
        return cache_mod.folder_uids(session, account_id, folder)


@router.post("/{account_id}/sync")
def sync_messages(
    account_id: int,
    folder: str = "INBOX",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Delta-Sync: holt neue Mails in den Cache, entfernt gelöschte, gleicht Flags
    ab. Das Frontend ruft das im Hintergrund auf, nachdem es den Cache gezeigt hat."""
    acc = _account(account_id, user, session)
    try:
        return cache_mod.sync_folder(session, acc, _account_secret(acc), folder)
    except Exception:  # noqa: BLE001
        logger.warning("Sync fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Sync fehlgeschlagen")


@router.post("/{account_id}/migrate")
def migrate_account(
    account_id: int,
    data: MigrateRequest,
    background: bool = False,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Migriert Mails aus diesem Quellkonto (z. B. Synology) in die uebrigen
    Konten des Users — pro Mail anhand des Empfängers ins passende Postfach.
    dry_run=True (Default) zeigt nur die Vorschau, schreibt nichts.

    ``background=true`` führt die (u. U. minutenlange) Migration in einem
    Hintergrund-Job aus und liefert sofort ``{job_id}`` — Status via
    ``GET /mail/jobs/{job_id}``. Ohne den Parameter bleibt es synchron (Alt-
    Verhalten, damit bestehende Clients unverändert weiterlaufen)."""
    source = _account(account_id, user, session)
    dest = _account(data.dest_account_id, user, session)  # prüft Eigentümer
    if dest.id == source.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Quelle und Ziel sind identisch")
    src_pw = _account_secret(source)
    dst_pw = _account_secret(dest)

    def _run() -> dict:
        return migrate_mod.migrate_folders(
            source, src_pw, dest, dst_pw,
            target_prefix=data.target_prefix, dry_run=data.dry_run, limit_per_folder=data.limit,
        )

    if background:
        job_id = jobs.create_job(user.id, "migrate")
        jobs.start(job_id, _run)
        return {"job_id": job_id}
    try:
        return _run()
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        logger.warning("Migration fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Migration fehlgeschlagen")


@router.post("/{account_id}/transfer")
def transfer(
    account_id: int,
    data: TransferRequest,
    background: bool = False,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Kopiert/verschiebt einzelne Mails oder einen ganzen Ordner aus diesem Konto
    in den Ordner eines ANDEREN Kontos des Users.

    ``background=true`` führt einen ganzen Ordner-Transfer als Hintergrund-Job aus
    und liefert sofort ``{job_id}`` (Status via ``GET /mail/jobs/{job_id}``); ohne
    den Parameter bleibt es synchron (Alt-Verhalten)."""
    source = _account(account_id, user, session)
    dest = _account(data.dest_account_id, user, session)
    src_pw = _account_secret(source)
    dst_pw = _account_secret(dest)

    def _run() -> dict:
        return migrate_mod.transfer_messages(
            source, src_pw, data.source_folder, data.uids,
            dest, dst_pw, data.dest_folder,
            move=data.move, limit=data.limit,
        )

    if background:
        job_id = jobs.create_job(user.id, "transfer")
        jobs.start(job_id, _run)
        return {"job_id": job_id}
    try:
        return _run()
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        logger.warning("Übertragen fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
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
    # Cache-first: schon einmal geöffnet → Body kommt SOFORT aus der DB (kein IMAP).
    try:
        cached = cache_mod.read_detail(session, account_id, folder, uid)
        # Alten Cache OHNE Echtheits-Analyse verwerfen -> live neu holen (mit auth).
        if cached is not None and cached.get("auth"):
            return cached
    except Exception:  # noqa: BLE001 - Cache ist nur Beschleunigung
        pass
    msg = imap_mod.get_message(acc, _account_secret(acc), uid, folder=folder)
    if msg is None:
        # Die Mail ist serverseitig weg (verschoben/gelöscht) — etwaigen stale
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


@router.get("/{account_id}/thread")
def thread(
    account_id: int,
    folder: str = "INBOX",
    uid: str = "",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    """Alle Nachrichten einer Konversation über mehrere Ordner (inkl. „Gesendet").

    Wird beim Öffnen eines Threads aufgerufen, um die EIGENEN Antworten mit
    einzuweben. Fehler/keine Treffer → leere Liste (das Frontend zeigt dann nur
    die ohnehin geladenen Ordner-Mails).

    BEWUSST OHNE response_model: das ``folder`` je Treffer MUSS erhalten bleiben
    (MessageHeader kennt es nicht und würde es rausfiltern). Ohne den Ordner
    kollidieren im Frontend die Zeilen-Keys ordnerübergreifend (gleiche UID in
    INBOX und Gesendet) und gesendete Mails ließen sich nicht öffnen."""
    acc = _account(account_id, user, session)
    if not uid:
        return []
    try:
        return imap_mod.collect_thread(acc, _account_secret(acc), folder, uid)
    except Exception:  # noqa: BLE001 - Thread-Zusammenführung ist Komfort, kein Muss
        return []


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
    raw = imap_mod.get_raw(acc, _account_secret(acc), uid, folder=folder)
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
    result = imap_mod.get_attachment(acc, _account_secret(acc), uid, index, folder=folder)
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
        imap_mod.set_flags(acc, _account_secret(acc), uid, folder=folder, seen=seen, flagged=flagged)
    except Exception:  # noqa: BLE001
        logger.warning("Flags setzen fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Aktion fehlgeschlagen")
    try:
        cache_mod.update_flags(session, account_id, folder, uid, seen=seen, flagged=flagged)
    except Exception:  # noqa: BLE001 - Cache-Pflege darf nie die Aktion kippen
        pass
    bus.publish(user.id, {"type": "mail", "account_id": account_id, "folder": folder})
    return {"ok": True}


@router.post("/{account_id}/messages/{uid}/label")
def set_label(
    account_id: int,
    uid: str,
    keyword: str,
    on: bool = True,
    folder: str = "INBOX",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Ein Label (IMAP-Keyword) an einer Nachricht setzen/entfernen."""
    acc = _account(account_id, user, session)
    ok = imap_mod.set_keyword(acc, _account_secret(acc), uid, folder, keyword, on)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Label konnte nicht gesetzt werden (Server erlaubt evtl. keine Keywords)")
    try:
        cache_mod.update_keyword(session, account_id, folder, uid, keyword, on)
    except Exception:  # noqa: BLE001 - Cache-Pflege darf die Aktion nie kippen
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
        imap_mod.move_message(acc, _account_secret(acc), uid, dest, folder=folder)
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
    """Wärmt den Body-Cache für eine Liste von UIDs in EINER IMAP-Session vor.

    Holt nur die noch nicht gecachten Bodies (ein Login + ein Sammel-Fetch) und
    legt sie ab. Danach kommt jedes Öffnen dieser Mails sofort aus der DB.
    Best-effort: Fehler werden geschluckt (Cache ist reine Beschleunigung).
    """
    acc = _account(account_id, user, session)
    try:
        missing = cache_mod.uncached_detail_uids(session, account_id, data.folder, data.uids)
        if not missing:
            return {"ok": True, "cached": 0}
        details = imap_mod.get_messages(acc, _account_secret(acc), missing, folder=data.folder)
        for d in details:
            cache_mod.write_detail(session, account_id, data.folder, d.get("uid", ""), d)
        return {"ok": True, "cached": len(details)}
    except Exception:  # noqa: BLE001 - Vorwärmen darf nie eine Anfrage kippen
        return {"ok": False, "cached": 0}


@router.post("/{account_id}/messages/batch-delete")
def delete_messages_batch(
    account_id: int,
    data: BatchRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Löscht mehrere Mails in EINER IMAP-Session (statt N Einzel-Requests/Logins)."""
    acc = _account(account_id, user, session)
    try:
        result = imap_mod.delete_messages(acc, _account_secret(acc), data.uids, folder=data.folder)
    except Exception:  # noqa: BLE001
        logger.warning("Batch-Löschen fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Löschen fehlgeschlagen")
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
    """Setzt Seen/Flagged für VIELE Mails in EINER IMAP-Session (z. B. "alles als
    gelesen" bei tausenden Mails) — statt N Einzel-Requests/Logins."""
    acc = _account(account_id, user, session)
    try:
        n = imap_mod.set_flags_many(
            acc, _account_secret(acc), data.uids, folder=data.folder, seen=seen, flagged=flagged,
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
        result = imap_mod.move_messages(acc, _account_secret(acc), data.uids, data.dest, folder=data.folder)
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
        result = imap_mod.delete_message(acc, _account_secret(acc), uid, folder=folder)
    except Exception:  # noqa: BLE001
        logger.warning("Nachricht löschen fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Löschen fehlgeschlagen")
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
    _reject_if_too_large(data)
    acc = _account(account_id, user, session)
    try:
        ok = imap_mod.save_draft(
            acc,
            _account_secret(acc),
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
    _reject_if_too_large(data)
    # Sync-DB-Zugriff (session.get) in einer async-Route -> Threadpool, damit der
    # Event-Loop nicht blockiert.
    acc = await run_in_threadpool(_account, account_id, user, session)
    pw = _account_secret(acc)
    try:
        raw = await smtp_mod.send_message(
            acc,
            pw,
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
    except SMTPRecipientsRefused as exc:
        # Zielserver hat Empfänger abgelehnt (z. B. Tippfehler / unbekannte Domain).
        try:
            addrs = ", ".join(str(a) for a in exc.recipients)
        except Exception:  # noqa: BLE001
            addrs = ""
        logger.warning("Versand: Empfänger abgelehnt (account_id=%s): %s", account_id, addrs)
        detail = "Empfänger abgelehnt" + (f": {addrs}" if addrs else "")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail + " — Adresse prüfen (unbekannt oder ungültige Domain?).")
    except Exception:  # noqa: BLE001
        logger.warning("Versand fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Versand fehlgeschlagen")

    # Kopie in „Gesendet" ablegen (best-effort; IMAP ist blockierend → Threadpool).
    # Schlägt das fehl, gilt der Versand trotzdem als erfolgreich.
    try:
        await run_in_threadpool(imap_mod.save_to_sent, acc, pw, raw)
    except Exception:  # noqa: BLE001
        logger.warning("Gesendete Mail nicht in 'Gesendet' ablegbar (account_id=%s)", account_id, exc_info=True)
    return {"sent": True}


@router.post("/{account_id}/messages/{uid}/read-receipt", status_code=status.HTTP_202_ACCEPTED)
async def send_read_receipt(
    account_id: int,
    uid: str,
    folder: str = "INBOX",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Sendet eine Lesebestätigung (MDN) an den Absender — nur wenn dieser sie
    per Header angefordert hat. Wird vom Bestätigungs-Hinweis im Reader ausgelöst."""
    # Sync-DB-Zugriff in einer async-Route -> Threadpool (Event-Loop nicht blocken).
    acc = await run_in_threadpool(_account, account_id, user, session)
    pw = _account_secret(acc)
    msg = await run_in_threadpool(imap_mod.get_message, acc, pw, uid, folder)
    if msg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nachricht nicht gefunden")
    req = (msg.get("mdn_request") or "").strip()
    if not req:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Diese Nachricht fordert keine Lesebestätigung an")
    try:
        await smtp_mod.send_mdn(
            acc,
            pw,
            to=req,
            original_message_id=msg.get("message_id", ""),
            original_subject=msg.get("subject", ""),
            original_date=msg.get("date", ""),
        )
    except Exception:  # noqa: BLE001
        logger.warning("Lesebestätigung senden fehlgeschlagen (account_id=%s)", account_id, exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Lesebestätigung konnte nicht gesendet werden")
    return {"sent": True}
