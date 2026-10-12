"""Transaktionales Löschen sämtlicher abhängiger Daten eines Kontos/Users."""
from __future__ import annotations

from sqlalchemy import delete, update
from sqlmodel import Session, SQLModel, select

from ..models import DavAccount, MailAccount, User


def _delete_children(session: Session, column: str, ids: list[int], *, exclude: set[str] | None = None) -> None:
    if not ids:
        return
    for table in reversed(SQLModel.metadata.sorted_tables):
        if table.name not in (exclude or set()) and column in table.c:
            session.execute(delete(table).where(table.c[column].in_(ids)))


def delete_mail_account(session: Session, account: MailAccount) -> None:
    """Kein Commit: Aufrufer löscht Kinder und Eltern in derselben Transaktion."""
    _delete_children(session, "account_id", [account.id])
    session.delete(account)


def delete_user_data(session: Session, user: User) -> None:
    account_ids = list(session.exec(select(MailAccount.id).where(MailAccount.user_id == user.id)).all())
    dav_ids = list(session.exec(select(DavAccount.id).where(DavAccount.user_id == user.id)).all())
    _delete_children(session, "account_id", account_ids)
    _delete_children(session, "dav_account_id", dav_ids)
    if dav_ids:
        session.execute(update(User).where(User.bday_cal_account_id.in_(dav_ids)).values(bday_cal_account_id=None, bday_cal_id=""))
    _delete_children(session, "user_id", [user.id], exclude={"mailaccount", "davaccount"})
    session.execute(delete(MailAccount).where(MailAccount.user_id == user.id))
    session.execute(delete(DavAccount).where(DavAccount.user_id == user.id))
    session.delete(user)
