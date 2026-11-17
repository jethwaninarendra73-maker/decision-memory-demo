"""Synthetic baseline: content rules, idempotent seed, reset, and persistence across processes."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from decision_memory.config import REPO_ROOT
from decision_memory.db import DatabaseUnavailableError
from decision_memory.models import (
    AuditEvent,
    Decision,
    DecisionStatus,
    Entity,
    EntityKind,
    Outcome,
    OutcomeVerdict,
    Relation,
    RelationType,
    Role,
    SourceDocument,
    SourceType,
)
from decision_memory.repository import Repository
from decision_memory.seed import (
    SEED_TIMESTAMP,
    SYNTHETIC_MARKER,
    FixtureError,
    ResetRefusedError,
    load_fixtures,
    reset_database,
    seed_database,
)

from conftest import FIXTURES_DIR, dump_database, make_decision, make_source

EXPECTED_COUNTS = {
    "source_documents": 22,
    "decisions": 16,
    "entities": 47,
    "relations": 147,
    "outcomes": 14,
    "proposals": 0,
    "scenario_results": 0,
    "recommendations": 0,
    "approvals": 0,
    "milestones": 0,
    "kpi_updates": 0,
    "audit_events": 1,
}


def run_script(name: str, db_path: Path) -> subprocess.CompletedProcess[str]:
    """Run a script in a fresh Python process with no API key in the environment."""
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "LLM_MODE")}
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / name), "--db", str(db_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def visible_to(role: Role, repo: Repository) -> set[str]:
    """Decisions whose every cited source lists the role. Plain set logic, not the Stage 03 layer."""
    roles = {source.id: source.access_roles for source in repo.find(SourceDocument)}
    return {
        decision.id
        for decision in repo.find(Decision)
        if all(role in roles[ref.source_id] for ref in decision.source_refs)
    }


# --- baseline content -------------------------------------------------------


def test_seed_counts(seeded: Repository) -> None:
    assert seeded.counts() == EXPECTED_COUNTS


def test_at_least_twelve_decisions_mostly_european(seeded: Repository) -> None:
    decisions = seeded.find(Decision)
    regions = [decision.region for decision in decisions]
    assert len(decisions) >= 12
    assert regions.count("Europe") >= 12
    assert 1 <= len([region for region in regions if region != "Europe"]) <= 2
    assert len({d.market for d in decisions if d.region == "Europe"}) >= 6
    assert all(decision.status is DecisionStatus.CONFIRMED for decision in decisions)


def test_three_evidence_types_are_seeded(seeded: Repository) -> None:
    types = {source.source_type for source in seeded.find(SourceDocument)}
    assert {
        SourceType.TEAMS_TRANSCRIPT,
        SourceType.STEERING_DECK,
        SourceType.JIRA_TICKET,
    } <= types


def test_people_risks_and_systems_are_seeded(seeded: Repository) -> None:
    for kind in (EntityKind.STAKEHOLDER, EntityKind.RISK, EntityKind.SYSTEM, EntityKind.MARKET):
        assert len(seeded.find(Entity, kind=kind)) >= 4


def test_outcomes_mix_success_partial_and_failure(seeded: Repository) -> None:
    verdicts = [outcome.verdict for outcome in seeded.find(Outcome)]
    for verdict in OutcomeVerdict:
        assert verdicts.count(verdict) >= 3
    measured = [o for o in seeded.find(Outcome) if o.actual_revenue_change_musd is not None]
    assert all(o.expected_revenue_change_musd is not None for o in measured)
    assert len(measured) >= 10


def test_every_decision_and_outcome_cites_exact_excerpts(seeded: Repository) -> None:
    bodies = {source.id: source.body for source in seeded.find(SourceDocument)}
    cited = [*seeded.find(Decision), *seeded.find(Outcome)]
    for record in cited:
        assert record.source_refs, f"{record.id} cites nothing"
        for ref in record.source_refs:
            assert bodies[ref.source_id][ref.start_offset : ref.end_offset] == ref.excerpt


def test_everything_is_marked_synthetic(seeded: Repository) -> None:
    for model in (SourceDocument, Decision, Entity, Relation, Outcome, AuditEvent):
        assert all(record.synthetic for record in seeded.find(model))
    for source in seeded.find(SourceDocument):
        assert source.body.startswith(SYNTHETIC_MARKER)
        assert source.mock_link.startswith("mock://")


def test_poor_and_contradictory_precedents_exist(seeded: Repository) -> None:
    assert seeded.find(Relation, relation_type=RelationType.CONTRADICTS)
    assert seeded.find(Relation, relation_type=RelationType.SUPERSEDES)
    europe = {d.id: d for d in seeded.find(Decision, region="Europe")}
    failed_in_europe = [
        o for o in seeded.find(Outcome, verdict=OutcomeVerdict.FAILED) if o.decision_id in europe
    ]
    assert len(failed_in_europe) >= 2
    # Same market, opposite results: Germany failed at 6% and succeeded at 3%.
    germany = {d.id for d in europe.values() if d.market == "Germany"}
    verdicts = {o.verdict for o in seeded.find(Outcome) if o.decision_id in germany}
    assert {OutcomeVerdict.FAILED, OutcomeVerdict.SUCCESS} <= verdicts


def test_no_dangling_relations_and_decision_links(seeded: Repository) -> None:
    assert seeded.dangling_relations() == []
    for decision in seeded.find(Decision):
        types = {r.relation_type for r in seeded.find(Relation, from_id=decision.id)}
        assert {RelationType.OWNED_BY, RelationType.APPLIES_TO} <= types


# --- permission metadata ----------------------------------------------------


def test_every_source_carries_valid_role_tags(seeded: Repository) -> None:
    sources = seeded.find(SourceDocument)
    assert all(source.access_roles for source in sources)
    used = {role for source in sources for role in source.access_roles}
    assert used == set(Role)
    assert all(Role.RESTRICTED_EXEC in source.access_roles for source in sources)


def test_restricted_sources_are_tagged_as_designed(seeded: Repository) -> None:
    assert seeded.get(SourceDocument, "SRC-019").access_roles == [Role.RESTRICTED_EXEC]
    assert seeded.get(SourceDocument, "SRC-021").access_roles == [
        Role.PRICING_DIRECTOR,
        Role.RESTRICTED_EXEC,
    ]


def test_role_metadata_gives_each_role_enough_precedents(seeded: Repository) -> None:
    everything = {decision.id for decision in seeded.find(Decision)}
    assert visible_to(Role.RESTRICTED_EXEC, seeded) == everything
    assert visible_to(Role.PRICING_DIRECTOR, seeded) == everything - {"DEC-015"}
    assert visible_to(Role.ANALYST, seeded) == everything - {"DEC-015", "DEC-016"}
    assert len(visible_to(Role.ANALYST, seeded)) >= 12


# --- idempotent seed and reset ----------------------------------------------


def test_seed_twice_changes_nothing(seeded_db: Path) -> None:
    before = dump_database(seeded_db)
    assert seed_database(seeded_db, FIXTURES_DIR) == EXPECTED_COUNTS
    assert dump_database(seeded_db) == before


def test_two_fresh_seeds_are_identical(seeded_db: Path, tmp_path: Path) -> None:
    other = tmp_path / "other.db"
    seed_database(other, FIXTURES_DIR)
    assert dump_database(other) == dump_database(seeded_db)


def test_seed_timestamps_are_fixed(seeded: Repository) -> None:
    assert {d.created_at for d in seeded.find(Decision)} == {SEED_TIMESTAMP}


def test_seed_restores_edited_fixture_rows_and_keeps_user_rows(seeded_db: Path) -> None:
    baseline = dump_database(seeded_db)
    with Repository.open(seeded_db) as repo:
        source = repo.add(make_source())
        repo.add(make_decision(source))
        edited = repo.get(Decision, "DEC-004").model_copy(update={"title": "Tampered"})
        repo.save(edited)
    seed_database(seeded_db, FIXTURES_DIR)
    with Repository.open(seeded_db) as repo:
        assert repo.get(Decision, "DEC-004").title != "Tampered"
        assert repo.get(Decision, "DEC-900") is not None
        assert repo.count(Decision) == EXPECTED_COUNTS["decisions"] + 1
    assert dump_database(seeded_db) != baseline


def test_reset_removes_user_rows_and_restores_the_baseline(seeded_db: Path) -> None:
    baseline = dump_database(seeded_db)
    with Repository.open(seeded_db) as repo:
        source = repo.add(make_source())
        repo.add(make_decision(source))
    assert reset_database(seeded_db, FIXTURES_DIR) == EXPECTED_COUNTS
    assert dump_database(seeded_db) == baseline


def test_reset_creates_a_missing_database(db_path: Path) -> None:
    assert not db_path.exists()
    assert reset_database(db_path, FIXTURES_DIR) == EXPECTED_COUNTS


def test_reset_refuses_a_file_it_did_not_create(tmp_path: Path) -> None:
    stranger = tmp_path / "notes.db"
    stranger.write_text("these are not the rows you are looking for")
    with pytest.raises(ResetRefusedError):
        reset_database(stranger, FIXTURES_DIR)
    assert stranger.read_text() == "these are not the rows you are looking for"


def test_reset_keeps_the_database_when_fixtures_are_broken(seeded_db: Path, tmp_path: Path) -> None:
    broken = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_DIR, broken)
    decisions = json.loads((broken / "decisions.json").read_text())
    decisions[0]["evidence"][0]["excerpt"] = "Words that appear in no source."
    (broken / "decisions.json").write_text(json.dumps(decisions))
    before = dump_database(seeded_db)
    with pytest.raises(FixtureError, match="DEC-001"):
        reset_database(seeded_db, broken)
    assert dump_database(seeded_db) == before


def test_fixture_loader_rejects_a_source_without_the_synthetic_marker(tmp_path: Path) -> None:
    broken = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_DIR, broken)
    (broken / "sources" / "SRC-001.md").write_text("Looks like a real meeting.\n")
    with pytest.raises(FixtureError, match="synthetic marker"):
        load_fixtures(broken)


# --- persistence across processes -------------------------------------------


def test_data_written_by_one_process_is_read_by_another(db_path: Path) -> None:
    # Process A (a fresh interpreter) creates and seeds the database.
    first = run_script("seed_demo.py", db_path)
    assert first.returncode == 0, first.stderr
    assert "decisions           16" in first.stdout

    # This process reads what A wrote, then adds a record and closes the file.
    with Repository.open(db_path) as repo:
        assert repo.counts() == EXPECTED_COUNTS
        assert repo.get(Decision, "DEC-004").market == "France"
        source = repo.add(make_source())
        repo.add(make_decision(source))

    # Process B sees the added record; seeding again does not remove it.
    second = run_script("seed_demo.py", db_path)
    assert second.returncode == 0, second.stderr
    assert "decisions           17" in second.stdout

    # Process C resets to the baseline.
    third = run_script("reset_demo.py", db_path)
    assert third.returncode == 0, third.stderr
    assert "decisions           16" in third.stdout
    with Repository.open(db_path) as repo:
        assert repo.get(Decision, "DEC-900") is None


def test_reset_script_reports_a_refusal_without_a_traceback(tmp_path: Path) -> None:
    stranger = tmp_path / "notes.db"
    stranger.write_text("not a database")
    result = run_script("reset_demo.py", stranger)
    assert result.returncode == 1
    assert "Reset failed" in result.stderr
    assert "Traceback" not in result.stderr
    assert stranger.exists()


# --- review regressions (Stage 14) -----------------------------------------------------


def test_a_locked_database_is_not_mistaken_for_a_foreign_file(seeded_db: Path) -> None:
    # Another connection holds a write lock. That says nothing about whose file it is,
    # so the reset reports the lock (not "not created by this app") and deletes nothing.
    before = dump_database(seeded_db)
    holder = sqlite3.connect(seeded_db, timeout=0)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(DatabaseUnavailableError, match="could not be read") as raised:
            reset_database(seeded_db, FIXTURES_DIR)
        assert not isinstance(raised.value, ResetRefusedError)
        assert "not created by this app" not in str(raised.value)
    finally:
        holder.rollback()
        holder.close()
    assert dump_database(seeded_db) == before


def test_the_reset_script_reports_a_locked_database_without_a_traceback(
    seeded_db: Path,
) -> None:
    holder = sqlite3.connect(seeded_db, timeout=0)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        result = run_script("reset_demo.py", seeded_db)
    finally:
        holder.rollback()
        holder.close()
    assert result.returncode == 1
    assert "Reset failed" in result.stderr and "could not be read" in result.stderr
    assert "Traceback" not in result.stderr
