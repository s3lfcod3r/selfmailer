"""Header-only operations must not transfer cached message bodies from SQLite."""
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine
from app.models import CachedMessage
from app.mail import cache


def run(operation):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(CachedMessage(account_id=1, folder="INBOX", uid="1", date_str="Thu, 01 Oct 2026 12:00:00 +0000",
                                  subject="Invoice", detail_json='{"text":"' + "x" * 50000 + '"}'))
        session.add(CachedMessage(account_id=1, folder="INBOX", uid="2", detail_json=""))
        session.commit()
    queries = []
    event.listen(engine, "before_cursor_execute", lambda conn, cursor, statement, params, context, many: queries.append(statement))
    try:
        with Session(engine) as session:
            result = operation(session)
        selects = [q.split("FROM")[0] for q in queries if q.lstrip().upper().startswith("SELECT")]
        assert selects and all("detail_json" not in q for q in selects), selects
        return result
    finally:
        engine.dispose()


def test_read_header_omits_body_column():
    result = run(lambda session: cache.read_header(session, 1, "INBOX", "1"))
    assert result["subject"] == "Invoice" and "detail_json" not in result


def test_unread_preview_omits_body_column():
    assert len(run(lambda session: cache.recent_unseen(session, 1))) == 2


def test_prefetch_presence_query_returns_only_uids():
    assert run(lambda session: cache.uncached_detail_uids(session, 1, "INBOX", ["1", "2", "3"])) == ["2", "3"]


def test_sort_date_repair_omits_body_column():
    assert run(cache.backfill_sort_dates) == 1
