"""Database session and engine helpers."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def create_session_manager(database_url: str) -> tuple[Engine, sessionmaker[Session]]:
    """Create an engine and session factory. Creates all tables on first call."""
    engine = create_engine(database_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, session_factory


def new_session(session_factory: sessionmaker[Session]) -> Session:
    return session_factory()
