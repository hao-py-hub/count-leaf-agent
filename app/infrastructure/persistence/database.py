"""SQLAlchemy engine and schema setup for enterprise adapters."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.persistence.models import Base


@dataclass(frozen=True)
class Database:
    """Owns the SQLAlchemy engine and request-safe session factory."""

    engine: Engine
    session_factory: sessionmaker[Session]

    def sessions(self) -> Iterator[Session]:
        """Yield a transactional session for repository operations."""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def create_database(database_url: str) -> Database:
    """Create the database connection and ensure v0.1 tables exist.

    Alembic migrations will replace ``create_all`` before production rollout.
    """
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, future=True, pool_pre_ping=True, connect_args=connect_args)
    Base.metadata.create_all(engine)
    asset_columns = {column["name"] for column in inspect(engine).get_columns("agent_assets")}
    if "project_id" not in asset_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE agent_assets ADD COLUMN project_id VARCHAR(128) NOT NULL DEFAULT 'default'"))
    columns = {column["name"] for column in inspect(engine).get_columns("analysis_jobs")}
    if "cache_key" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE analysis_jobs ADD COLUMN cache_key VARCHAR(128)"))
    if "trace_id" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE analysis_jobs ADD COLUMN trace_id VARCHAR(36)"))
    if "retry_of" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE analysis_jobs ADD COLUMN retry_of VARCHAR(36)"))
    if "project_id" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE analysis_jobs ADD COLUMN project_id VARCHAR(128) NOT NULL DEFAULT 'default'"))
    batch_columns = {column["name"] for column in inspect(engine).get_columns("analysis_batches")}
    if "trace_id" not in batch_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE analysis_batches ADD COLUMN trace_id VARCHAR(36)"))
    if "project_id" not in batch_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE analysis_batches ADD COLUMN project_id VARCHAR(128) NOT NULL DEFAULT 'default'"))
    return Database(engine=engine, session_factory=sessionmaker(engine, expire_on_commit=False))
