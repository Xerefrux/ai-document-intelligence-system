"""Engine and session helpers, plus the SQLite full text index.

SQLite runs in WAL mode so the API can read while workers write. Keyword search uses two FTS5
tables that read their text from the `pages` and `records` tables (external content), kept in
sync by triggers, so there is no second copy of the extracted text.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

log = logging.getLogger(__name__)

FTS_STATEMENTS = [
    """CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5(
         text, content='pages', content_rowid='id', tokenize='porter unicode61 remove_diacritics 2')""",
    """CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
         INSERT INTO page_fts(rowid, text) VALUES (new.id, new.text);
       END""",
    """CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
         INSERT INTO page_fts(page_fts, rowid, text) VALUES ('delete', old.id, old.text);
       END""",
    """CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE OF text ON pages BEGIN
         INSERT INTO page_fts(page_fts, rowid, text) VALUES ('delete', old.id, old.text);
         INSERT INTO page_fts(rowid, text) VALUES (new.rowid, new.text);
       END""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS record_fts USING fts5(
         search_text, content='records', content_rowid='rowid', tokenize='porter unicode61 remove_diacritics 2')""",
    """CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
         INSERT INTO record_fts(rowid, search_text) VALUES (new.rowid, new.search_text);
       END""",
    """CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
         INSERT INTO record_fts(record_fts, rowid, search_text) VALUES ('delete', old.rowid, old.search_text);
       END""",
    """CREATE TRIGGER IF NOT EXISTS records_au AFTER UPDATE OF search_text ON records BEGIN
         INSERT INTO record_fts(record_fts, rowid, search_text) VALUES ('delete', old.rowid, old.search_text);
         INSERT INTO record_fts(rowid, search_text) VALUES (new.rowid, new.search_text);
       END""",
]


def make_engine(url: str, *, echo: bool = False) -> Engine:
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"timeout": 30, "check_same_thread": False}
    engine = create_engine(url, echo=echo, pool_pre_ping=True, **kwargs)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def init_db(engine: Engine) -> None:
    """Create tables and, on SQLite, the full text index. Safe to call on every start."""
    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            for stmt in FTS_STATEMENTS:
                conn.execute(text(stmt))
    else:
        log.warning("Keyword search needs SQLite FTS5; on %s search falls back to simple LIKE matching.", engine.dialect.name)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
