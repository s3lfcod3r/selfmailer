"""Labels/Schlagworte: farbige Definitionen pro User. Angewendet werden sie als
IMAP-Keyword direkt an der Nachricht (siehe api/mail.py::set_label)."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..core.db import get_session
from ..models import MailLabel, User
from ..schemas import LabelCreate, LabelOut, LabelUpdate
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/labels", tags=["labels"])

_MAX = 100


def _slug(name: str) -> str:
    """Name in ein IMAP-taugliches Keyword-Atom umwandeln (nur A-Za-z0-9_)."""
    s = re.sub(r"[^A-Za-z0-9_]+", "_", (name or "").strip()).strip("_")
    return s or "Label"


def _unique_keyword(base: str, user_id: int, session: Session) -> str:
    """Eindeutiges Keyword je User erzeugen (bei Kollision _2, _3 …)."""
    existing = {
        lbl.keyword for lbl in session.exec(select(MailLabel).where(MailLabel.user_id == user_id)).all()
    }
    if base not in existing:
        return base
    for i in range(2, 1000):
        cand = f"{base}_{i}"
        if cand not in existing:
            return cand
    return f"{base}_{user_id}"


def _owned(label_id: int, user: User, session: Session) -> MailLabel:
    lbl = session.get(MailLabel, label_id)
    if lbl is None or lbl.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Label nicht gefunden")
    return lbl


@router.get("", response_model=list[LabelOut])
def list_labels(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> list[MailLabel]:
    stmt = select(MailLabel).where(MailLabel.user_id == user.id).order_by(MailLabel.name).limit(_MAX)
    return list(session.exec(stmt).all())


@router.post("", response_model=LabelOut, status_code=status.HTTP_201_CREATED)
def create_label(
    data: LabelCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MailLabel:
    keyword = _unique_keyword(_slug(data.name), user.id, session)
    lbl = MailLabel(user_id=user.id, name=data.name, color=data.color, keyword=keyword)
    session.add(lbl)
    session.commit()
    session.refresh(lbl)
    return lbl


@router.patch("/{label_id}", response_model=LabelOut)
def update_label(
    label_id: int,
    data: LabelUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MailLabel:
    lbl = _owned(label_id, user, session)
    # keyword bleibt STABIL (schon an Mails vergeben) — nur name/color änderbar.
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(lbl, field, value)
    session.add(lbl)
    session.commit()
    session.refresh(lbl)
    return lbl


@router.delete("/{label_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_label(
    label_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    lbl = _owned(label_id, user, session)
    session.delete(lbl)
    session.commit()
