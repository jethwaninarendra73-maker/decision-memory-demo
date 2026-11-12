"""Load the synthetic fixtures into SQLite, and reset the demo database to that baseline.

Seeding is idempotent: fixture records are upserted by ID with a fixed timestamp, so
running it twice leaves the same rows. Records created in the app are left alone by
`seed_database`; `reset_database` deletes the file and seeds from scratch.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .db import is_decision_memory_db
from .models import (
    AuditAction,
    AuditEvent,
    Decision,
    DecisionStatus,
    Entity,
    EntityKind,
    Outcome,
    Relation,
    RelationType,
    SourceDocument,
    SourceRef,
)
from .repository import Repository

logger = logging.getLogger(__name__)

SEED_TIMESTAMP = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
SYNTHETIC_MARKER = "[SYNTHETIC DEMO DATA: fictional company, people and numbers]"


class FixtureError(ValueError):
    """A fixture file is missing, malformed or inconsistent."""


class ResetRefusedError(RuntimeError):
    """The reset target is not a database created by this app, so it was not deleted."""


@dataclass
class SeedData:
    """Validated fixture records, in the order they are written."""

    sources: list[SourceDocument] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    audit_events: list[AuditEvent] = field(default_factory=list)

    def records(self) -> list[Any]:
        return [
            *self.sources,
            *self.entities,
            *self.decisions,
            *self.outcomes,
            *self.relations,
            *self.audit_events,
        ]


def _read_json(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FixtureError(f"Missing fixture file: {path}") from None
    except json.JSONDecodeError as exc:
        raise FixtureError(f"{path.name} is not valid JSON: {exc}") from None
    if not isinstance(data, list):
        raise FixtureError(f"{path.name} must contain a JSON list")
    return data


def _stamps() -> dict[str, Any]:
    return {"created_at": SEED_TIMESTAMP, "updated_at": SEED_TIMESTAMP, "synthetic": True}


def _load_sources(fixtures_dir: Path) -> list[SourceDocument]:
    sources = []
    for raw in _read_json(fixtures_dir / "sources.json"):
        body_path = fixtures_dir / "sources" / raw["file"]
        try:
            body = body_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise FixtureError(f"{raw['id']}: missing source body {body_path}") from None
        if not body.startswith(SYNTHETIC_MARKER):
            raise FixtureError(f"{raw['id']}: body must start with the synthetic marker line")
        sources.append(
            SourceDocument(
                id=raw["id"],
                source_type=raw["source_type"],
                title=raw["title"],
                document_date=raw["document_date"],
                author_label=raw.get("author", ""),
                body=body,
                access_roles=raw["access_roles"],
                # A label, not a real link: nothing in this app connects to Teams or Jira.
                mock_link=f"mock://{raw['source_type']}/{raw['id']}",
                region=raw.get("region"),
                **_stamps(),
            )
        )
    return sources


def _refs(
    owner_id: str, evidence: list[dict[str, str]], sources: dict[str, SourceDocument]
) -> list[SourceRef]:
    refs = []
    for item in evidence:
        source = sources.get(item["source_id"])
        if source is None:
            raise FixtureError(f"{owner_id} cites unknown source {item['source_id']}")
        try:
            refs.append(source.excerpt_ref(item["excerpt"]))
        except ValueError as exc:
            raise FixtureError(f"{owner_id}: {exc}") from None
    return refs


def load_fixtures(fixtures_dir: Path | str) -> SeedData:
    """Read and validate every fixture file. Raises `FixtureError` on any inconsistency."""
    fixtures_dir = Path(fixtures_dir)
    data = SeedData()

    data.sources = _load_sources(fixtures_dir)
    sources = {source.id: source for source in data.sources}

    data.entities = [
        Entity(**raw, **_stamps()) for raw in _read_json(fixtures_dir / "entities.json")
    ]
    entities = {entity.id: entity for entity in data.entities}

    def entity(owner_id: str, entity_id: str, kind: EntityKind) -> Entity:
        found = entities.get(entity_id)
        if found is None or found.kind is not kind:
            raise FixtureError(f"{owner_id} refers to {entity_id}, which is not a known {kind}")
        return found

    links: list[tuple[str, str, RelationType, str]] = []
    for raw in _read_json(fixtures_dir / "decisions.json"):
        dec_id = raw["id"]
        owner = entity(dec_id, raw["owner_id"], EntityKind.STAKEHOLDER)
        stakeholders = [
            entity(dec_id, i, EntityKind.STAKEHOLDER) for i in raw.get("stakeholder_ids", [])
        ]
        market = entity(dec_id, raw["market_id"], EntityKind.MARKET)
        segment = entity(dec_id, raw["segment_id"], EntityKind.SEGMENT)
        systems = [entity(dec_id, i, EntityKind.SYSTEM) for i in raw.get("system_ids", [])]
        groups = [
            entity(dec_id, i, EntityKind.CUSTOMER_GROUP)
            for i in raw.get("customer_group_ids", [])
        ]
        risks = [entity(dec_id, i, EntityKind.RISK) for i in raw.get("risk_ids", [])]

        data.decisions.append(
            Decision(
                id=dec_id,
                title=raw["title"],
                statement=raw["statement"],
                decision_date=raw["decision_date"],
                owner=owner.name,
                stakeholders=[s.name for s in stakeholders],
                region=raw["region"],
                market=market.name,
                customer_segment=segment.name,
                surcharge_rate=raw.get("surcharge_rate"),
                rationale=raw.get("rationale", ""),
                alternatives=raw.get("alternatives", []),
                risks=[risk.name for risk in risks],
                expected_outcome=raw.get("expected_outcome", ""),
                status=DecisionStatus.CONFIRMED,
                source_refs=_refs(dec_id, raw["evidence"], sources),
                **_stamps(),
            )
        )

        links.append((dec_id, owner.id, RelationType.OWNED_BY, ""))
        links += [(dec_id, s.id, RelationType.INVOLVES, "") for s in stakeholders]
        links.append((dec_id, market.id, RelationType.APPLIES_TO, ""))
        links.append((dec_id, segment.id, RelationType.APPLIES_TO, ""))
        links += [(dec_id, s.id, RelationType.AFFECTS, "") for s in systems]
        links += [(dec_id, g.id, RelationType.AFFECTS, "") for g in groups]
        links += [(dec_id, r.id, RelationType.HAS_RISK, "") for r in risks]
        if raw.get("initiative_id"):
            initiative = entity(dec_id, raw["initiative_id"], EntityKind.INITIATIVE)
            links.append((dec_id, initiative.id, RelationType.PART_OF, ""))

    decision_ids = {decision.id for decision in data.decisions}
    for raw in _read_json(fixtures_dir / "relations.json"):
        for endpoint in (raw["from_id"], raw["to_id"]):
            if endpoint not in decision_ids:
                raise FixtureError(f"relations.json refers to unknown decision {endpoint}")
        links.append(
            (raw["from_id"], raw["to_id"], RelationType(raw["relation_type"]), raw.get("note", ""))
        )

    data.relations = [
        Relation(
            id=f"REL-{number:03d}",
            from_id=from_id,
            to_id=to_id,
            relation_type=relation_type,
            note=note,
            **_stamps(),
        )
        for number, (from_id, to_id, relation_type, note) in enumerate(links, start=1)
    ]

    for raw in _read_json(fixtures_dir / "outcomes.json"):
        if raw["decision_id"] not in decision_ids:
            raise FixtureError(f"{raw['id']} refers to unknown decision {raw['decision_id']}")
        evidence = raw.pop("evidence")
        data.outcomes.append(
            Outcome(**raw, source_refs=_refs(raw["id"], evidence, sources), **_stamps())
        )

    data.audit_events = [
        AuditEvent(
            id="AUD-001",
            at=SEED_TIMESTAMP,
            actor_label="seed script",
            role=None,
            action=AuditAction.SEED,
            entity_type="database",
            entity_id="baseline",
            detail=(
                f"Loaded synthetic baseline: {len(data.sources)} sources, "
                f"{len(data.decisions)} decisions, {len(data.outcomes)} outcomes."
            ),
            **_stamps(),
        )
    ]
    return data


def seed(repo: Repository, data: SeedData) -> None:
    """Upsert every fixture record in one transaction."""
    with repo.transaction():
        for record in data.records():
            repo.save(record)


def seed_database(db_path: Path | str, fixtures_dir: Path | str) -> dict[str, int]:
    """Create the database if needed, load the fixtures, and return row counts."""
    data = load_fixtures(fixtures_dir)
    with Repository.open(db_path) as repo:
        seed(repo, data)
        counts = repo.counts()
    logger.info("Seeded %s: %s", db_path, counts)
    return counts


def reset_database(db_path: Path | str, fixtures_dir: Path | str) -> dict[str, int]:
    """Delete the demo database and rebuild it from the fixtures."""
    path = Path(db_path)
    if not is_decision_memory_db(path):
        raise ResetRefusedError(
            f"{path} was not created by this app, so it was not deleted. "
            "Point DM_DB_PATH or --db at the demo database."
        )
    # Validate the fixtures before deleting anything.
    load_fixtures(fixtures_dir)
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    return seed_database(path, fixtures_dir)


def format_counts(counts: dict[str, int]) -> str:
    """Aligned `table  count` lines for the command-line scripts."""
    width = max(len(name) for name in counts)
    return "\n".join(f"  {name:<{width}}  {count:>4}" for name, count in counts.items())
