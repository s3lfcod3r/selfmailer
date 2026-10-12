"""Monotone IDs auch für bestehende SQLite-Tabellen ohne AUTOINCREMENT."""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, SQLModel, select

from ..models import DavAccount, EntitySequence, MailAccount, User


def _advance(session: Session, model: type, *, allocate: bool) -> int:
    current = session.exec(select(func.max(model.id))).one() or 0
    if not allocate:
        # Alte Versionen ließen Kinder gelöschter Eltern bestehen. Deren IDs
        # ebenfalls reservieren, statt sie einem künftigen Nutzer zuzuordnen.
        for table in SQLModel.metadata.tables.values():
            for fk in table.foreign_keys:
                if fk.column.table.name == model.__tablename__ and fk.column.name == "id":
                    current = max(current, session.exec(select(func.max(fk.parent))).one() or 0)
    step = 1 if allocate else 0
    statement = insert(EntitySequence).values(name=model.__tablename__, last_id=current + step)
    statement = statement.on_conflict_do_update(index_elements=[EntitySequence.name], set_={
        "last_id": func.max(EntitySequence.last_id, current) + step,
    })
    session.execute(statement)
    # Der SQLite-Schreiblock bleibt bis zum gemeinsamen Commit erhalten.
    return session.exec(select(EntitySequence.last_id).where(EntitySequence.name == model.__tablename__)).one()


def allocate_id(session: Session, model: type) -> int:
    return _advance(session, model, allocate=True)


def initialize_sequences(session: Session) -> None:
    for model in (User, MailAccount, DavAccount):
        _advance(session, model, allocate=False)
