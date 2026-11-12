"""Validation rules of the typed contracts."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from decision_memory.models import (
    Decision,
    DecisionStatus,
    KPIUpdate,
    Proposal,
    Relation,
    RelationType,
    Role,
    ScenarioAssumptions,
    SourceRef,
)

from conftest import STAMPS, make_decision, make_source


def test_source_ref_offsets_must_match_excerpt_length() -> None:
    SourceRef(source_id="SRC-001", excerpt="abc", start_offset=4, end_offset=7)
    with pytest.raises(ValidationError, match="excerpt length"):
        SourceRef(source_id="SRC-001", excerpt="abc", start_offset=4, end_offset=9)


def test_source_ref_must_point_at_a_source_id() -> None:
    with pytest.raises(ValidationError):
        SourceRef(source_id="DEC-001", excerpt="abc", start_offset=0, end_offset=3)


def test_excerpt_ref_returns_exact_offsets() -> None:
    source = make_source()
    ref = source.excerpt_ref("Apply a 2% test surcharge in Testland.")
    assert source.body[ref.start_offset : ref.end_offset] == ref.excerpt
    assert ref.source_id == source.id


@pytest.mark.parametrize("excerpt", ["not in the body", "line.", ""])
def test_excerpt_ref_rejects_missing_or_ambiguous_excerpts(excerpt: str) -> None:
    with pytest.raises(ValueError, match="exactly once"):
        make_source().excerpt_ref(excerpt)


def test_source_needs_at_least_one_access_role() -> None:
    with pytest.raises(ValidationError):
        make_source(access_roles=[])


def test_access_roles_are_validated_and_canonical() -> None:
    source = make_source(access_roles=["restricted_exec", "analyst", "analyst"])
    assert source.access_roles == [Role.ANALYST, Role.RESTRICTED_EXEC]
    with pytest.raises(ValidationError):
        make_source(access_roles=["intern"])


@pytest.mark.parametrize("bad_id", ["900", "SRC900", "DEC-900", "src-900", "SRC-"])
def test_record_id_must_carry_its_prefix(bad_id: str) -> None:
    with pytest.raises(ValidationError, match="id must look like"):
        make_source(source_id=bad_id)


def test_timestamps_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError):
        make_source(created_at=datetime(2026, 1, 1, 9, 0))


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        make_source(password="x")


def test_records_are_synthetic_by_default() -> None:
    assert make_source().synthetic is True


def test_confirmed_decision_must_cite_evidence() -> None:
    source = make_source()
    with pytest.raises(ValidationError, match="at least one source excerpt"):
        make_decision(source, source_refs=[])
    draft = make_decision(source, source_refs=[], status=DecisionStatus.DRAFT)
    assert draft.status is DecisionStatus.DRAFT
    with pytest.raises(ValidationError):
        draft.status = DecisionStatus.CONFIRMED


@pytest.mark.parametrize("rate", [-0.01, 1.01])
def test_surcharge_rate_is_a_fraction(rate: float) -> None:
    with pytest.raises(ValidationError):
        make_decision(make_source(), surcharge_rate=rate)


def test_relation_cannot_link_a_record_to_itself() -> None:
    with pytest.raises(ValidationError, match="itself"):
        Relation(
            id="REL-900",
            from_id="DEC-900",
            to_id="DEC-900",
            relation_type=RelationType.SIMILAR_TO,
            **STAMPS,
        )


@pytest.mark.parametrize("week", [0, 9])
def test_kpi_week_is_one_to_eight(week: int) -> None:
    with pytest.raises(ValidationError):
        KPIUpdate(
            id="KPI-900",
            proposal_id="PRO-900",
            week=week,
            planned_revenue_uplift_musd=1.0,
            planned_churn_loss_fraction=0.01,
            **STAMPS,
        )


def test_proposal_validates_quarter_and_options() -> None:
    fields = {
        "id": "PRO-900",
        "question": "Should we apply a surcharge?",
        "region": "Europe",
        "surcharge_options": [0.03, 0.05, 0.07],
        "requested_by_role": Role.PRICING_DIRECTOR,
        **STAMPS,
    }
    assert Proposal(effective_quarter="2027-Q1", **fields).surcharge_options == [0.03, 0.05, 0.07]
    with pytest.raises(ValidationError):
        Proposal(effective_quarter="next quarter", **fields)
    with pytest.raises(ValidationError):
        Proposal(effective_quarter="2027-Q1", **{**fields, "surcharge_options": []})


def test_scenario_assumptions_bounds() -> None:
    fields = {
        "baseline_revenue_musd": 400.0,
        "exempt_share": 0.10,
        "churn_loss_fraction": 0.01,
        "contribution_margin": 0.70,
        "implementation_cost_musd": 0.3,
    }
    ScenarioAssumptions(**fields)
    for name, bad in [("baseline_revenue_musd", 0), ("exempt_share", 1.5), ("contribution_margin", -0.1)]:
        with pytest.raises(ValidationError):
            ScenarioAssumptions(**{**fields, name: bad})


def test_decision_dates_parse_from_iso_strings() -> None:
    decision = make_decision(make_source(), decision_date="2026-02-03")
    assert decision.decision_date == date(2026, 2, 3)
    assert isinstance(decision, Decision)
