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
APP_FILE = REPO_ROOT / "app.py"

# Strings that exist only in records hidden from the role named in the constant.
# SRC-019 / DEC-015 are restricted_exec only; SRC-021 / DEC-016 are also hidden from analyst.
HIDDEN_FROM_DIRECTOR = (
    "SRC-019",
    "DEC-015",
    "Board strategy",
    "Board Strategy Committee",
    "twenty largest",
    "strategic accounts",
    "ENT-CGR-DE-STRAT",
    "ENT-STK-BOARD",
    "accounts lost in 2023",
)
HIDDEN_FROM_ANALYST = (
    *HIDDEN_FROM_DIRECTOR,
    "SRC-021",
    "DEC-016",
    "France wave 2",
    "two-region pilot",
    "0.9% and 1.4%",
    "to a new segment",
)
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
        **STAMPS,
    }
    if "source_refs" not in overrides:
        fields["source_refs"] = [source.excerpt_ref("Apply a 2% test surcharge in Testland.")]
    return Decision(**{**fields, **overrides})


def rendered_text(app) -> str:
    """Every piece of text a Streamlit test run put on screen, sidebar included."""
    parts: list[str] = []
    for kind in (
        "title", "header", "subheader", "markdown", "caption", "text",
        "info", "warning", "error", "success",
    ):  # fmt: skip
        parts += [str(element.value) for element in getattr(app, kind)]
    for box in app.selectbox:
        parts += [str(option) for option in box.options]
        parts.append(str(box.value))
    for frame in app.dataframe:
        parts.append(frame.value.to_string())
    return "\n".join(parts)


@pytest.fixture
def run_app(monkeypatch: pytest.MonkeyPatch, seeded_db: Path):
    """Run app.py headlessly against a temporary database, with no API key."""

    def _run(*, role: str | None = None, evidence: str | None = None, db: Path | None = None):
        from streamlit.testing.v1 import AppTest

        for name in ("ANTHROPIC_API_KEY", "LLM_MODE", "DM_LOG_LEVEL"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("DM_DB_PATH", str(db or seeded_db))
        app = AppTest.from_file(str(APP_FILE), default_timeout=30)
        if role is not None:
            app.query_params["role"] = role
        if evidence is not None:
            app.query_params["evidence"] = evidence
        return app.run()

    return _run


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
