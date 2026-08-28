"""Abwesenheitsnotiz: Einstellungen je Konto lesen/schreiben. Der eigentliche
Versand läuft im Scheduler (mail/vacation.py) — defensiv, Standard AUS."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..core.db import get_session
from ..models import MailAccount, User, VacationSetting
from ..schemas import VacationOut, VacationUpdate
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/vacation", tags=["vacation"])


def _account(account_id: int, user: User, session: Session) -> MailAccount:
    acc = session.get(MailAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Konto nicht gefunden")
    return acc


def _get_or_default(account_id: int, user: User, session: Session) -> VacationSetting:
    row = session.exec(select(VacationSetting).where(VacationSetting.account_id == account_id)).first()
    return row or VacationSetting(user_id=user.id, account_id=account_id)


def _valid_date(s: str) -> str:
    """Leer ist ok; sonst muss es ein echtes YYYY-MM-DD sein (sonst 422)."""
    v = (s or "").strip()
    if not v:
        return ""
    try:
        dt.date.fromisoformat(v)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Datum erwartet (JJJJ-MM-TT)")
    return v


@router.get("/{account_id}", response_model=VacationOut)
def get_vacation(
    account_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    _account(account_id, user, session)
    row = _get_or_default(account_id, user, session)
    return {
        "account_id": account_id, "enabled": row.enabled, "subject": row.subject,
        "body": row.body, "start_date": row.start_date, "end_date": row.end_date,
        "interval_days": row.interval_days,
    }


@router.put("/{account_id}", response_model=VacationOut)
def put_vacation(
    account_id: int,
    data: VacationUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    _account(account_id, user, session)
    row = _get_or_default(account_id, user, session)
    was_enabled = row.enabled
    fields = data.model_dump(exclude_unset=True)
    if "start_date" in fields and fields["start_date"] is not None:
        fields["start_date"] = _valid_date(fields["start_date"])
    if "end_date" in fields and fields["end_date"] is not None:
        fields["end_date"] = _valid_date(fields["end_date"])
    for key, val in fields.items():
        if val is not None:
            setattr(row, key, val)
    if row.enabled and not was_enabled:
        # Frisch eingeschaltet: Startpunkt zurücksetzen — der nächste Scheduler-
        # Lauf initialisiert ihn und beantwortet damit NUR künftige Mails.
        row.last_uid = 0
    row.updated_at = dt.datetime.now(dt.timezone.utc)
    session.add(row)
    session.commit()
    session.refresh(row)
    return {
        "account_id": account_id, "enabled": row.enabled, "subject": row.subject,
        "body": row.body, "start_date": row.start_date, "end_date": row.end_date,
        "interval_days": row.interval_days,
    }
