"""Database session and engine helpers.

database_url fallback:
  - Render: set via DATABASE_URL env (fromDatabase in render.yaml → Postgres)
  - Local dev: set DATABASE_URL=postgresql://... in .env OR leave blank → SQLite file
  - Tests: use SQLite in-memory via create_test_engine()
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base


def _default_db_url() -> str:
    """Return DATABASE_URL env or a local SQLite fallback."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        # Render Postgres uses postgres:// scheme; SQLAlchemy needs postgresql://
        if url.startswith("postgres://"):
            url = "postgresql" + url[len("postgres"):]
        return url
    # Dev/test fallback: SQLite file in backend/
    db_path = Path(__file__).parent.parent.parent.parent.parent / "pred_agent.db"
    return f"sqlite:///{db_path}"


def create_session_manager(database_url: str | None = None) -> tuple[Engine, sessionmaker[Session]]:
    """Create an engine and session factory.

    Uses Alembic for schema management in production; create_all for test
    convenience when ``create_all_tables=True`` is passed.
    """
    url = database_url or _default_db_url()
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        # SQLite needs connect_args for thread safety
        kwargs["connect_args"] = {"check_same_thread": False}

    engine = create_engine(url, **kwargs)

    # Enable WAL mode for SQLite (better concurrency)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_wal(dbapi_conn: object, _: object) -> None:
            if hasattr(dbapi_conn, "execute"):
                dbapi_conn.execute("PRAGMA journal_mode=WAL")  # noqa: PGH003 — hasattr-guarded

    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, session_factory


def create_all_tables(engine: Engine) -> None:
    """Create all tables. Used in tests and for dev SQLite. Production uses Alembic."""
    Base.metadata.create_all(engine)


def new_session(session_factory: sessionmaker[Session]) -> Session:
    return session_factory()


def create_test_engine() -> Engine:
    """In-memory SQLite engine with all tables created. For tests."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_all_tables(engine)
    return engine
