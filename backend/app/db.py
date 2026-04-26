from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_PATH = BASE_DIR / "comic_library.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DATABASE_PATH}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_runtime_schema() -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return

    column_migrations = {
        "reading_paths": {
            "event_id": "INTEGER",
            "source_name": "VARCHAR(255)",
            "source_url": "TEXT",
        },
        "reading_path_entries": {
            "canonical_series_id": "INTEGER",
            "canonical_issue_id": "INTEGER",
            "story_arc_id": "INTEGER",
            "importance": "VARCHAR(32) NOT NULL DEFAULT 'main'",
        },
        "issues": {
            "issue_kind": "VARCHAR(32) NOT NULL DEFAULT 'issue'",
        },
        "canonical_issues": {
            "issue_kind": "VARCHAR(32) NOT NULL DEFAULT 'issue'",
            "provider_name": "VARCHAR(64)",
            "provider_issue_id": "VARCHAR(255)",
            "provider_url": "TEXT",
            "cover_url": "TEXT",
            "page_count": "INTEGER",
        },
        "series": {
            "canonical_series_id": "INTEGER",
            "canonical_match_strategy": "VARCHAR(64)",
            "canonical_match_confidence": "INTEGER",
        },
        "canonical_series": {
            "provider_name": "VARCHAR(64)",
            "provider_series_id": "VARCHAR(255)",
            "provider_url": "TEXT",
            "cover_url": "TEXT",
        },
    }

    with engine.begin() as connection:
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())
        for table_name, columns in column_migrations.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_definition in columns.items():
                if column_name in existing_columns:
                    continue
                connection.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")
                )
