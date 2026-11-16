"""Agent 6: simulated approval, the 60-day tracker, weekly KPIs, closing with an outcome,
the proposal state machine, the audit trail and role filtering."""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from decision_memory.agents import governance_agent as gov
from decision_memory.agents.governance_agent import (
    NOTHING_TO_APPROVE,
    GovernanceError,
    approve_recommendation,
    close_pilot,
    kpi_flags,
    milestone_states,
    pilot_market_options,
    pilot_record,
    plan_pilot,
    record_kpi,
    reject_recommendation,
    suggest_verdict,
)
from decision_memory.agents.impact_agent import HVC_EXCLUSION, assess_impact, draft_proposal
from decision_memory.agents.memory_agent import build_decision_graph
from decision_memory.agents.recommendation_agent import recommend
from decision_memory.agents.scenario_agent import default_inputs, scenario_set
from decision_memory.audit import (
    InvalidTransitionError,
    PermissionDeniedError,
    record_kpi_update,
    set_proposal_status,
)
from decision_memory.authz import MemoryView
from decision_memory.models import (
    PROPOSAL_TRANSITIONS,
    Approval,
    ApprovalRequest,
    AuditEvent,
    Decision,
    KPIUpdate,
    Milestone,
    MilestoneState,
    Outcome,
    OutcomeVerdict,
    Proposal,
    ProposalStatus,
    Recommendation,
    RecommendationReason,
    RecommendationResult,
    Relation,
    RelationType,
    Role,
    SlaStatus,
    SourceDocument,
    SourceType,
)
from decision_memory.precedent_search import SearchSettings, infer_query, search_precedents
from decision_memory.repository import Repository
from decision_memory.seed import reset_database

from conftest import FIXTURES_DIR, HIDDEN_FROM_DIRECTOR, dump_database

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
QUESTION = "Should we increase the freight surcharge by 5% for European customers next quarter?"
OFF_TOPIC = "Should we migrate the payroll system to a new vendor?"
PILOT = ApprovalRequest(approver_label="Dana Example", markets=["France", "Italy"], pilot_share=0.2)
DIRECTOR, ANALYST, EXEC = Role.PRICING_DIRECTOR, Role.ANALYST, Role.RESTRICTED_EXEC


def pipeline(repo, role=DIRECTOR, *, question=QUESTION, exclude=True, guardrail=None):
    """Tabs 2 to 5 for one question, as the app runs them."""
    view = MemoryView(repo, role)
    query = infer_query(question, NOW.date())
    search = search_precedents(view, query)
    proposal = draft_proposal(
        query.text, query.region, query.as_of, NOW,
        surcharge_rate=query.surcharge_rate, exclusions=[HVC_EXCLUSION] if exclude else [],
    )  # fmt: skip
    impact = assess_impact(view, proposal, search.precedent_ids)
    inputs = default_inputs(proposal.surcharge_options)
    if guardrail is not None:
        inputs = inputs.model_copy(update={"churn_guardrail": guardrail})
    scenarios = scenario_set(view, inputs, proposal.id, NOW)
    result = recommend(view, proposal, search.precedent_ids, impact, scenarios, NOW)
    return proposal, scenarios, result


def approve(repo, role=DIRECTOR, request=PILOT, now=NOW, **kwargs):
    proposal, scenarios, result = pipeline(repo, role, **kwargs)
    return approve_recommendation(
        repo, draft=proposal, result=result, scenarios=scenarios, request=request,
        role=role, now=now,
    )  # fmt: skip


def kpi(repo, proposal_id, week=1, revenue=0.215, churn=0.01, sla=SlaStatus.MET, role=ANALYST):
    return record_kpi(
        repo, proposal_id=proposal_id, week=week, actual_revenue_musd=revenue,
        actual_churn_loss_fraction=churn, sla_status=sla, note="", actor_label="Sam Example",
        role=role, now=NOW + timedelta(days=7 * week),
    )  # fmt: skip


def close(repo, proposal_id, verdict=OutcomeVerdict.SUCCESS, note="", role=DIRECTOR):
    return close_pilot(
        repo, proposal_id=proposal_id, verdict=verdict, note=note, actor_label="Dana Example",
        role=role, now=NOW + timedelta(days=60),
    )  # fmt: skip


def actions(events: list[AuditEvent]) -> list[tuple[str, str]]:
    return [(e.action.value, e.entity_type) for e in events]


# --- approval -----------------------------------------------------------------------


def test_approval_opens_a_60_day_tracker_with_8_weekly_slots(seeded: Repository) -> None:
    record = approve(seeded)
    proposal = record.proposal
    assert (proposal.id, proposal.status, proposal.churn_guardrail) == (
        "PRO-001", ProposalStatus.APPROVED, 0.012,
    )  # fmt: skip
    assert proposal.exclusions == [HVC_EXCLUSION]
    assert record.recommendation.id == "REC-001"
    assert record.recommendation.proposal_id == "PRO-001"
    assert record.approval.verdict.value == "approved"
    assert record.approval.chosen_rate == 0.05
    assert [(m.name, m.due_date, m.state) for m in record.milestones] == [
        ("Live: week 1 KPI recorded", date(2026, 11, 23), MilestoneState.IN_PROGRESS),
        ("Week 4 KPI checkpoint", date(2026, 12, 14), MilestoneState.PLANNED),
        ("Week 8 KPI checkpoint", date(2027, 1, 11), MilestoneState.PLANNED),
        ("60-day executive review", date(2027, 1, 15), MilestoneState.PLANNED),
    ]
    assert [k.week for k in record.kpis] == list(range(1, 9))
    # +$14.00M a quarter x 20% pilot share / 13 weeks = +$0.215M a week; churn 1.0% planned.
    assert {
        (k.planned_revenue_uplift_musd, k.planned_churn_loss_fraction) for k in record.kpis
    } == {(0.215, 0.01)}
    assert record.recorded == [] and record.next_week == 1
    assert record.outcome is None


def test_the_approved_pilot_enters_memory_as_a_cited_decision(seeded: Repository) -> None:
    record = approve(seeded)
    decision = record.decision
    assert decision.id == "DEC-017" and decision.status.value == "confirmed"
    assert decision.title == "Europe: +5% surcharge pilot in France and Italy"
    assert decision.statement.startswith("Run a +5% freight surcharge as a phased pilot in France")
    assert (decision.market, decision.surcharge_rate, decision.owner) == (
        "France and Italy", 0.05, "Dana Example",
    )  # fmt: skip
    assert decision.customer_segment == "All customers except high-volume contract customers"
    source = seeded.get(SourceDocument, "SRC-023")
    assert source.source_type is SourceType.GOVERNANCE_RECORD
    assert source.mock_link == "mock://governance_record/SRC-023"
    assert source.access_roles == sorted(Role)  # the recommendation cites nothing restricted
    assert [ref.excerpt.split(":")[0] for ref in decision.source_refs] == [
        "Decision",
        "Approved by",
    ]
    for ref in decision.source_refs:
        assert source.body[ref.start_offset : ref.end_offset] == ref.excerpt
    assert "+$14.00M × 20% ÷ 13 = +$0.215M" in source.body
    links = seeded.find(Relation, from_id="DEC-017")
    assert {(r.relation_type, r.to_id) for r in links} == {
        *((RelationType.INFORMED_BY, d) for d in ("DEC-001", "DEC-004", "DEC-007", "DEC-009",
                                                    "DEC-011")),
        (RelationType.APPLIES_TO, "ENT-MKT-FR"),
        (RelationType.APPLIES_TO, "ENT-MKT-IT"),
    }  # fmt: skip
    assert seeded.dangling_relations() == []


def test_approval_is_audited_with_neutral_details(seeded: Repository) -> None:
    record = approve(seeded, request=PILOT.model_copy(update={"note": "Watch Germany closely"}))
    assert actions(record.events) == [
        ("approve", "recommendation"),
        ("capture", "decision"),
        ("confirm", "decision"),
        ("approve", "proposal"),
        ("edit", "milestone"),
    ]
    assert {e.actor_label for e in record.events} == {"Dana Example"}
    for event in record.events:
        assert "Watch Germany" not in event.detail and "freight surcharge" not in event.detail
    assert "Approver note: Watch Germany closely" in seeded.get(SourceDocument, "SRC-023").body


def test_analyst_cannot_approve_or_reject_and_nothing_is_stored(seeded: Repository) -> None:
    before = seeded.counts()
    proposal, scenarios, result = pipeline(seeded, ANALYST)
    for write in (approve_recommendation, reject_recommendation):
        with pytest.raises(PermissionDeniedError):
            write(
                seeded, draft=proposal, result=result, scenarios=scenarios, request=PILOT,
                role=ANALYST, now=NOW,
            )  # fmt: skip
    assert seeded.counts() == before


def test_approval_waits_for_the_required_exclusion(seeded: Repository) -> None:
    before = seeded.counts()
    with pytest.raises(GovernanceError, match="Tick the exclusion in tab 3"):
        approve(seeded, exclude=False)
    assert seeded.counts() == before


@pytest.mark.parametrize(
    ("markets", "message"),
    [
        (["France"], "Choose exactly 2 pilot markets"),
        (["France", "Italy", "Iberia"], "Choose exactly 2 pilot markets"),
        (["France", "Atlantis"], "Choose pilot markets from the list"),
        (["France", "Singapore"], "Choose pilot markets from the list"),  # not in Europe
    ],
)
def test_pilot_markets_are_checked(seeded: Repository, markets, message) -> None:
    with pytest.raises(GovernanceError, match=message):
        approve(seeded, request=PILOT.model_copy(update={"markets": markets}))
    assert seeded.count(Proposal) == 0


def test_market_options_are_the_regions_markets_the_role_can_see(seeded: Repository) -> None:
    options = pilot_market_options(MemoryView(seeded, DIRECTOR), "Europe")
    assert list(options) == [
        "Benelux", "France", "Germany", "Iberia", "Italy", "Nordics", "Poland and CEE",
        "UK and Ireland",
    ]  # fmt: skip
    assert options["France"] == "ENT-MKT-FR"
    assert pilot_market_options(MemoryView(seeded, DIRECTOR), "Mars") == {}


def test_one_open_pilot_per_question(seeded: Repository) -> None:
    first = approve(seeded)
    with pytest.raises(GovernanceError, match=r"already open \(PRO-001\)"):
        approve(seeded)
    kpi(seeded, first.proposal.id)
    close(seeded, first.proposal.id, OutcomeVerdict.SUCCESS)
    assert approve(seeded).proposal.id == "PRO-002"  # a closed pilot allows a new decision


def test_a_recommendation_without_a_rate_cannot_be_approved(seeded: Repository) -> None:
    proposal, scenarios, result = pipeline(seeded, question=OFF_TOPIC)
    assert result.recommendation.recommended_rate is None
    with pytest.raises(GovernanceError, match="Nothing to approve"):
        approve_recommendation(
            seeded, draft=proposal, result=result, scenarios=scenarios, request=PILOT,
            role=DIRECTOR, now=NOW,
        )  # fmt: skip
    assert NOTHING_TO_APPROVE.startswith("Nothing to approve")


def test_a_full_rollout_tracks_the_whole_baseline(seeded: Repository) -> None:
    rollout = ApprovalRequest(approver_label="Dana Example")
    with pytest.raises(GovernanceError, match="no pilot markets"):
        approve(seeded, request=PILOT, guardrail=0.009)
    record = approve(seeded, request=rollout, guardrail=0.009)  # "Proceed at +3%"
    assert record.decision.title == "Europe: +3% surcharge from 2027-Q1"
    assert record.decision.market == "Europe (all markets)"
    # +$8.40M a quarter / 13 weeks = +$0.646M a week; churn 0.6% planned; guardrail as edited.
    assert record.kpis[0].planned_revenue_uplift_musd == 0.646
    assert record.kpis[0].planned_churn_loss_fraction == 0.006
    assert record.proposal.churn_guardrail == 0.009
    assert not seeded.find(Relation, from_id=record.decision.id, relation_type="APPLIES_TO")


def test_the_weekly_plan_is_a_visible_formula(seeded: Repository) -> None:
    _, scenarios, result = pipeline(seeded)
    plan = plan_pilot(result, scenarios, PILOT)
    assert plan.formula.endswith("= +$14.00M × 20% ÷ 13 = +$0.215M (rounded to $1K)")
    half = plan_pilot(result, scenarios, PILOT.model_copy(update={"pilot_share": 0.5}))
    assert half.planned_weekly_revenue_musd == round(14.0 * 0.5 / 13, 3) == 0.538
    edited = scenarios.model_copy(
        update={"inputs": scenarios.inputs.model_copy(update={"churn_guardrail": 0.011})}
    )
    assert plan_pilot(result, edited, PILOT).churn_guardrail == 0.011


def test_rejection_records_no_pilot(seeded: Repository) -> None:
    proposal, scenarios, result = pipeline(seeded)
    record = reject_recommendation(
        seeded, draft=proposal, result=result, scenarios=scenarios,
        request=PILOT.model_copy(update={"note": "Not this quarter"}), role=DIRECTOR, now=NOW,
    )  # fmt: skip
    assert record.proposal.status is ProposalStatus.REJECTED
    assert record.approval.verdict.value == "rejected" and record.approval.reasons == (
        "Not this quarter"
    )
    assert (record.decision, record.milestones, record.kpis) == (None, [], [])
    assert seeded.count(Decision) == 16 and seeded.count(SourceDocument) == 22
    assert actions(record.events) == [("reject", "recommendation"), ("reject", "proposal")]
    with pytest.raises(InvalidTransitionError):
        set_proposal_status(
            seeded, "PRO-001", ProposalStatus.APPROVED, actor_label="x", role=DIRECTOR, now=NOW
        )


# --- the state machine --------------------------------------------------------------


@pytest.mark.parametrize(("start", "to"), list(itertools.product(ProposalStatus, ProposalStatus)))
def test_only_listed_transitions_are_allowed(
    seeded: Repository, start: ProposalStatus, to: ProposalStatus
) -> None:
    record = approve(seeded)
    seeded.save(record.proposal.model_copy(update={"status": start}))
    # A pilot starts only with a recorded week, so give the slot one.
    week_1 = record.kpis[0].model_copy(
        update={"actual_revenue_uplift_musd": 0.2, "actual_churn_loss_fraction": 0.01}
    )
    seeded.save(week_1)
    move = lambda: set_proposal_status(
        seeded, record.proposal.id, to, actor_label="Dana Example", role=DIRECTOR, now=NOW
    )
    before = seeded.count(AuditEvent)
    if to in PROPOSAL_TRANSITIONS[start]:
        assert move().status is to
        assert seeded.count(AuditEvent) == before + 1
    else:
        with pytest.raises(InvalidTransitionError):
            move()
        assert seeded.get(Proposal, record.proposal.id).status is start
        assert seeded.count(AuditEvent) == before


def test_the_state_machine_is_the_documented_one() -> None:
    allowed = {(a, b) for a, targets in PROPOSAL_TRANSITIONS.items() for b in targets}
    assert allowed == {
        (ProposalStatus.DRAFT, ProposalStatus.APPROVED),
        (ProposalStatus.DRAFT, ProposalStatus.REJECTED),
        (ProposalStatus.APPROVED, ProposalStatus.IN_PROGRESS),
        (ProposalStatus.IN_PROGRESS, ProposalStatus.COMPLETED),
    }


def test_a_pilot_cannot_start_without_a_recorded_week(seeded: Repository) -> None:
    record = approve(seeded)
    with pytest.raises(InvalidTransitionError, match="first recorded weekly KPI"):
        set_proposal_status(
            seeded, record.proposal.id, ProposalStatus.IN_PROGRESS, actor_label="Sam",
            role=ANALYST, now=NOW,
        )  # fmt: skip
    assert seeded.get(Proposal, record.proposal.id).status is ProposalStatus.APPROVED


def test_an_analyst_cannot_make_approver_moves(seeded: Repository) -> None:
    record = approve(seeded)
    for to in (ProposalStatus.REJECTED, ProposalStatus.COMPLETED):
        with pytest.raises(PermissionDeniedError):
            set_proposal_status(
                seeded, record.proposal.id, to, actor_label="Sam", role=ANALYST, now=NOW
            )


# --- weekly KPIs --------------------------------------------------------------------


def test_the_first_kpi_starts_the_pilot_and_updates_milestones(seeded: Repository) -> None:
    pid = approve(seeded).proposal.id
    stored = kpi(seeded, pid, revenue=0.22, churn=0.009)
    assert (stored.week, stored.risk_flag) == (1, False)
    record = pilot_record(MemoryView(seeded, ANALYST), pid)
    assert record.proposal.status is ProposalStatus.IN_PROGRESS
    assert [m.state.value for m in record.milestones] == [
        "done", "in_progress", "planned", "planned",
    ]  # fmt: skip
    assert actions(record.events)[-4:] == [
        ("kpi_update", "kpi_update"),
        ("kpi_update", "proposal"),
        ("edit", "milestone"),
        ("edit", "milestone"),
    ]
    assert record.events[-4].role is ANALYST


def test_weeks_are_recorded_in_order_and_never_overwritten(seeded: Repository) -> None:
    pid = approve(seeded).proposal.id
    with pytest.raises(GovernanceError, match="next week to record is week 1"):
        kpi(seeded, pid, week=2)
    first = kpi(seeded, pid)
    with pytest.raises(GovernanceError, match="next week to record is week 2"):
        kpi(seeded, pid, week=1)
    with pytest.raises(ValueError, match="already recorded"):
        record_kpi_update(
            seeded, first.model_copy(update={"actual_revenue_uplift_musd": 9.9}),
            actor_label="x", role=ANALYST, now=NOW,
        )  # fmt: skip
    with pytest.raises(ValueError, match="fixed at approval"):
        slot = seeded.find(KPIUpdate, proposal_id=pid, week=2)[0]
        record_kpi_update(
            seeded,
            slot.model_copy(update={"planned_revenue_uplift_musd": 9.9,
                                    "actual_revenue_uplift_musd": 0.2,
                                    "actual_churn_loss_fraction": 0.01}),
            actor_label="x", role=ANALYST, now=NOW,
        )  # fmt: skip
    assert seeded.get(KPIUpdate, first.id).actual_revenue_uplift_musd == 0.215


def test_a_flagged_week_puts_the_next_milestones_at_risk(seeded: Repository) -> None:
    pid = approve(seeded).proposal.id
    stored = kpi(seeded, pid, revenue=0.1, churn=0.013)
    assert stored.risk_flag
    states = [m.state.value for m in pilot_record(MemoryView(seeded, DIRECTOR), pid).milestones]
    assert states == ["done", "at_risk", "at_risk", "at_risk"]


def test_kpis_need_an_open_pilot(seeded: Repository) -> None:
    pid = approve(seeded).proposal.id
    for week in range(1, 9):
        kpi(seeded, pid, week=week)
    with pytest.raises(GovernanceError, match="Every weekly KPI slot is recorded"):
        kpi(seeded, pid, week=9)
    close(seeded, pid)
    with pytest.raises(GovernanceError, match="This pilot is completed"):
        kpi(seeded, pid, week=1)


def test_kpi_flags() -> None:
    slot = KPIUpdate(
        id="KPI-900", proposal_id="PRO-900", week=1, planned_revenue_uplift_musd=0.2,
        planned_churn_loss_fraction=0.01, created_at=NOW, updated_at=NOW,
    )  # fmt: skip

    def flags(revenue, churn, sla=SlaStatus.MET):
        filled = slot.model_copy(
            update={"actual_revenue_uplift_musd": revenue, "actual_churn_loss_fraction": churn,
                    "sla_status": sla}
        )  # fmt: skip
        return kpi_flags(filled, 0.012)

    assert kpi_flags(slot, 0.012) == []  # nothing recorded yet
    assert flags(0.2, 0.01) == []  # exactly on plan
    assert flags(0.25, 0.011) == ["churn loss above plan"]
    assert flags(0.25, 0.013) == ["churn loss above the 1.2% guardrail: pause and review"]
    assert flags(0.19, 0.01, SlaStatus.BREACHED) == ["revenue change below plan", "SLA breached"]
    assert flags(0.2, 0.01, SlaStatus.AT_RISK) == ["SLA at risk"]


# --- closing ------------------------------------------------------------------------


def weeks(*rows, planned=(0.2, 0.01)):
    return [
        KPIUpdate(
            id=f"KPI-9{week:02d}", proposal_id="PRO-900", week=week,
            planned_revenue_uplift_musd=planned[0], planned_churn_loss_fraction=planned[1],
            actual_revenue_uplift_musd=revenue, actual_churn_loss_fraction=churn,
            sla_status=sla, created_at=NOW, updated_at=NOW,
        )
        for week, (revenue, churn, sla) in enumerate(rows, start=1)
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("rows", "verdict", "reason"),
    [
        ([(0.2, 0.01, SlaStatus.MET), (0.21, 0.009, SlaStatus.NOT_MODELED)], "success",
         "Revenue change at or above plan"),
        ([(0.19, 0.01, SlaStatus.MET)], "partial", "but revenue change below plan."),
        ([(0.3, 0.011, SlaStatus.MET)], "partial", "but average churn loss above plan."),
        ([(0.3, 0.01, SlaStatus.AT_RISK)], "partial", "SLA at risk or breached in week 1"),
        ([(0.3, 0.013, SlaStatus.MET)], "failed", "above the 1.2% guardrail"),
        ([(0.1, 0.01, SlaStatus.MET), (-0.1, 0.01, SlaStatus.MET)], "failed", "No revenue gain"),
    ],
)  # fmt: skip
def test_suggested_verdict(rows, verdict: str, reason: str) -> None:
    suggestion = suggest_verdict(weeks(*rows), 0.012)
    assert suggestion.verdict.value == verdict
    assert reason in suggestion.reason


def test_the_suggestion_scales_recorded_weeks_to_a_quarter() -> None:
    suggestion = suggest_verdict(
        weeks((0.2, 0.008, SlaStatus.MET), (0.3, 0.012, SlaStatus.BREACHED)), 0.012
    )
    assert suggestion.weeks_recorded == 2
    assert suggestion.planned_revenue_musd == 2.6  # 0.2 a week x 13
    assert suggestion.actual_revenue_musd == 3.25  # (0.2 + 0.3) / 2 x 13
    assert suggestion.actual_churn_loss_fraction == 0.01
    assert suggestion.sla_status is SlaStatus.BREACHED
    with pytest.raises(GovernanceError, match="No weekly KPI"):
        suggest_verdict(weeks(), 0.012)


def test_closing_writes_a_cited_outcome_back_to_memory(seeded: Repository) -> None:
    pid = approve(seeded).proposal.id
    kpi(seeded, pid, revenue=0.22, churn=0.009)
    outcome = close(seeded, pid, OutcomeVerdict.SUCCESS)
    assert (outcome.id, outcome.decision_id, outcome.verdict) == (
        "OUT-015", "DEC-017", OutcomeVerdict.SUCCESS,
    )  # fmt: skip
    assert outcome.observed_on == date(2027, 1, 15)
    # One week, scaled to a quarter: 0.215 x 13 = 2.795 planned, 0.22 x 13 = 2.86 actual.
    assert (outcome.expected_revenue_change_musd, outcome.actual_revenue_change_musd) == (
        2.79, 2.86,
    )  # fmt: skip
    assert (outcome.expected_churn_loss_fraction, outcome.actual_churn_loss_fraction) == (
        0.01, 0.009,
    )  # fmt: skip
    assert outcome.explanation.startswith("Closed after 1 of 8 weekly checks")
    report = seeded.get(SourceDocument, outcome.source_refs[0].source_id)
    assert report.source_type is SourceType.GOVERNANCE_RECORD
    assert [ref.excerpt.split(" ")[0] for ref in outcome.source_refs] == ["Result", "Verdict:"]
    assert "Week 1: revenue change +$0.220M against +$0.215M planned" in report.body
    record = pilot_record(MemoryView(seeded, DIRECTOR), pid)
    assert record.proposal.status is ProposalStatus.COMPLETED
    assert record.outcome == outcome
    assert [m.state.value for m in record.milestones] == ["done", "planned", "planned", "done"]
    assert ("close", "outcome") in actions(record.events)
    assert ("close", "proposal") in actions(record.events)


@pytest.mark.parametrize("role", list(Role))
def test_the_outcome_shows_in_search_and_graph_after_refresh(seeded: Repository, role) -> None:
    pid = approve(seeded).proposal.id
    kpi(seeded, pid)
    close(seeded, pid, OutcomeVerdict.SUCCESS)
    view = MemoryView(seeded, role)  # a fresh read, as on the next rerun
    found = search_precedents(view, infer_query(QUESTION, NOW.date()))
    assert "DEC-017" in found.precedent_ids
    graph = build_decision_graph(view)
    ids = {node.id for node in graph.nodes}
    assert {"DEC-017", "OUT-015", "SRC-023", "SRC-024"} <= ids
    assert any(
        (e.from_id, e.edge_type.value, e.to_id) == ("DEC-017", "RESULTED_IN", "OUT-015")
        for e in graph.edges
    )
    # The next recommendation counts the pilot as a successful +5% precedent.
    *_, result = pipeline(seeded, role)
    backed = next(o for o in result.options if o.surcharge_rate == 0.05)
    assert "DEC-017" in backed.supporting_decision_ids


def test_closing_needs_an_approver_a_running_pilot_and_a_note_to_override(
    seeded: Repository,
) -> None:
    pid = approve(seeded).proposal.id
    with pytest.raises(GovernanceError, match="only while it is in progress"):
        close(seeded, pid)  # approved, no KPI yet
    kpi(seeded, pid, revenue=0.1)  # below plan: the rule suggests partial
    with pytest.raises(PermissionDeniedError):
        close(seeded, pid, role=ANALYST)
    with pytest.raises(GovernanceError, match="Add a note"):
        close(seeded, pid, OutcomeVerdict.SUCCESS)
    assert seeded.count(Outcome) == 14
    outcome = close(seeded, pid, OutcomeVerdict.SUCCESS, note="Week 1 was a holiday week")
    assert "Verdict set by the reviewer; the rule suggested partial." in outcome.explanation
    assert "Reviewer note: Week 1 was a holiday week" in outcome.explanation
    with pytest.raises(GovernanceError, match="only while it is in progress"):
        close(seeded, pid)


def test_milestone_states_rule() -> None:
    start = date(2026, 11, 16)
    milestones = [
        Milestone(id=f"MIL-90{i}", proposal_id="PRO-900", name=rule.name,
                  due_date=start + timedelta(days=rule.due_day), created_at=NOW, updated_at=NOW)
        for i, rule in enumerate(gov.DEFAULT_RULES.milestones)
    ]  # fmt: skip

    def states(kpis, status=ProposalStatus.IN_PROGRESS, today=start):
        return [m.state.value for m in milestone_states(milestones, kpis, status, today)]

    assert states([], ProposalStatus.APPROVED) == ["in_progress", "planned", "planned", "planned"]
    four = weeks(*[(0.2, 0.01, SlaStatus.MET)] * 4)
    assert states(four) == ["done", "done", "in_progress", "planned"]
    assert states(four, today=start + timedelta(days=57)) == ["done", "done", "at_risk", "planned"]
    flagged = [four[0].model_copy(update={"risk_flag": True})]
    assert states(flagged) == ["done", "at_risk", "at_risk", "at_risk"]
    assert states(flagged, ProposalStatus.COMPLETED) == ["done", "planned", "planned", "done"]


# --- every change is audited, and the reset restores the baseline ------------------------


def test_every_record_the_flow_writes_has_an_audit_event(seeded: Repository) -> None:
    pid = approve(seeded).proposal.id
    kpi(seeded, pid)
    close(seeded, pid)
    audited = {e.entity_id for e in seeded.find(AuditEvent)}
    written = [
        *(p.id for p in seeded.find(Proposal)),
        *(r.id for r in seeded.find(Recommendation)),
        "DEC-017",
        "OUT-015",
        *(k.id for k in seeded.find(KPIUpdate) if k.actual_revenue_uplift_musd is not None),
        *(m.id for m in seeded.find(Milestone) if m.state is not MilestoneState.PLANNED),
    ]
    assert set(written) <= audited
    approval = seeded.find(Approval)[0]
    assert any(approval.id in e.detail for e in seeded.find(AuditEvent, entity_type="proposal"))


def test_reset_removes_governance_records(seeded_db: Path) -> None:
    baseline = dump_database(seeded_db)
    with Repository.open(seeded_db) as repo:
        pid = approve(repo).proposal.id
        kpi(repo, pid)
        close(repo, pid)
    reset_database(seeded_db, FIXTURES_DIR)
    assert dump_database(seeded_db) == baseline


# --- role filtering -----------------------------------------------------------------


def restricted_approval(repo: Repository):
    """The executive approves a recommendation that also cites a restricted source."""
    proposal, scenarios, result = pipeline(repo, EXEC)
    secret = repo.get(SourceDocument, "SRC-019")
    line = next(text for text in secret.body.splitlines() if "Board" in text)
    reason = RecommendationReason(text="Board context.", source_refs=[secret.excerpt_ref(line)])
    rec = result.recommendation
    result = RecommendationResult.model_validate(
        {**result.model_dump(), "recommendation": {**rec.model_dump(),
                                                   "reasons": [*rec.model_dump()["reasons"],
                                                               reason.model_dump()]}}
    )  # fmt: skip
    return approve_recommendation(
        repo, draft=proposal, result=result, scenarios=scenarios, request=PILOT, role=EXEC,
        now=NOW,
    )  # fmt: skip


@pytest.mark.parametrize("role", [DIRECTOR, ANALYST])
def test_a_restricted_pilot_is_invisible_to_other_roles(seeded: Repository, role) -> None:
    record = restricted_approval(seeded)
    assert seeded.get(SourceDocument, "SRC-023").access_roles == [EXEC]
    pid, decision_id = record.proposal.id, record.decision.id
    view = MemoryView(seeded, role)
    assert view.proposals() == [] and view.proposal(pid) is None
    assert pilot_record(view, pid) is None and gov.pilot_records(view) == []
    assert view.decision(decision_id) is None and view.source("SRC-023") is None
    assert view.milestones(pid) == [] and view.kpi_updates(pid) == []
    assert view.audit_trail(pid) == [] and view.approvals(record.recommendation.id) == []
    assert all(decision_id not in (r.from_id, r.to_id) for r in view.relations())
    graph = build_decision_graph(view)
    assert decision_id not in {node.id for node in graph.nodes}
    shown = " ".join(
        [*(d.title + d.statement for d in view.decisions()), *(s.body for s in view.sources())]
    )
    for secret in (*HIDDEN_FROM_DIRECTOR, "Simulated approval record"):
        assert secret not in shown
    # Acting on it gets the same answer as acting on a record that does not exist.
    messages = []
    for target in (pid, "PRO-404"):
        with pytest.raises(PermissionDeniedError) as refused:
            kpi(seeded, target, role=role)
        messages.append(str(refused.value))
    assert messages[0] == messages[1]
    # The open-pilot check cannot see it either, so it reveals nothing.
    assert gov.open_pilot(view, QUESTION) is None


def test_the_executive_sees_and_runs_the_restricted_pilot(seeded: Repository) -> None:
    pid = restricted_approval(seeded).proposal.id
    kpi(seeded, pid, role=EXEC)
    outcome = close(seeded, pid, role=EXEC)
    view = MemoryView(seeded, EXEC)
    assert view.outcome(outcome.id) == outcome
    assert MemoryView(seeded, DIRECTOR).outcome(outcome.id) is None
    assert seeded.get(SourceDocument, outcome.source_refs[0].source_id).access_roles == [EXEC]


# --- review regressions ---------------------------------------------------------------


def impact_items(repo: Repository, role: Role = DIRECTOR):
    view = MemoryView(repo, role)
    search = search_precedents(view, infer_query(QUESTION, NOW.date()))
    proposal = draft_proposal(QUESTION, "Europe", NOW.date(), NOW, surcharge_rate=0.05)
    return search, assess_impact(view, proposal, search.precedent_ids)


def test_a_new_pilot_does_not_push_the_failed_precedent_out_of_view(seeded: Repository) -> None:
    *_, before = pipeline(seeded)
    key_risks = [r.text for r in before.recommendation.reasons if r.text.startswith("Key risk")]
    assert len(key_risks) == 2
    approve(seeded)
    search, _ = impact_items(seeded)
    # DEC-017 now ranks first, so the failed Germany precedent falls below the top 5 ...
    assert search.precedent_ids[:5] == ["DEC-017", "DEC-009", "DEC-007", "DEC-004", "DEC-011"]
    # ... and is still returned, after them, as a warning.
    assert search.precedent_ids[5:] == ["DEC-001"]
    assert search.precedents[5].warning is not None
    assert "Includes 1 failed precedent(s) ranked below the top 5" in search.message
    *_, after = pipeline(seeded)
    assert [r.text for r in after.recommendation.reasons if r.text.startswith("Key risk")] == (
        key_risks
    )


def test_failed_precedents_kept_past_the_top_k_follow_the_setting(seeded: Repository) -> None:
    view = MemoryView(seeded, DIRECTOR)
    query = infer_query(QUESTION, NOW.date())
    three = search_precedents(view, query, SearchSettings(top_k=3))
    # DEC-011 (success) and DEC-001 (failed) rank 4th and 5th: only the failed one is kept.
    assert three.precedent_ids == ["DEC-009", "DEC-007", "DEC-004", "DEC-001"]
    off = search_precedents(view, query, SearchSettings(top_k=3, failed_beyond_top_k=0))
    assert off.precedent_ids == ["DEC-009", "DEC-007", "DEC-004"]
    assert search_precedents(view, query).precedent_ids == [
        "DEC-009", "DEC-007", "DEC-004", "DEC-011", "DEC-001",
    ]  # fmt: skip


def test_generated_governance_records_are_never_impact_evidence(seeded: Repository) -> None:
    _, before = impact_items(seeded)
    pid = approve(seeded).proposal.id
    kpi(seeded, pid)
    close(seeded, pid)
    search, after = impact_items(seeded)
    assert "DEC-017" in search.precedent_ids
    generated = {s.id for s in seeded.find(SourceDocument, source_type="governance_record")}
    assert generated == {"SRC-023", "SRC-024"}
    assert not {e.ref.source_id for i in after.items for e in i.evidence} & generated

    def summary(assessment):
        return {
            i.key: (i.basis, i.confidence, sorted(e.ref.source_id for e in i.evidence), i.warning)
            for i in assessment.items
        }

    assert summary(after) == summary(before)  # the pilot adds no quoted evidence of its own


@pytest.mark.parametrize(
    ("slas", "wording"),
    [
        ((SlaStatus.NOT_MODELED, SlaStatus.NOT_MODELED), "SLA not modeled."),
        ((SlaStatus.MET, SlaStatus.MET), "SLA held."),
        ((SlaStatus.NOT_MODELED, SlaStatus.MET), "SLA held in the weeks it was checked (week 2)."),
    ],
)
def test_the_sla_is_claimed_only_where_it_was_checked(slas, wording: str) -> None:
    suggestion = suggest_verdict(weeks(*[(0.2, 0.01, sla) for sla in slas]), 0.012)
    assert suggestion.verdict is OutcomeVerdict.SUCCESS
    assert suggestion.reason.endswith(wording)


def test_an_unchecked_sla_is_not_written_into_memory_as_held(seeded: Repository) -> None:
    pid = approve(seeded).proposal.id
    kpi(seeded, pid, sla=SlaStatus.NOT_MODELED)
    outcome = close(seeded, pid)
    assert outcome.sla_status is SlaStatus.NOT_MODELED
    assert "SLA held" not in outcome.explanation
    assert "SLA held" not in seeded.get(SourceDocument, outcome.source_refs[0].source_id).body


def test_a_pilot_without_a_share_uses_the_rules_default(seeded: Repository) -> None:
    record = approve(
        seeded, request=ApprovalRequest(approver_label="Dana", markets=["France", "Italy"])
    )
    # 20% default: +$14.00M x 20% / 13 = +$0.215M a week, not the whole baseline's +$1.077M.
    assert record.kpis[0].planned_revenue_uplift_musd == 0.215
