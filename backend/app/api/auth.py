"""Auth: First-Run-Setup, Login, eigenes Profil."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, func, select

from ..core.config import get_settings
from ..core.db import get_session
from ..core.security import create_access_token, hash_password, verify_password
from ..models import Role, User
from ..schemas import LoginRequest, SetupRequest, TokenResponse, UserOut
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _user_count(session: Session) -> int:
    return session.exec(select(func.count()).select_from(User)).one()


@router.get("/status")
def setup_status(session: Session = Depends(get_session)) -> dict:
    """Sagt dem Frontend, ob der erste Admin noch angelegt werden muss."""
    return {"needs_setup": _user_count(session) == 0}


@router.post("/setup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def setup(data: SetupRequest, session: Session = Depends(get_session)) -> TokenResponse:
    if _user_count(session) > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup bereits abgeschlossen")

    settings = get_settings()
    # Wenn ein ADMIN_TOKEN per Env gesetzt ist, muss er stimmen.
    if settings.admin_token and data.admin_token != settings.admin_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin-Token falsch")

    admin = User(
        username=data.username,
        display_name=data.display_name or data.username,
        password_hash=hash_password(data.password),
        role=Role.admin,
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return TokenResponse(access_token=create_access_token(admin.username, admin.role.value))


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, session: Session = Depends(get_session)) -> TokenResponse:
    user = session.exec(select(User).where(User.username == data.username)).first()
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Anmeldedaten falsch")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Konto gesperrt")
    return TokenResponse(access_token=create_access_token(user.username, user.role.value))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
