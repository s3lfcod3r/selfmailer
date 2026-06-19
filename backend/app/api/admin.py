"""Admin: User verwalten (mehrere User anlegen/sperren/loeschen).

Selbstschutz: ein Admin kann sich nicht selbst deaktivieren oder loeschen,
damit man sich nicht aus dem eigenen System aussperrt.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..core.db import get_session
from ..core.security import hash_password
from ..models import Role, User
from ..schemas import UserCreate, UserOut
from .deps import require_admin

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/users", response_model=list[UserOut])
def list_users(
    _: User = Depends(require_admin), session: Session = Depends(get_session)
) -> list[User]:
    return list(session.exec(select(User)).all())


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    data: UserCreate,
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> User:
    exists = session.exec(select(User).where(User.username == data.username)).first()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Benutzername bereits vergeben")
    user = User(
        username=data.username,
        display_name=data.display_name or data.username,
        password_hash=hash_password(data.password),
        role=data.role,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@router.patch("/users/{user_id}/active", response_model=UserOut)
def set_active(
    user_id: int,
    active: bool,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> User:
    if user_id == admin.id and not active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Eigenes Konto nicht deaktivierbar")
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User nicht gefunden")
    user.is_active = active
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@router.patch("/users/{user_id}/password", response_model=UserOut)
def reset_password(
    user_id: int,
    new_password: str,
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> User:
    if len(new_password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Passwort zu kurz (min. 8)")
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User nicht gefunden")
    user.password_hash = hash_password(new_password)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> None:
    if user_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Eigenes Konto nicht loeschbar")
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User nicht gefunden")
    session.delete(user)
    session.commit()
