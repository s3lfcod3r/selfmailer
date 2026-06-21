"""ntfy-Push-Konfiguration des angemeldeten Users (Self-Service)."""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..core.db import get_session
from ..mail import fcm as fcm_mod
from ..models import DeviceToken, FolderNotify, MailAccount, PushConfig, User
from ..schemas import DeviceTokenIn, FolderNotifyIn, PushConfigIn, PushConfigOut
from .deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/push", tags=["push"])


def _own_account(account_id: int, user: User, session: Session) -> MailAccount:
    acc = session.get(MailAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Konto nicht gefunden")
    return acc


@router.get("", response_model=PushConfigOut)
def get_push(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> PushConfigOut:
    cfg = session.exec(select(PushConfig).where(PushConfig.user_id == user.id)).first()
    if cfg is None:
        return PushConfigOut(enabled=False, ntfy_url="", topic="")
    return PushConfigOut(enabled=cfg.enabled, ntfy_url=cfg.ntfy_url, topic=cfg.topic)


@router.put("", response_model=PushConfigOut)
def set_push(
    data: PushConfigIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> PushConfigOut:
    cfg = session.exec(select(PushConfig).where(PushConfig.user_id == user.id)).first()
    if cfg is None:
        cfg = PushConfig(user_id=user.id)
    cfg.ntfy_url = data.ntfy_url.rstrip("/")
    cfg.topic = data.topic.strip()
    cfg.enabled = data.enabled and bool(cfg.ntfy_url) and bool(cfg.topic)
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    return PushConfigOut(enabled=cfg.enabled, ntfy_url=cfg.ntfy_url, topic=cfg.topic)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_push(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> None:
    cfg = session.exec(select(PushConfig).where(PushConfig.user_id == user.id)).first()
    if cfg is not None:
        session.delete(cfg)
        session.commit()


# ---- Pro-Ordner-Benachrichtigung je Konto -------------------------------
@router.get("/folders", response_model=list[str])
def get_notify_folders(
    account_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[str]:
    """Welche Ordner dieses Kontos lösen eine Benachrichtigung aus."""
    _own_account(account_id, user, session)
    rows = session.exec(select(FolderNotify).where(FolderNotify.account_id == account_id)).all()
    return [r.folder for r in rows]


@router.put("/folders", response_model=list[str])
def set_notify_folders(
    data: FolderNotifyIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[str]:
    """Ersetzt die Ordnerauswahl eines Kontos (volle Liste). Neue Ordner starten
    mit Basis -1 (erster Lauf meldet nicht)."""
    _own_account(data.account_id, user, session)
    existing = {
        r.folder: r
        for r in session.exec(select(FolderNotify).where(FolderNotify.account_id == data.account_id)).all()
    }
    wanted = {f for f in data.folders if f.strip()}
    for folder, row in existing.items():
        if folder not in wanted:
            session.delete(row)
    for folder in wanted:
        if folder not in existing:
            session.add(FolderNotify(account_id=data.account_id, folder=folder, last_unseen=-1))
    session.commit()
    return sorted(wanted)


# ---- FCM-Geraetetokens (Google-Push) ------------------------------------
@router.post("/device", status_code=status.HTTP_204_NO_CONTENT)
def register_device(
    data: DeviceTokenIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    """Registriert/aktualisiert den FCM-Token dieses Geräts für den User."""
    if not data.token.strip():
        return
    exists = session.exec(
        select(DeviceToken).where(DeviceToken.user_id == user.id, DeviceToken.token == data.token)
    ).first()
    if exists is None:
        session.add(DeviceToken(user_id=user.id, token=data.token, platform=data.platform))
        session.commit()


@router.post("/test")
def test_push(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Schickt eine Test-Benachrichtigung über alle aktiven Kanäle und meldet den
    Diagnose-Status zurück — zeigt sofort, wo es klemmt."""
    tokens = session.exec(select(DeviceToken).where(DeviceToken.user_id == user.id)).all()
    cfg = session.exec(select(PushConfig).where(PushConfig.user_id == user.id)).first()
    fcm_on = fcm_mod.enabled()
    ntfy_on = bool(cfg and cfg.enabled and cfg.ntfy_url and cfg.topic)

    if fcm_on and tokens:
        try:
            fcm_mod.notify(session, user.id, "SelfMailer Test", "Push funktioniert ✅")
        except Exception:  # noqa: BLE001
            logger.warning("Test-FCM fehlgeschlagen (user_id=%s)", user.id, exc_info=True)
    if ntfy_on and cfg is not None:
        try:
            httpx.post(cfg.ntfy_url.rstrip("/"),
                       json={"topic": cfg.topic, "title": "SelfMailer Test", "message": "Push funktioniert ✅", "tags": ["white_check_mark"]},
                       timeout=10.0)
        except Exception:  # noqa: BLE001
            logger.warning("Test-ntfy fehlgeschlagen (user_id=%s)", user.id, exc_info=True)

    return {"fcm_enabled": fcm_on, "device_tokens": len(tokens), "ntfy_configured": ntfy_on}


@router.delete("/device", status_code=status.HTTP_204_NO_CONTENT)
def unregister_device(
    data: DeviceTokenIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    for row in session.exec(
        select(DeviceToken).where(DeviceToken.user_id == user.id, DeviceToken.token == data.token)
    ).all():
        session.delete(row)
    session.commit()
