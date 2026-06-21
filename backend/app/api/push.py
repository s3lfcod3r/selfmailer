"""ntfy-Push-Konfiguration des angemeldeten Users (Self-Service)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlmodel import Session, select

from ..core.db import get_session
from ..models import PushConfig, User
from ..schemas import PushConfigIn, PushConfigOut
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/push", tags=["push"])


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
