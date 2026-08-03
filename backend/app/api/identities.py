"""Absender-Identitäten/Aliase: CRUD pro User. Eine Identität hängt an einem
Konto (account_id) und setzt beim Schreiben From-Header + Signatur."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..core.db import get_session
from ..models import MailAccount, MailIdentity, User
from ..schemas import IdentityCreate, IdentityOut, IdentityUpdate
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/identities", tags=["identities"])

# Harte Obergrenze, damit die Liste nie unbegrenzt viele Zeilen zurückgibt.
_MAX_LIST = 200


def _owned(identity_id: int, user: User, session: Session) -> MailIdentity:
    ident = session.get(MailIdentity, identity_id)
    if ident is None or ident.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identität nicht gefunden")
    return ident


def _assert_account(account_id: int, user: User, session: Session) -> None:
    """Konto muss dem User gehören — sonst könnte man Identitäten an fremde Konten hängen."""
    acc = session.get(MailAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Konto nicht gefunden")


def _clear_default(account_id: int, user: User, session: Session, keep_id: int | None = None) -> None:
    """Sorgt dafür, dass je Konto höchstens eine Identität als Standard markiert ist."""
    stmt = select(MailIdentity).where(
        MailIdentity.user_id == user.id,
        MailIdentity.account_id == account_id,
        MailIdentity.is_default == True,  # noqa: E712
    )
    for other in session.exec(stmt).all():
        if keep_id is None or other.id != keep_id:
            other.is_default = False
            session.add(other)


@router.get("", response_model=list[IdentityOut])
def list_identities(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> list[MailIdentity]:
    stmt = (
        select(MailIdentity)
        .where(MailIdentity.user_id == user.id)
        .order_by(MailIdentity.account_id, MailIdentity.name)
        .limit(_MAX_LIST)
    )
    return list(session.exec(stmt).all())


@router.post("", response_model=IdentityOut, status_code=status.HTTP_201_CREATED)
def create_identity(
    data: IdentityCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MailIdentity:
    _assert_account(data.account_id, user, session)
    payload = data.model_dump()
    payload["email"] = str(payload["email"])
    ident = MailIdentity(user_id=user.id, **payload)
    if ident.is_default:
        _clear_default(ident.account_id, user, session)
    session.add(ident)
    session.commit()
    session.refresh(ident)
    return ident


@router.patch("/{identity_id}", response_model=IdentityOut)
def update_identity(
    identity_id: int,
    data: IdentityUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MailIdentity:
    ident = _owned(identity_id, user, session)
    fields = data.model_dump(exclude_unset=True)
    if "email" in fields and fields["email"] is not None:
        fields["email"] = str(fields["email"])
    for field, value in fields.items():
        setattr(ident, field, value)
    if ident.is_default:
        _clear_default(ident.account_id, user, session, keep_id=ident.id)
    ident.updated_at = dt.datetime.now(dt.timezone.utc)
    session.add(ident)
    session.commit()
    session.refresh(ident)
    return ident


@router.delete("/{identity_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_identity(
    identity_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    ident = _owned(identity_id, user, session)
    session.delete(ident)
    session.commit()
