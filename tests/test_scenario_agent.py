"""Agent 4: pure scenario formulas, editable inputs and where each default comes from."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from decision_memory.agents.scenario_agent import (
    DEFAULT_CHURN,
    DEFAULT_INPUTS,
    EDITED_RATIONALE,
    INTERPOLATED_RATIONALE,
    UNSOURCED_RATIONALE,
    assumption_notes,
    churn_loss,
    default_churn,
    default_inputs,
    formula_trace,
    gross_uplift,
    margin_change,
    retention_proxy,
    revenue_change,
    scenario_set,
    simulate_scenarios,
    within_guardrail,
)
from decision_memory.authz import MemoryView
from decision_memory.models import (
    AssumptionNote,
    EvidenceBasis,
    Role,
    ScenarioInputs,
    ScenarioSet,
)
from decision_memory.provenance import resolve_all
from decision_memory.repository import Repository

NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)

# Quality gate 5: the documented defaults, computed (never stored) by the formulas.
GATE = {0.03: (8.4, 5.58), 0.05: (14.0, 9.50), 0.07: (18.8, 12.86)}


def results(inputs: ScenarioInputs = DEFAULT_INPUTS):
    return {r.surcharge_rate: r for r in simulate_scenarios(inputs, "PRO-DRAFT", NOW)}


def edit(**changes) -> ScenarioInputs:
    return DEFAULT_INPUTS.model_copy(update=changes)


# --- the headline figures -------------------------------------------------------


def test_defaults_compute_the_documented_figures() -> None:
    computed = results()
    assert sorted(computed) == [0.03, 0.05, 0.07]
    for rate, (revenue, margin) in GATE.items():
        assert computed[rate].revenue_change_musd == pytest.approx(revenue, abs=1e-9)
        assert computed[rate].margin_change_musd == pytest.approx(margin, abs=1e-9)
    five = computed[0.05]  # the talk track: 400 × 5% × 90% = 18, minus 400 × 1% = 4
    assert (five.gross_uplift_musd, five.churn_loss_musd) == (18.0, 4.0)


@pytest.mark.parametrize(
    ("baseline", "exempt", "churn", "margin", "cost", "rate"),
    [
        (400, 0.10, 0.010, 0.70, 0.3, 0.05),
        (250, 0.00, 0.000, 1.00, 0.0, 0.03),
        (123.45, 0.37, 0.021, 0.55, 1.7, 0.07),
        (1000, 1.00, 0.050, 0.40, 2.0, 0.09),  # everything exempt: only churn loss remains
    ],
)
def test_figures_follow_the_formulas_for_any_inputs(
    baseline: float, exempt: float, churn: float, margin: float, cost: float, rate: float
) -> None:
    inputs = ScenarioInputs(
        baseline_revenue_musd=baseline, exempt_share=exempt, contribution_margin=margin,
        implementation_cost_musd=cost, churn_guardrail=0.012, churn_loss_by_rate={rate: churn},
    )  # fmt: skip
    (result,) = simulate_scenarios(inputs, "PRO-DRAFT", NOW)
    expected_revenue = baseline * rate * (1 - exempt) - baseline * churn
    assert result.revenue_change_musd == pytest.approx(expected_revenue, abs=1e-6)
    assert result.margin_change_musd == pytest.approx(expected_revenue * margin - cost, abs=1e-6)
    assert result.gross_uplift_musd - result.churn_loss_musd == pytest.approx(
        result.revenue_change_musd, abs=1e-6
    )


@pytest.mark.parametrize(
    ("changes", "rate", "revenue", "margin"),
    [
        ({"baseline_revenue_musd": 500.0}, 0.05, 17.5, 11.95),
        ({"exempt_share": 0.0}, 0.05, 16.0, 10.9),
        ({"contribution_margin": 0.5}, 0.05, 14.0, 6.7),
        ({"implementation_cost_musd": 1.0}, 0.05, 14.0, 8.8),
        ({"churn_loss_by_rate": {0.03: 0.006, 0.05: 0.02, 0.07: 0.016}}, 0.05, 10.0, 6.7),
    ],
)
def test_editing_any_input_recomputes(changes, rate: float, revenue: float, margin: float) -> None:
    result = results(edit(**changes))[rate]
    assert result.revenue_change_musd == pytest.approx(revenue, abs=1e-9)
    assert result.margin_change_musd == pytest.approx(margin, abs=1e-9)


def test_the_guardrail_is_compared_not_applied() -> None:
    a7, a5 = DEFAULT_INPUTS.assumptions(0.07), DEFAULT_INPUTS.assumptions(0.05)
    assert not within_guardrail(a7, 0.012) and within_guardrail(a5, 0.012)
    assert within_guardrail(a5, 0.010)  # equal to the guardrail is within it
    assert results(edit(churn_guardrail=0.0))[0.07].revenue_change_musd == pytest.approx(18.8)


def test_calculator_is_pure_and_deterministic() -> None:
    before = DEFAULT_INPUTS.model_dump()
    first = [r.model_dump() for r in simulate_scenarios(DEFAULT_INPUTS, "PRO-DRAFT", NOW)]
    second = [r.model_dump() for r in simulate_scenarios(DEFAULT_INPUTS, "PRO-DRAFT", NOW)]
    assert first == second and DEFAULT_INPUTS.model_dump() == before
    a = DEFAULT_INPUTS.assumptions(0.03)
    assert gross_uplift(0.03, a) - churn_loss(a) == revenue_change(0.03, a)
    assert margin_change(0.03, a) == pytest.approx(revenue_change(0.03, a) * 0.7 - 0.3)


def test_results_carry_ids_order_and_a_formula_trace() -> None:
    listed = simulate_scenarios(DEFAULT_INPUTS, "PRO-DRAFT", NOW)
    assert [r.id for r in listed] == ["SCN-DRAFT-300BP", "SCN-DRAFT-500BP", "SCN-DRAFT-700BP"]
    assert {r.proposal_id for r in listed} == {"PRO-DRAFT"}
    assert formula_trace(0.05, DEFAULT_INPUTS.assumptions(0.05)) == (
        "revenue = 400.00 × 5% × (1 − 10%) − 400.00 × 1% = 18.00 − 4.00 = 14.00; "
        "margin = 14.00 × 70% − 0.30 = 9.50 (USD M per quarter)"
    )


def test_retention_proxy_is_one_minus_churn_loss() -> None:
    assert retention_proxy(DEFAULT_INPUTS.assumptions(0.05)) == pytest.approx(0.99)
    assert retention_proxy(DEFAULT_INPUTS.assumptions(0.07)) == pytest.approx(0.984)


# --- inputs ------------------------------------------------------------------------


def test_default_churn_interpolates_between_the_defaults() -> None:
    for rate, churn in DEFAULT_CHURN.items():
        assert default_churn(rate) == churn
    assert default_churn(0.0) == 0.0
    assert default_churn(0.04) == pytest.approx(0.008)
    assert default_churn(0.06) == pytest.approx(0.013)
    assert default_churn(0.08) == pytest.approx(0.019)  # continues the last slope
    assert default_churn(5.0) == 1.0  # never above the whole baseline


def test_default_inputs_add_a_churn_loss_for_an_asked_rate() -> None:
    inputs = default_inputs([0.03, 0.04, 0.05, 0.07])
    assert inputs.rates == [0.03, 0.04, 0.05, 0.07]
    assert inputs.churn_loss_by_rate[0.04] == pytest.approx(0.008)
    assert default_inputs() == DEFAULT_INPUTS


def test_options_closer_than_a_basis_point_are_refused() -> None:
    # Two such options would share a result ID and an input box.
    fields = DEFAULT_INPUTS.model_dump()
    with pytest.raises(ValidationError, match="0.01 percentage points"):
        ScenarioInputs(**{**fields, "churn_loss_by_rate": {0.03: 0.006, 0.03004: 0.006}})
    ok = ScenarioInputs(**{**fields, "churn_loss_by_rate": {0.03: 0.006, 0.0301: 0.006}})
    assert ok.rates == [0.03, 0.0301]


def test_inputs_are_validated() -> None:
    with pytest.raises(KeyError):
        DEFAULT_INPUTS.assumptions(0.04)
    for bad in (
        {"baseline_revenue_musd": 0},
        {"exempt_share": 1.5},
        {"implementation_cost_musd": -1},
        {"churn_loss_by_rate": {}},
        {"churn_loss_by_rate": {0.05: -0.1}},
    ):
        with pytest.raises(ValidationError):
            ScenarioInputs(**{**DEFAULT_INPUTS.model_dump(), **bad})


# --- where the defaults come from -------------------------------------------------


def notes_by_key(view: MemoryView, inputs: ScenarioInputs = DEFAULT_INPUTS):
    return {note.key: note for note in assumption_notes(view, inputs)}


@pytest.mark.parametrize("role", list(Role))
def test_default_notes_cite_sources_or_say_they_are_assumed(seeded: Repository, role) -> None:
    view = MemoryView(seeded, role)
    notes = notes_by_key(view)
    supported = {k for k, n in notes.items() if n.basis is EvidenceBasis.SOURCE_SUPPORTED}
    assert supported == {"exempt_share", "churn_guardrail", "churn:0.03", "churn:0.05"}
    cited = {k: [r.source_id for r in n.source_refs] for k, n in notes.items()}
    assert cited["exempt_share"] == ["SRC-022"]
    assert cited["churn_guardrail"] == ["SRC-014"]
    assert cited["churn:0.03"] == ["SRC-004", "SRC-015"]
    assert cited["churn:0.05"] == ["SRC-006", "SRC-011"]
    for note in notes.values():
        assert not note.edited
        if note.source_refs:
            assert resolve_all(view, note.source_refs)  # exact excerpts the role can see
    seven = notes["churn:0.07"]
    assert seven.basis is EvidenceBasis.ASSUMED_FOR_DEMO
    assert [r.source_id for r in seven.source_refs] == ["SRC-009", "SRC-010"]  # context only
    assert "context, not support" in seven.rationale
    for key in ("baseline_revenue_musd", "contribution_margin", "implementation_cost_musd"):
        assert notes[key].basis is EvidenceBasis.ASSUMED_FOR_DEMO and not notes[key].source_refs


def test_an_edited_value_loses_its_citation(seeded: Repository) -> None:
    view = MemoryView(seeded, Role.ANALYST)
    notes = notes_by_key(view, edit(exempt_share=0.2, baseline_revenue_musd=450.0))
    for key in ("exempt_share", "baseline_revenue_musd"):
        assert notes[key].edited and notes[key].basis is EvidenceBasis.ASSUMED_FOR_DEMO
        assert notes[key].rationale == EDITED_RATIONALE and not notes[key].source_refs
    assert notes["churn_guardrail"].basis is EvidenceBasis.SOURCE_SUPPORTED  # unchanged


def test_an_asked_rate_without_a_default_is_reported_as_interpolated(
    seeded: Repository,
) -> None:
    view = MemoryView(seeded, Role.ANALYST)
    inputs = default_inputs([0.03, 0.04, 0.05, 0.07])
    four = notes_by_key(view, inputs)["churn:0.04"]
    assert four.basis is EvidenceBasis.ASSUMED_FOR_DEMO and not four.edited
    assert four.rationale == INTERPOLATED_RATIONALE
    churn = dict(inputs.churn_loss_by_rate) | {0.04: 0.02}
    edited = notes_by_key(view, inputs.model_copy(update={"churn_loss_by_rate": churn}))
    assert edited["churn:0.04"].edited


def test_without_visible_sources_every_default_is_an_assumption(repo: Repository) -> None:
    # An empty database stands in for sources the role cannot see: same code path.
    notes = notes_by_key(MemoryView(repo, Role.ANALYST))
    assert {n.basis for n in notes.values()} == {EvidenceBasis.ASSUMED_FOR_DEMO}
    assert notes["exempt_share"].rationale == UNSOURCED_RATIONALE
    assert all(not n.source_refs for n in notes.values())
    assert all("SRC-" not in n.rationale for n in notes.values())


def test_note_contract() -> None:
    base = {"key": "k", "label": "L", "value": 1.0, "display": "1", "rationale": "why"}
    with pytest.raises(ValidationError, match="must cite"):
        AssumptionNote(**base, basis=EvidenceBasis.SOURCE_SUPPORTED)
    note = AssumptionNote(**base, basis=EvidenceBasis.ASSUMED_FOR_DEMO, edited=True)
    assert math.isclose(note.value, 1.0)


# --- the bundle later agents read -------------------------------------------------


def test_scenario_set_bundles_results_and_notes_from_one_set_of_inputs(
    seeded: Repository,
) -> None:
    view = MemoryView(seeded, Role.ANALYST)
    inputs = edit(exempt_share=0.2)
    bundle = scenario_set(view, inputs, "PRO-DRAFT", NOW)
    assert bundle.inputs == inputs
    assert bundle.results == simulate_scenarios(inputs, "PRO-DRAFT", NOW)
    assert bundle.notes == assumption_notes(view, inputs)
    assert bundle.note("exempt_share").edited and bundle.note("nope") is None
    assert bundle.note("churn_guardrail").value == inputs.churn_guardrail


def test_scenario_set_refuses_results_or_notes_that_do_not_match_its_inputs(
    seeded: Repository,
) -> None:
    view = MemoryView(seeded, Role.ANALYST)
    good = scenario_set(view, DEFAULT_INPUTS, "PRO-DRAFT", NOW)
    other = edit(churn_loss_by_rate={0.03: 0.006, 0.05: 0.03, 0.07: 0.016})
    with pytest.raises(ValidationError, match="not computed from these inputs"):
        ScenarioSet(
            inputs=DEFAULT_INPUTS,
            results=simulate_scenarios(other, "PRO-DRAFT", NOW),
            notes=good.notes,
        )
    with pytest.raises(ValidationError, match="exactly one result per option"):
        ScenarioSet(inputs=DEFAULT_INPUTS, results=good.results[:2], notes=good.notes)
    with pytest.raises(ValidationError, match="must describe the churn guardrail"):
        ScenarioSet(inputs=edit(churn_guardrail=0.02), results=good.results, notes=good.notes)
    with pytest.raises(ValidationError, match="keys must be unique"):
        ScenarioSet(inputs=DEFAULT_INPUTS, results=good.results, notes=[*good.notes, good.notes[0]])
