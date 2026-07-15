"""Notizen: einfache CRUD-Funktion pro User."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..core.db import get_session
from ..models import Note, User
from ..schemas import NoteCreate, NoteOut, NoteUpdate
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])

# Harte Obergrenze, damit die Liste nie unbegrenzt viele Zeilen zurückgibt.
_MAX_LIST = 2000


def _owned(note_id: int, user: User, session: Session) -> Note:
    note = session.get(Note, note_id)
    if note is None or note.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notiz nicht gefunden")
    return note


@router.get("", response_model=list[NoteOut])
def list_notes(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> list[Note]:
    stmt = (
        select(Note)
        .where(Note.user_id == user.id)
        .order_by(Note.pinned.desc(), Note.updated_at.desc())
        .limit(_MAX_LIST)
    )
    return list(session.exec(stmt).all())


@router.post("", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
def create_note(
    data: NoteCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Note:
    note = Note(user_id=user.id, **data.model_dump())
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


@router.patch("/{note_id}", response_model=NoteOut)
def update_note(
    note_id: int,
    data: NoteUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Note:
    note = _owned(note_id, user, session)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(note, field, value)
    note.updated_at = dt.datetime.now(dt.timezone.utc)
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    note_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    note = _owned(note_id, user, session)
    session.delete(note)
    session.commit()
