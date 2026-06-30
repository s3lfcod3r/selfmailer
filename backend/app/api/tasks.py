"""Aufgaben / To-dos: pro User (CRUD).

Eigenständiger lokaler Store, analog zu Notizen/Kalender. Wird u. a. im
Kalender-Seitenpanel angezeigt.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..core.db import get_session
from ..models import Task, User
from ..schemas import TaskCreate, TaskOut, TaskUpdate
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


def _owned(task_id: int, user: User, session: Session) -> Task:
    tk = session.get(Task, task_id)
    if tk is None or tk.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aufgabe nicht gefunden")
    return tk


@router.get("", response_model=list[TaskOut])
def list_tasks(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[Task]:
    # Offen zuerst, dann nach Position und Fälligkeit.
    stmt = (
        select(Task)
        .where(Task.user_id == user.id)
        .order_by(Task.done, Task.position, Task.due, Task.created_at)
    )
    return list(session.exec(stmt).all())


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    data: TaskCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Task:
    tk = Task(user_id=user.id, **data.model_dump())
    session.add(tk)
    session.commit()
    session.refresh(tk)
    return tk


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int,
    data: TaskUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Task:
    tk = _owned(task_id, user, session)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(tk, field, value)
    tk.updated_at = dt.datetime.now(dt.timezone.utc)
    session.add(tk)
    session.commit()
    session.refresh(tk)
    return tk


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    tk = _owned(task_id, user, session)
    session.delete(tk)
    session.commit()
