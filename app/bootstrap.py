"""Composition root for enterprise Agent infrastructure dependencies."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from app.agent.llm import LlmSettings
from app.infrastructure.persistence.database import Database, create_database
from app.infrastructure.persistence.repositories import (
    SqlAlchemyAnalysisJobRepository,
    SqlAlchemyAnalysisBatchRepository,
    SqlAlchemyAssetRepository,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_URL = f"sqlite:///{(PROJECT_ROOT / 'data' / 'agent.sqlite3').as_posix()}"


def resolve_database_url(database_url: str) -> str:
    """Resolve relative SQLite files against the project, not the shell directory."""
    sqlite_prefix = "sqlite:///"
    if not database_url.startswith(sqlite_prefix):
        return database_url
    database_path = database_url.removeprefix(sqlite_prefix)
    path = Path(database_path)
    if path.is_absolute() or (len(database_path) > 1 and database_path[1] == ":"):
        return database_url
    return f"{sqlite_prefix}{(PROJECT_ROOT / path).resolve().as_posix()}"


@dataclass(frozen=True)
class EnterpriseDependencies:
    """Infrastructure dependencies owned by the application composition root."""

    database: Database
    assets: SqlAlchemyAssetRepository
    analysis_jobs: SqlAlchemyAnalysisJobRepository
    analysis_batches: SqlAlchemyAnalysisBatchRepository
    llm_settings: LlmSettings


def build_enterprise_dependencies(database_url: str | None = None) -> EnterpriseDependencies:
    """Build replaceable infrastructure adapters from environment configuration."""
    configured_url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    database = create_database(resolve_database_url(configured_url))
    return EnterpriseDependencies(
        database=database,
        assets=SqlAlchemyAssetRepository(database.session_factory),
        analysis_jobs=SqlAlchemyAnalysisJobRepository(database.session_factory),
        analysis_batches=SqlAlchemyAnalysisBatchRepository(database.session_factory),
        llm_settings=LlmSettings.from_environment(),
    )
