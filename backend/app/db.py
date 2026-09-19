from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """Stores naive UTC in SQLite and always returns timezone-aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


SessionLocal = sessionmaker(expire_on_commit=False)
_engine: Engine | None = None


def init_engine(url: str) -> Engine:
    global _engine
    if _engine is not None:
        _engine.dispose()
    is_sqlite = url.startswith("sqlite")
    _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15} if is_sqlite else {})
    if is_sqlite:
        @event.listens_for(_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=15000")
            cur.close()

    SessionLocal.configure(bind=_engine)
    from . import models  # noqa: F401  (registers tables)

    Base.metadata.create_all(_engine)
    return _engine


def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


@contextmanager
def session_scope() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
