"""Vorlagen/Textbausteine fürs Schreiben: einfache CRUD-Funktion pro User."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..core.db import get_session
from ..models import MailTemplate, User
from ..schemas import TemplateCreate, TemplateOut
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])

# Harte Obergrenze, damit die Liste nie unbegrenzt viele Zeilen zurückgibt.
_MAX_LIST = 500


def _owned(template_id: int, user: User, session: Session) -> MailTemplate:
    tpl = session.get(MailTemplate, template_id)
    if tpl is None or tpl.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vorlage nicht gefunden")
    return tpl


@router.get("", response_model=list[TemplateOut])
def list_templates(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> list[MailTemplate]:
    stmt = (
        select(MailTemplate)
        .where(MailTemplate.user_id == user.id)
        .order_by(MailTemplate.name)
        .limit(_MAX_LIST)
    )
    return list(session.exec(stmt).all())


@router.post("", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(
    data: TemplateCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MailTemplate:
    tpl = MailTemplate(user_id=user.id, **data.model_dump())
    session.add(tpl)
    session.commit()
    session.refresh(tpl)
    return tpl


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(
    template_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    tpl = _owned(template_id, user, session)
    session.delete(tpl)
    session.commit()
