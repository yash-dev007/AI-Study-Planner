"""SQLAlchemy engine, session factory, and declarative base."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _prepare_sqlite_path(url: str) -> None:
    prefix = "sqlite:///"
    if url.startswith(prefix):
        raw = url[len(prefix) :]
        if raw and raw != ":memory:":
            Path(raw).parent.mkdir(parents=True, exist_ok=True)


_prepare_sqlite_path(settings.database_url)

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,
    connect_args=connect_args,
    future=True,
)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(Engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts and background jobs."""

    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
