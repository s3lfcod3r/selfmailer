"""Geplante Mails (Schedule Send) verwalten: auflisten + abbrechen. Das Anlegen
läuft account-scoped über POST /mail/{id}/schedule; der Versand über den Scheduler."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..core.db import get_session
from ..models import ScheduledMail, User
from ..schemas import ScheduledOut
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/schedule", tags=["schedule"])


@router.get("", response_model=list[ScheduledOut])
def list_scheduled(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> list[ScheduledOut]:
    """Anstehende (pending) + fehlgeschlagene geplante Mails, älteste Fälligkeit zuerst."""
    rows = session.exec(
        select(ScheduledMail)
        .where(ScheduledMail.user_id == user.id, ScheduledMail.status.in_(["pending", "failed"]))
        .order_by(ScheduledMail.send_at)
        .limit(500)
    ).all()
    return [
        ScheduledOut(
            id=r.id, account_id=r.account_id, subject=r.subject,
            to=[x.strip() for x in r.to_addrs.split(",") if x.strip()],
            send_at=r.send_at, status=r.status, error=r.error, created_at=r.created_at,
        )
        for r in rows
    ]


@router.delete("/{scheduled_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_scheduled(
    scheduled_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    """Geplante Mail abbrechen/entfernen. Bei ``pending`` verhindert das den Versand
    (der Scheduler holt nur pending); die Zeile wird gelöscht (samt Body/Anhang)."""
    row = session.get(ScheduledMail, scheduled_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Geplante Mail nicht gefunden")
    session.delete(row)
    session.commit()
