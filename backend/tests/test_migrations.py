"""Migrations must build the same schema the models declare.

The CLI runs in a subprocess because ``app.core.config`` caches settings at
import time; a subprocess gets a clean ``DATABASE_URL`` for the temporary file.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app.db.session import Base
from sqlalchemy import create_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _run_alembic(database: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database}"
    env.pop("APP_ENV", None)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _columns(database: Path) -> dict[str, set[str]]:
    connection = sqlite3.connect(database)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table' "
                "and name not like 'sqlite_%' and name != 'alembic_version'"
            )
        ]
        return {
            table: {row[1] for row in connection.execute(f"pragma table_info({table})")}
            for table in tables
        }
    finally:
        connection.close()


def test_upgrade_head_and_downgrade_base(tmp_path: Path) -> None:
    database = tmp_path / "migrations.db"
    _run_alembic(database, "upgrade", "head")

    connection = sqlite3.connect(database)
    try:
        version = connection.execute("select version_num from alembic_version").fetchone()
        assert version is not None
        allocation_columns = {
            row[1] for row in connection.execute("pragma table_info(topic_allocations)")
        }
        assert {"planned_minutes", "actual_minutes"} <= allocation_columns
        assert "allocated_duration_minutes" not in allocation_columns
        tables = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        assert {"user_settings", "topic_suggestions", "email_deliveries"} <= tables
    finally:
        connection.close()

    _run_alembic(database, "downgrade", "base")
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        assert tables == {"alembic_version"}
    finally:
        connection.close()


def test_migration_schema_matches_models(tmp_path: Path) -> None:
    migrated = tmp_path / "migrated.db"
    _run_alembic(migrated, "upgrade", "head")
    migrated_columns = _columns(migrated)

    declared = tmp_path / "declared.db"
    engine = create_engine(f"sqlite:///{declared}")
    try:
        Base.metadata.create_all(bind=engine)
    finally:
        engine.dispose()
    declared_columns = _columns(declared)

    assert declared_columns.keys() == migrated_columns.keys()
    for table, columns in declared_columns.items():
        assert columns == migrated_columns[table], f"column drift in {table}"
