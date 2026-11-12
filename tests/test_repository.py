"""Repository layer: create/read, updates, filters and referential integrity."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from decision_memory import db
from decision_memory.models import (
    Approval,
    ApprovalVerdict,
    AuditAction,
    AuditEvent,
    Decision,
    Entity,
    EntityKind,
    ExcludedOption,
    KPIUpdate,
    Milestone,
    Outcome,
    OutcomeVerdict,
    Proposal,
    Recommendation,
    RecommendationReason,
    RecommendationType,
    Record,
    Relation,
    RelationType,
    RiskLevel,
    Role,
    ScenarioAssumptions,
    ScenarioResult,
    SlaStatus,
    SourceDocument,
    SourceRef,
)
from decision_memory.repository import (
    DuplicateRecordError,
    IntegrityViolationError,
    Repository,
    column_names,
    stored_models,
    table_name,
)

from conftest import NOW, STAMPS, make_decision, make_source


def sample_records() -> list[Record]:
    """One valid record of every stored type, in an order that satisfies references."""
    source = make_source()
    decision = make_decision(source)
    ref = source.excerpt_ref("Apply a 2% test surcharge in Testland.")
    return [
        source,
        decision,
        Entity(id="ENT-MKT-TEST", kind=EntityKind.MARKET, name="Testland", **STAMPS),
        Relation(
            id="REL-900",
            from_id="DEC-900",
            to_id="ENT-MKT-TEST",
            relation_type=RelationType.APPLIES_TO,
            **STAMPS,
        ),
        Outcome(
            id="OUT-900",
            decision_id="DEC-900",
            verdict=OutcomeVerdict.PARTIAL,
            observed_on=date(2026, 4, 1),
            expected_revenue_change_musd=1.0,
            actual_revenue_change_musd=0.7,
            expected_churn_loss_fraction=0.005,
            actual_churn_loss_fraction=0.008,
            sla_status=SlaStatus.MET,
            explanation="Below plan.",
            source_refs=[ref],
            **STAMPS,
        ),
        Proposal(
            id="PRO-900",
            question="Should we apply a 5% surcharge in Testland?",
            region="Europe",
            target_segments=["All customers"],
            effective_quarter="2027-Q1",
            surcharge_options=[0.03, 0.05, 0.07],
            exclusions=["High-volume contract customers"],
            requested_by_role=Role.PRICING_DIRECTOR,
            **STAMPS,
        ),
        ScenarioResult(
            id="SCN-900",
            proposal_id="PRO-900",
            surcharge_rate=0.05,
            assumptions=ScenarioAssumptions(
                baseline_revenue_musd=100.0,
                exempt_share=0.1,
                churn_loss_fraction=0.01,
                contribution_margin=0.7,
                implementation_cost_musd=0.1,
            ),
            gross_uplift_musd=4.5,
            churn_loss_musd=1.0,
            revenue_change_musd=3.5,
            margin_change_musd=2.35,
            formula_trace="100 x 0.05 x 0.9 - 100 x 0.01",
            **STAMPS,
        ),
        Recommendation(
            id="REC-900",
            proposal_id="PRO-900",
            decision_type=RecommendationType.PHASED_PILOT,
            recommended_rate=0.05,
            reasons=[RecommendationReason(text="A precedent exists.", source_refs=[ref])],
            excluded_options=[ExcludedOption(surcharge_rate=0.07, reason="Breaches guardrail.")],
            risk_level=RiskLevel.MEDIUM,
            assumptions=["Synthetic baseline"],
            action_plan=["Pilot in two markets"],
            approval_required_by=Role.PRICING_DIRECTOR,
            **STAMPS,
        ),
        Approval(
            id="APR-900",
            recommendation_id="REC-900",
            approver_label="Demo Pricing Director",
            role=Role.PRICING_DIRECTOR,
            verdict=ApprovalVerdict.APPROVED,
            chosen_rate=0.05,
            reasons="Within guardrail.",
            decided_at=NOW,
            **STAMPS,
        ),
        Milestone(
            id="MIL-900", proposal_id="PRO-900", name="Pilot start", due_date=date(2027, 1, 4), **STAMPS
        ),
        KPIUpdate(
            id="KPI-900",
            proposal_id="PRO-900",
            week=1,
            planned_revenue_uplift_musd=0.3,
            actual_revenue_uplift_musd=0.28,
            planned_churn_loss_fraction=0.01,
            actual_churn_loss_fraction=0.009,
            risk_flag=True,
            note="Week 1.",
            **STAMPS,
        ),
        AuditEvent(
            id="AUD-900",
            at=NOW,
            actor_label="Demo Pricing Director",
            role=Role.PRICING_DIRECTOR,
            action=AuditAction.APPROVE,
            entity_type="recommendation",
            entity_id="REC-900",
            **STAMPS,
        ),
    ]


# --- schema -----------------------------------------------------------------


def test_bootstrap_is_repeatable_and_stamps_the_file(db_path: Path) -> None:
    conn = db.open_database(db_path)
    db.bootstrap(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("PRAGMA application_id").fetchone()[0] == db.APPLICATION_ID
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_newer_schema_version_is_refused(db_path: Path) -> None:
    conn = db.open_database(db_path)
    conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(db.SchemaError, match="newer"):
        db.open_database(db_path)


@pytest.mark.parametrize("model", stored_models(), ids=lambda model: model.__name__)
def test_table_columns_match_model_fields(repo: Repository, model: type[Record]) -> None:
    rows = repo._conn.execute(f"PRAGMA table_info({table_name(model)})").fetchall()
    assert sorted(row["name"] for row in rows) == sorted(column_names(model))


def test_every_listed_contract_is_stored() -> None:
    names = {model.__name__ for model in stored_models()}
    assert {
        "SourceDocument", "Decision", "Entity", "Relation", "Outcome", "Proposal",
        "ScenarioResult", "Recommendation", "Milestone", "KPIUpdate", "AuditEvent",
    } <= names  # fmt: skip


# --- create / read / update -------------------------------------------------


def test_every_record_type_round_trips(repo: Repository) -> None:
    records = sample_records()
    for record in records:
        repo.add(record)
    for record in records:
        assert repo.get(type(record), record.id) == record
    assert all(count == 1 for count in repo.counts().values())


def test_get_returns_none_for_unknown_id(repo: Repository) -> None:
    assert repo.get(Decision, "DEC-404") is None
    assert not repo.exists("DEC-404")
    assert not repo.exists("nonsense")


def test_add_refuses_a_duplicate_id(repo: Repository) -> None:
    repo.add(make_source())
    with pytest.raises(DuplicateRecordError):
        repo.add(make_source(title="Another title"))
    assert repo.get(SourceDocument, "SRC-900").title == "Test pricing call"


def test_save_updates_in_place_and_keeps_created_at(repo: Repository) -> None:
    source = repo.add(make_source())
    decision = repo.add(make_decision(source))
    later = datetime(2026, 12, 1, 8, 0, tzinfo=UTC)
    changed = decision.model_copy(
        update={"title": "Edited title", "risks": [], "created_at": later, "updated_at": later}
    )
    repo.save(changed)
    stored = repo.get(Decision, decision.id)
    assert stored.title == "Edited title"
    assert stored.risks == []
    assert stored.created_at == NOW
    assert stored.updated_at == later
    assert repo.count(Decision) == 1


def test_save_replaces_access_roles_and_source_refs(repo: Repository) -> None:
    source = repo.add(make_source())
    decision = repo.add(make_decision(source))
    repo.save(source.model_copy(update={"access_roles": [Role.RESTRICTED_EXEC]}))
    assert repo.get(SourceDocument, source.id).access_roles == [Role.RESTRICTED_EXEC]

    extra = source.excerpt_ref("Closing line.")
    repo.save(decision.model_copy(update={"source_refs": [extra, *decision.source_refs]}))
    assert [ref.excerpt for ref in repo.get(Decision, decision.id).source_refs] == [
        "Closing line.",
        "Apply a 2% test surcharge in Testland.",
    ]


def test_find_filters_by_column_values(repo: Repository) -> None:
    source = repo.add(make_source())
    repo.add(make_decision(source, "DEC-901", region="Europe", market="Testland"))
    repo.add(make_decision(source, "DEC-902", region="Asia-Pacific", market="Farland"))
    assert [d.id for d in repo.find(Decision)] == ["DEC-901", "DEC-902"]
    assert [d.id for d in repo.find(Decision, region="Europe")] == ["DEC-901"]
    assert [d.id for d in repo.find(Decision, decision_date=date(2026, 1, 15))] == [
        "DEC-901",
        "DEC-902",
    ]
    assert repo.find(Decision, region="Europe", market="Farland") == []


@pytest.mark.parametrize("column", ["risks", "source_refs", "no_such_column", "id; DROP TABLE x"])
def test_find_rejects_unknown_or_unfilterable_columns(repo: Repository, column: str) -> None:
    with pytest.raises(ValueError, match="Cannot filter"):
        repo.find(Decision, **{column: "x"})


def test_next_id_follows_the_highest_number(repo: Repository) -> None:
    assert repo.next_id(Decision) == "DEC-001"
    source = repo.add(make_source())
    repo.add(make_decision(source, "DEC-007"))
    repo.add(make_decision(source, "DEC-PILOT"))
    assert repo.next_id(Decision) == "DEC-008"


# --- referential integrity --------------------------------------------------


def test_decision_cannot_cite_a_missing_source(repo: Repository) -> None:
    decision = make_decision(make_source())  # the source is never stored
    with pytest.raises(IntegrityViolationError, match="SRC-900, which does not exist"):
        repo.add(decision)
    assert repo.count(Decision) == 0


def test_decision_cannot_cite_text_that_is_not_in_the_source(repo: Repository) -> None:
    source = repo.add(make_source())
    forged = SourceRef(
        source_id=source.id, excerpt="Approve a 20% surcharge.", start_offset=0, end_offset=24
    )
    with pytest.raises(IntegrityViolationError, match="not at offsets"):
        repo.add(make_decision(source, source_refs=[forged]))
    assert repo.count(Decision) == 0


def test_outcome_needs_an_existing_decision(repo: Repository) -> None:
    outcome = Outcome(
        id="OUT-900",
        decision_id="DEC-404",
        verdict=OutcomeVerdict.SUCCESS,
        observed_on=date(2026, 4, 1),
        **STAMPS,
    )
    with pytest.raises(IntegrityViolationError):
        repo.add(outcome)
    assert repo.count(Outcome) == 0


@pytest.mark.parametrize(("from_id", "to_id"), [("DEC-900", "ENT-NOPE"), ("DEC-404", "ENT-MKT-TEST")])
def test_relation_endpoints_must_exist(repo: Repository, from_id: str, to_id: str) -> None:
    for record in sample_records()[:3]:
        repo.add(record)
    relation = Relation(
        id="REL-901", from_id=from_id, to_id=to_id, relation_type=RelationType.AFFECTS, **STAMPS
    )
    with pytest.raises(IntegrityViolationError, match="does not exist"):
        repo.add(relation)
    assert repo.dangling_relations() == []


def test_child_records_need_their_parent(repo: Repository) -> None:
    orphans = [r for r in sample_records() if isinstance(r, ScenarioResult | Milestone | KPIUpdate)]
    assert len(orphans) == 3
    for orphan in orphans:
        with pytest.raises(IntegrityViolationError):
            repo.add(orphan)
    assert repo.counts()["scenario_results"] == 0


def test_recommendation_cannot_cite_an_unknown_source(repo: Repository) -> None:
    records = {type(r).__name__: r for r in sample_records()}
    repo.add(records["Proposal"])
    with pytest.raises(IntegrityViolationError, match="does not exist"):
        repo.add(records["Recommendation"])


def test_database_itself_blocks_deleting_a_cited_source(repo: Repository) -> None:
    source = repo.add(make_source())
    repo.add(make_decision(source))
    with pytest.raises(sqlite3.IntegrityError):
        repo._conn.execute("DELETE FROM source_documents WHERE id = ?", (source.id,))


def test_one_kpi_update_per_week(repo: Repository) -> None:
    records = {type(r).__name__: r for r in sample_records()}
    repo.add(records["Proposal"])
    repo.add(records["KPIUpdate"])
    with pytest.raises(IntegrityViolationError):
        repo.add(records["KPIUpdate"].model_copy(update={"id": "KPI-901"}))


def test_failed_transaction_leaves_nothing_behind(repo: Repository) -> None:
    source = make_source()
    with pytest.raises(IntegrityViolationError):
        with repo.transaction():
            repo.add(source)
            repo.add(make_decision(source, source_refs=[
                SourceRef(source_id="SRC-404", excerpt="x", start_offset=0, end_offset=1)
            ]))
    assert repo.count(SourceDocument) == 0
    assert repo.counts()["decisions"] == 0
    repo.add(source)  # the connection is still usable
    assert repo.count(SourceDocument) == 1
