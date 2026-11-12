"""Shared test fixtures. Every test uses a temporary database, never data/demo.db."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from decision_memory.config import REPO_ROOT
from decision_memory.models import (
    Decision,
    DecisionStatus,
    Role,
    SourceDocument,
    SourceType,
)
from decision_memory.repository import Repository
from decision_memory.seed import seed_database

FIXTURES_DIR = REPO_ROOT / "data" / "fixtures"
NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
STAMPS = {"created_at": NOW, "updated_at": NOW}


def dump_database(db_path: Path) -> dict[str, list[tuple]]:
    """Every row of every table, for byte-for-byte comparisons."""
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            name
            for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        return {
            table: sorted(map(tuple, conn.execute(f"SELECT * FROM {table}")), key=repr)
            for table in tables
        }
    finally:
        conn.close()


def make_source(source_id: str = "SRC-900", **overrides) -> SourceDocument:
    fields = {
        "id": source_id,
        "source_type": SourceType.TEAMS_TRANSCRIPT,
        "title": "Test pricing call",
        "document_date": date(2026, 1, 15),
        "body": "Intro line.\nDECISION: Apply a 2% test surcharge in Testland.\nClosing line.",
        "access_roles": [Role.PRICING_DIRECTOR, Role.ANALYST],
        **STAMPS,
    }
    return SourceDocument(**{**fields, **overrides})


def make_decision(source: SourceDocument, decision_id: str = "DEC-900", **overrides) -> Decision:
    fields = {
        "id": decision_id,
        "title": "Testland: 2% surcharge",
        "statement": "Apply a 2% test surcharge in Testland.",
        "decision_date": date(2026, 1, 15),
        "owner": "Test Owner",
        "stakeholders": ["Test Analyst"],
        "region": "Europe",
        "market": "Testland",
        "customer_segment": "All customers",
        "surcharge_rate": 0.02,
        "risks": ["Test risk"],
        "status": DecisionStatus.CONFIRMED,
        "source_refs": [source.excerpt_ref("Apply a 2% test surcharge in Testland.")],
        **STAMPS,
    }
    return Decision(**{**fields, **overrides})


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def repo(db_path: Path) -> Iterator[Repository]:
    """An empty, bootstrapped database."""
    with Repository.open(db_path) as repository:
        yield repository


@pytest.fixture
def seeded_db(db_path: Path) -> Path:
    """Path to a database holding exactly the synthetic baseline."""
    seed_database(db_path, FIXTURES_DIR)
    return db_path


@pytest.fixture
def seeded(seeded_db: Path) -> Iterator[Repository]:
    with Repository.open(seeded_db) as repository:
        yield repository
