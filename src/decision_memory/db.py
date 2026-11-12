"""SQLite connection and safe create-table bootstrap (standard library only).

The schema is created with `CREATE TABLE IF NOT EXISTS`, so bootstrapping an existing
database is harmless. `PRAGMA user_version` records the schema version and
`PRAGMA application_id` marks the file as ours, which lets the reset script refuse to
delete any other file.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

SCHEMA_VERSION = 1
APPLICATION_ID = 0x444D454D  # ASCII "DMEM"

# Explicit transaction control on every supported Python version.
_CONNECT_KWARGS: dict[str, object] = (
    {"autocommit": True} if sys.version_info >= (3, 12) else {"isolation_level": None}
)

_RECORD_COLUMNS = """
    id TEXT PRIMARY KEY,
    synthetic INTEGER NOT NULL DEFAULT 1 CHECK (synthetic IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL"""

_SOURCE_REF_COLUMNS = """
    position INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_documents(id),
    excerpt TEXT NOT NULL,
    start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK (end_offset > start_offset)"""

SCHEMA: tuple[str, ...] = (
    f"""CREATE TABLE IF NOT EXISTS source_documents ({_RECORD_COLUMNS},
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    document_date TEXT NOT NULL,
    author_label TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    mock_link TEXT NOT NULL DEFAULT '',
    region TEXT
)""",
    """CREATE TABLE IF NOT EXISTS source_access_roles (
    source_id TEXT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    PRIMARY KEY (source_id, role)
)""",
    f"""CREATE TABLE IF NOT EXISTS decisions ({_RECORD_COLUMNS},
    title TEXT NOT NULL,
    statement TEXT NOT NULL,
    decision_date TEXT NOT NULL,
    owner TEXT NOT NULL,
    stakeholders TEXT NOT NULL DEFAULT '[]',
    region TEXT NOT NULL,
    market TEXT NOT NULL,
    customer_segment TEXT NOT NULL,
    surcharge_rate REAL,
    rationale TEXT NOT NULL DEFAULT '',
    alternatives TEXT NOT NULL DEFAULT '[]',
    risks TEXT NOT NULL DEFAULT '[]',
    expected_outcome TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL
)""",
    f"""CREATE TABLE IF NOT EXISTS decision_source_refs (
    decision_id TEXT NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,{_SOURCE_REF_COLUMNS},
    PRIMARY KEY (decision_id, position)
)""",
    f"""CREATE TABLE IF NOT EXISTS entities ({_RECORD_COLUMNS},
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
)""",
    f"""CREATE TABLE IF NOT EXISTS relations ({_RECORD_COLUMNS},
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    UNIQUE (from_id, to_id, relation_type)
)""",
    f"""CREATE TABLE IF NOT EXISTS outcomes ({_RECORD_COLUMNS},
    decision_id TEXT NOT NULL REFERENCES decisions(id),
    verdict TEXT NOT NULL,
    observed_on TEXT NOT NULL,
    expected_revenue_change_musd REAL,
    actual_revenue_change_musd REAL,
    expected_churn_loss_fraction REAL,
    actual_churn_loss_fraction REAL,
    sla_status TEXT NOT NULL,
    explanation TEXT NOT NULL DEFAULT ''
)""",
    f"""CREATE TABLE IF NOT EXISTS outcome_source_refs (
    outcome_id TEXT NOT NULL REFERENCES outcomes(id) ON DELETE CASCADE,{_SOURCE_REF_COLUMNS},
    PRIMARY KEY (outcome_id, position)
)""",
    f"""CREATE TABLE IF NOT EXISTS proposals ({_RECORD_COLUMNS},
    question TEXT NOT NULL,
    region TEXT NOT NULL,
    target_segments TEXT NOT NULL DEFAULT '[]',
    effective_quarter TEXT NOT NULL,
    surcharge_options TEXT NOT NULL,
    exclusions TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    requested_by_role TEXT NOT NULL,
    decision_id TEXT REFERENCES decisions(id)
)""",
    f"""CREATE TABLE IF NOT EXISTS scenario_results ({_RECORD_COLUMNS},
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    surcharge_rate REAL NOT NULL,
    assumptions TEXT NOT NULL,
    gross_uplift_musd REAL NOT NULL,
    churn_loss_musd REAL NOT NULL,
    revenue_change_musd REAL NOT NULL,
    margin_change_musd REAL NOT NULL,
    formula_trace TEXT NOT NULL DEFAULT ''
)""",
    f"""CREATE TABLE IF NOT EXISTS recommendations ({_RECORD_COLUMNS},
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    decision_type TEXT NOT NULL,
    recommended_rate REAL,
    reasons TEXT NOT NULL DEFAULT '[]',
    excluded_options TEXT NOT NULL DEFAULT '[]',
    risk_level TEXT,
    assumptions TEXT NOT NULL DEFAULT '[]',
    uncertainty TEXT NOT NULL DEFAULT '',
    action_plan TEXT NOT NULL DEFAULT '[]',
    approval_required_by TEXT NOT NULL
)""",
    f"""CREATE TABLE IF NOT EXISTS approvals ({_RECORD_COLUMNS},
    recommendation_id TEXT NOT NULL REFERENCES recommendations(id),
    approver_label TEXT NOT NULL,
    role TEXT NOT NULL,
    verdict TEXT NOT NULL,
    chosen_rate REAL,
    reasons TEXT NOT NULL DEFAULT '',
    decided_at TEXT NOT NULL
)""",
    f"""CREATE TABLE IF NOT EXISTS milestones ({_RECORD_COLUMNS},
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    name TEXT NOT NULL,
    due_date TEXT NOT NULL,
    state TEXT NOT NULL
)""",
    f"""CREATE TABLE IF NOT EXISTS kpi_updates ({_RECORD_COLUMNS},
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    week INTEGER NOT NULL CHECK (week BETWEEN 1 AND 8),
    planned_revenue_uplift_musd REAL NOT NULL,
    actual_revenue_uplift_musd REAL,
    planned_churn_loss_fraction REAL NOT NULL,
    actual_churn_loss_fraction REAL,
    sla_status TEXT NOT NULL,
    risk_flag INTEGER NOT NULL DEFAULT 0 CHECK (risk_flag IN (0, 1)),
    note TEXT NOT NULL DEFAULT '',
    UNIQUE (proposal_id, week)
)""",
    f"""CREATE TABLE IF NOT EXISTS audit_events ({_RECORD_COLUMNS},
    at TEXT NOT NULL,
    actor_label TEXT NOT NULL,
    role TEXT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
)""",
    "CREATE INDEX IF NOT EXISTS idx_decision_refs_source ON decision_source_refs(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_outcome_refs_source ON outcome_source_refs(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_outcomes_decision ON outcomes(decision_id)",
    "CREATE INDEX IF NOT EXISTS idx_relations_from ON relations(from_id)",
    "CREATE INDEX IF NOT EXISTS idx_relations_to ON relations(to_id)",
)


class SchemaError(RuntimeError):
    """The database file is not usable by this version of the app."""


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) a SQLite file with foreign keys enforced."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, **_CONNECT_KWARGS)  # type: ignore[arg-type]
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def bootstrap(conn: sqlite3.Connection) -> None:
    """Create any missing tables and stamp the schema version. Safe to repeat."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    app_id = conn.execute("PRAGMA application_id").fetchone()[0]
    if app_id not in (0, APPLICATION_ID):
        raise SchemaError("This SQLite file belongs to a different application.")
    if version > SCHEMA_VERSION:
        raise SchemaError(
            f"Database schema version {version} is newer than this app supports "
            f"({SCHEMA_VERSION}). Run scripts/reset_demo.py or update the code."
        )
    conn.execute("BEGIN")
    try:
        for statement in SCHEMA:
            conn.execute(statement)
        conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def open_database(db_path: Path | str) -> sqlite3.Connection:
    """Connect and bootstrap in one step."""
    conn = connect(db_path)
    try:
        bootstrap(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def is_decision_memory_db(db_path: Path | str) -> bool:
    """True if the path is absent, empty, or a database created by this app."""
    path = Path(db_path)
    if not path.exists() or path.stat().st_size == 0:
        return True
    if not path.is_file():
        return False
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            return conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False
