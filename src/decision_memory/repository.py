"""Small repository layer: the only module that runs SQL against the demo database.

Records go in and come out as validated Pydantic models. Referential integrity is
enforced twice: by SQLite foreign keys, and here for things SQL cannot express
(relation endpoints of mixed types, and excerpts that must match the source body).

Stage 03 adds the permission filter on top of this layer.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from .db import open_database
from .models import (
    Approval,
    AuditEvent,
    Decision,
    Entity,
    KPIUpdate,
    Milestone,
    Outcome,
    Proposal,
    Recommendation,
    Record,
    Relation,
    ScenarioResult,
    SourceDocument,
    SourceRef,
)

R = TypeVar("R", bound=Record)


class RepositoryError(Exception):
    """Base class for repository failures."""


class DuplicateRecordError(RepositoryError):
    """`add` was called with an ID that already exists."""


class IntegrityViolationError(RepositoryError):
    """A write would leave a dangling reference or an excerpt that is not in its source."""


@dataclass(frozen=True)
class _Table:
    name: str
    json_fields: tuple[str, ...] = ()
    child_fields: tuple[str, ...] = ()


_TABLES: dict[type[Record], _Table] = {
    SourceDocument: _Table("source_documents", child_fields=("access_roles",)),
    Decision: _Table(
        "decisions",
        json_fields=("stakeholders", "alternatives", "risks"),
        child_fields=("source_refs",),
    ),
    Entity: _Table("entities"),
    Relation: _Table("relations"),
    Outcome: _Table("outcomes", child_fields=("source_refs",)),
    Proposal: _Table(
        "proposals", json_fields=("target_segments", "surcharge_options", "exclusions")
    ),
    ScenarioResult: _Table("scenario_results", json_fields=("assumptions",)),
    Recommendation: _Table(
        "recommendations",
        json_fields=("reasons", "excluded_options", "assumptions", "action_plan"),
    ),
    Approval: _Table("approvals"),
    Milestone: _Table("milestones"),
    KPIUpdate: _Table("kpi_updates"),
    AuditEvent: _Table("audit_events"),
}

_MODEL_BY_PREFIX: dict[str, type[Record]] = {model.ID_PREFIX: model for model in _TABLES}

# Link tables that hold source refs: model -> (table, owner column).
_SOURCE_REF_TABLES: dict[type[Record], tuple[str, str]] = {
    Decision: ("decision_source_refs", "decision_id"),
    Outcome: ("outcome_source_refs", "outcome_id"),
}


def _table_for(model: type[Record]) -> _Table:
    try:
        return _TABLES[model]
    except KeyError:
        raise RepositoryError(f"{model.__name__} is not a stored record type") from None


def stored_models() -> tuple[type[Record], ...]:
    """Every record type the repository can store, in dependency order."""
    return tuple(_TABLES)


def table_name(model: type[Record]) -> str:
    return _table_for(model).name


def column_names(model: type[Record]) -> list[str]:
    """Columns of the model's main table (model fields minus link-table fields)."""
    table = _table_for(model)
    return [name for name in model.model_fields if name not in table.child_fields]


def _sql_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, datetime):
        raise TypeError("Filtering on timestamps is not supported")
    if isinstance(value, date):
        return value.isoformat()
    return value


class Repository:
    """Typed create/read/update access to the demo database."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._depth = 0

    @classmethod
    def open(cls, db_path: Path | str) -> Repository:
        """Open the database file, creating the file and schema if needed."""
        return cls(open_database(db_path))

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Repository:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- transactions ---------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """All writes inside commit together or not at all. Nesting joins the outer one."""
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self._conn.execute("BEGIN")
        self._depth = 1
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")
        finally:
            self._depth = 0

    # --- writes ---------------------------------------------------------

    def add(self, record: R) -> R:
        """Insert a new record. Raises `DuplicateRecordError` if the ID exists."""
        return self._write(record, upsert=False)

    def save(self, record: R) -> R:
        """Insert the record, or update it if the ID exists (`created_at` is kept)."""
        return self._write(record, upsert=True)

    def _write(self, record: R, *, upsert: bool) -> R:
        model = type(record)
        table = _table_for(model)
        data = record.model_dump(mode="json")
        for name in table.child_fields:
            data.pop(name)
        for name in table.json_fields:
            data[name] = json.dumps(data[name], ensure_ascii=False)
        columns = list(data)
        sql = (
            f"INSERT INTO {table.name} ({', '.join(columns)}) "
            f"VALUES ({', '.join(':' + column for column in columns)})"
        )
        if upsert:
            updates = ", ".join(
                f"{column} = excluded.{column}"
                for column in columns
                if column not in ("id", "created_at")
            )
            sql += f" ON CONFLICT(id) DO UPDATE SET {updates}"

        with self.transaction():
            if not upsert and self._row(table, record.id) is not None:
                raise DuplicateRecordError(f"{record.id} already exists")
            self._check_references(record)
            try:
                self._conn.execute(sql, data)
                self._write_children(record)
            except sqlite3.IntegrityError as exc:
                raise IntegrityViolationError(f"Cannot store {record.id}: {exc}") from exc
        return record

    def _check_references(self, record: Record) -> None:
        if isinstance(record, Decision | Outcome):
            self._check_source_refs(record.id, record.source_refs)
        elif isinstance(record, Recommendation):
            for reason in record.reasons:
                self._check_source_refs(record.id, reason.source_refs)
        elif isinstance(record, Relation):
            for endpoint in (record.from_id, record.to_id):
                if not self.exists(endpoint):
                    raise IntegrityViolationError(
                        f"{record.id} points at {endpoint}, which does not exist"
                    )

    def _check_source_refs(self, owner_id: str, refs: Sequence[SourceRef]) -> None:
        for ref in refs:
            row = self._conn.execute(
                "SELECT body FROM source_documents WHERE id = ?", (ref.source_id,)
            ).fetchone()
            if row is None:
                raise IntegrityViolationError(
                    f"{owner_id} cites {ref.source_id}, which does not exist"
                )
            if row["body"][ref.start_offset : ref.end_offset] != ref.excerpt:
                raise IntegrityViolationError(
                    f"{owner_id} cites an excerpt that is not at offsets "
                    f"{ref.start_offset}:{ref.end_offset} of {ref.source_id}"
                )

    def _write_children(self, record: Record) -> None:
        if isinstance(record, SourceDocument):
            self._conn.execute(
                "DELETE FROM source_access_roles WHERE source_id = ?", (record.id,)
            )
            self._conn.executemany(
                "INSERT INTO source_access_roles (source_id, role) VALUES (?, ?)",
                [(record.id, role.value) for role in record.access_roles],
            )
        elif isinstance(record, Decision | Outcome):
            link_table, owner_column = _SOURCE_REF_TABLES[type(record)]
            self._conn.execute(f"DELETE FROM {link_table} WHERE {owner_column} = ?", (record.id,))
            self._conn.executemany(
                f"INSERT INTO {link_table} "
                f"({owner_column}, position, source_id, excerpt, start_offset, end_offset) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (record.id, position, ref.source_id, ref.excerpt, ref.start_offset, ref.end_offset)
                    for position, ref in enumerate(record.source_refs)
                ],
            )

    # --- reads ----------------------------------------------------------

    def get(self, model: type[R], record_id: str) -> R | None:
        """Return the record with this ID, or None."""
        row = self._row(_table_for(model), record_id)
        return None if row is None else self._to_model(model, row)

    def find(self, model: type[R], **equals: Any) -> list[R]:
        """Return records whose columns equal the given values, ordered by ID."""
        table = _table_for(model)
        filterable = set(column_names(model)) - set(table.json_fields)
        unknown = set(equals) - filterable
        if unknown:
            raise ValueError(f"Cannot filter {model.__name__} by {sorted(unknown)}")
        sql = f"SELECT * FROM {table.name}"
        if equals:
            sql += " WHERE " + " AND ".join(f"{column} = ?" for column in equals)
        sql += " ORDER BY id"
        rows = self._conn.execute(sql, [_sql_value(value) for value in equals.values()])
        return [self._to_model(model, row) for row in rows.fetchall()]

    def count(self, model: type[Record]) -> int:
        table = _table_for(model)
        return self._conn.execute(f"SELECT COUNT(*) FROM {table.name}").fetchone()[0]

    def counts(self) -> dict[str, int]:
        """Row count per main table, in a stable order."""
        return {table.name: self.count(model) for model, table in _TABLES.items()}

    def exists(self, record_id: str) -> bool:
        """True if any stored record has this ID (the prefix selects the table)."""
        model = _MODEL_BY_PREFIX.get(record_id.split("-", 1)[0])
        return model is not None and self._row(_table_for(model), record_id) is not None

    def next_id(self, model: type[Record]) -> str:
        """Next free numeric ID for the model, e.g. `DEC-017`."""
        table = _table_for(model)
        pattern = re.compile(rf"{model.ID_PREFIX}-(\d+)")
        numbers = [
            int(match.group(1))
            for (record_id,) in self._conn.execute(f"SELECT id FROM {table.name}")
            if (match := pattern.fullmatch(record_id))
        ]
        return f"{model.ID_PREFIX}-{max(numbers, default=0) + 1:03d}"

    def dangling_relations(self) -> list[Relation]:
        """Relations whose endpoints no longer exist. Should always be empty."""
        return [
            relation
            for relation in self.find(Relation)
            if not (self.exists(relation.from_id) and self.exists(relation.to_id))
        ]

    def _row(self, table: _Table, record_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            f"SELECT * FROM {table.name} WHERE id = ?", (record_id,)
        ).fetchone()

    def _to_model(self, model: type[R], row: sqlite3.Row) -> R:
        table = _table_for(model)
        data = dict(row)
        for name in table.json_fields:
            data[name] = json.loads(data[name])
        if model is SourceDocument:
            data["access_roles"] = [
                role
                for (role,) in self._conn.execute(
                    "SELECT role FROM source_access_roles WHERE source_id = ? ORDER BY role",
                    (data["id"],),
                )
            ]
        elif model in _SOURCE_REF_TABLES:
            link_table, owner_column = _SOURCE_REF_TABLES[model]
            data["source_refs"] = [
                dict(ref)
                for ref in self._conn.execute(
                    f"SELECT source_id, excerpt, start_offset, end_offset FROM {link_table} "
                    f"WHERE {owner_column} = ? ORDER BY position",
                    (data["id"],),
                )
            ]
        return model.model_validate(data)
