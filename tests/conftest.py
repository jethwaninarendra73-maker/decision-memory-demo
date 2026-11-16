"""Shared test fixtures. Every test uses a temporary database, never data/demo.db.

Three guards cover the whole suite (Stage 13):
- the network is blocked: any connection to a host other than this machine fails;
- the app runs offline with no API key, even if a developer's `.env` holds one;
- `data/demo.db` must be byte-identical before and after the run.
"""

from __future__ import annotations

import hashlib
import os
import socket
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
DEMO_DB = REPO_ROOT / "data" / "demo.db"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}
# Real environment variables beat `.env`, and an empty key counts as no key, so these
# keep the app offline in tests whatever a local `.env` file says.
OFFLINE_ENV = {"LLM_MODE": "offline", "ANTHROPIC_API_KEY": "", "DM_LLM_MODEL": ""}


class NetworkBlockedError(RuntimeError):
    """A test tried to reach the network. The suite must run offline."""


def _host(address: object) -> str:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address)


@pytest.fixture(autouse=True, scope="session")
def _offline_suite() -> Iterator[None]:
    """Block every connection and name lookup that leaves this machine."""
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def check(sock: socket.socket, address: object) -> None:
        local = sock.family == getattr(socket, "AF_UNIX", None) or _host(address) in LOCAL_HOSTS
        if not local:
            raise NetworkBlockedError(f"Tests run offline; refused to reach {_host(address)}.")

    def connect(sock, address):
        check(sock, address)
        return real_connect(sock, address)

    def connect_ex(sock, address):
        check(sock, address)
        return real_connect_ex(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and str(host) not in LOCAL_HOSTS:
            raise NetworkBlockedError(f"Tests run offline; refused to look up {host}.")
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    socket.getaddrinfo = getaddrinfo
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex = real_connect, real_connect_ex
        socket.getaddrinfo = real_getaddrinfo


@pytest.fixture(autouse=True, scope="session")
def _no_key_and_demo_db_untouched() -> Iterator[None]:
    """No API key for the whole run, and the real demo database left exactly as it was."""
    saved = {name: os.environ.get(name) for name in OFFLINE_ENV}
    os.environ.update(OFFLINE_ENV)

    def fingerprint() -> str | None:
        return hashlib.sha256(DEMO_DB.read_bytes()).hexdigest() if DEMO_DB.exists() else None

    before = fingerprint()
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    assert fingerprint() == before, "A test changed data/demo.db; use a temporary database."


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

        monkeypatch.delenv("DM_LOG_LEVEL", raising=False)
        for name, value in OFFLINE_ENV.items():  # beats any value in a local .env
            monkeypatch.setenv(name, value)
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
