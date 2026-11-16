"""Agent 6 — Governance: simulated human approval, a 60-day tracker with weekly KPI checks,
and an outcome written back to institutional memory when the pilot closes.

Nothing here executes a business action. An approval is a stored, simulated record; KPI
values are typed in by hand in the demo; closing a pilot only writes an `Outcome`.

Each step is one transaction of audited writes (`audit.py`):
- approve: proposal, recommendation, approval, a generated approval record (source), the
  pilot decision (captured, then confirmed), its links, 4 milestones and 8 KPI slots;
- reject: proposal, recommendation and the rejection; no pilot;
- record a KPI: fills the next weekly slot, starts the pilot on week 1, updates milestones;
- close: a generated close report (source), the outcome citing it, milestones, completed.

The generated records get the same audience as the recommendation: the roles that can
see every excerpt it cites. So the pilot decision and its outcome are visible exactly
where the recommendation is, and hidden (like a missing record) everywhere else.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Annotated

from pydantic import Field, model_validator

from ..audit import (
    PermissionDeniedError,
    capture_decision,
    record_approval,
    record_kpi_update,
    record_outcome,
    review_decision,
    set_proposal_status,
    update_milestones,
)
from ..authz import ROLE_LABELS, MemoryView, can_approve
from ..models import (
    Approval,
    ApprovalRequest,
    ApprovalVerdict,
    Contract,
    Decision,
    DecisionStatus,
    EntityKind,
    KPIUpdate,
    Milestone,
    MilestoneState,
    NonEmptyStr,
    Outcome,
    OutcomeVerdict,
    PilotPlan,
    PilotRecord,
    Proposal,
    ProposalStatus,
    RecommendationResult,
    RecommendationType,
    Relation,
    RelationType,
    Role,
    ScenarioSet,
    SlaStatus,
    SourceDocument,
    SourceRef,
    SourceType,
    VerdictSuggestion,
)
from ..provenance import InaccessibleEvidenceError, verify_recommendation
from ..repository import Repository
from .base import AgentInfo

logger = logging.getLogger(__name__)

INFO = AgentInfo(
    number=6,
    key="governance",
    name="Governance",
    purpose="Records human approval, tracks the pilot and feeds outcomes back into memory.",
    reads="An approved recommendation and weekly KPI entries",
    produces="60-day tracker, milestone status and an outcome linked to the decision",
    build_stage=10,
)

GOVERNANCE_NOTE = (
    "Simulated governance: approvals, weekly KPI values and outcomes are demo records typed "
    "in here. Nothing on this tab changes a price, sends a message or opens a ticket."
)
NOTHING_TO_APPROVE = (
    "Nothing to approve: only a recommendation that names a rate (a phased pilot or proceed) "
    "can be approved or rejected."
)
# Same text as the seeded fixtures' first line, so generated records read as synthetic.
SYNTHETIC_MARKER = "[SYNTHETIC DEMO DATA: fictional company, people and numbers]"
_OPEN = (ProposalStatus.APPROVED, ProposalStatus.IN_PROGRESS)
_ACTING = (RecommendationType.PHASED_PILOT, RecommendationType.PROCEED)
_SLA_SEVERITY = {
    SlaStatus.NOT_MODELED: 0,
    SlaStatus.MET: 1,
    SlaStatus.AT_RISK: 2,
    SlaStatus.BREACHED: 3,
}
_EPS = 1e-9


class GovernanceError(ValueError):
    """A governance step was refused. The message is safe to show as is."""


# --- settings ---------------------------------------------------------------------------


class MilestoneRule(Contract):
    """One tracker milestone: due a number of days after approval, done at a KPI week or
    when the pilot closes."""

    name: NonEmptyStr
    due_day: Annotated[int, Field(ge=0)]
    done_at_week: Annotated[int, Field(ge=1, le=8)] | None = None
    done_at_close: bool = False

    @model_validator(mode="after")
    def _one_trigger(self) -> MilestoneRule:
        if (self.done_at_week is None) == (not self.done_at_close):
            raise ValueError("A milestone completes at a KPI week or at close, not both")
        return self


class GovernanceRules(Contract):
    """Demo settings for the tracker. The 60-day review follows the pilot policy (SRC-014);
    the rest are labelled demo settings."""

    kpi_weeks: Annotated[int, Field(ge=1, le=8)] = 8
    weeks_per_quarter: Annotated[int, Field(ge=1)] = 13
    review_after_days: Annotated[int, Field(ge=1)] = 60
    pilot_markets: Annotated[int, Field(ge=1)] = 2
    default_pilot_share: Annotated[float, Field(gt=0.0, le=1.0)] = 0.20
    region_wide_marker: NonEmptyStr = "(all markets)"
    milestones: tuple[MilestoneRule, ...] = (
        MilestoneRule(name="Live: week 1 KPI recorded", due_day=7, done_at_week=1),
        MilestoneRule(name="Week 4 KPI checkpoint", due_day=28, done_at_week=4),
        MilestoneRule(name="Week 8 KPI checkpoint", due_day=56, done_at_week=8),
        MilestoneRule(name="60-day executive review", due_day=60, done_at_close=True),
    )

    @model_validator(mode="after")
    def _milestones_fit(self) -> GovernanceRules:
        names = [m.name for m in self.milestones]
        if len(set(names)) != len(names):
            raise ValueError("Milestone names must be unique")
        if any(m.done_at_week and m.done_at_week > self.kpi_weeks for m in self.milestones):
            raise ValueError("A milestone cannot wait for a week beyond the KPI weeks")
        return self


DEFAULT_RULES = GovernanceRules()


def verdict_rules(rules: GovernanceRules = DEFAULT_RULES) -> list[str]:
    """The closing rule in words, for the tab. The reviewer can override it with a note."""
    return [
        (
            "Failed: average churn loss above the guardrail, or no revenue gain "
            "(actual revenue change at or below zero)."
        ),
        (
            "Success: revenue change at or above plan, average churn loss at or below plan, "
            "and no week with SLA at risk or breached."
        ),
        "Partial: anything else.",
    ]


# --- formatting -------------------------------------------------------------------------


def pct(fraction: float) -> str:
    """0.0105 -> `1.05%`."""
    return f"{round(fraction * 100, 3):g}%"


def musd(value: float, digits: int = 2) -> str:
    """Signed USD millions: `+$0.215M`, `−$1.20M`."""
    sign = "+" if value >= 0 else "−"
    return f"{sign}${abs(value):,.{digits}f}M"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _same_question(a: str, b: str) -> bool:
    return _one_line(a).casefold() == _one_line(b).casefold()


def headline(result: RecommendationResult) -> str:
    rec = result.recommendation
    kind = {
        RecommendationType.PHASED_PILOT: "phased pilot",
        RecommendationType.PROCEED: "proceed",
        RecommendationType.DO_NOT_PROCEED: "do not proceed",
        RecommendationType.INSUFFICIENT_EVIDENCE: "insufficient evidence",
    }[rec.decision_type]
    rate = rec.recommended_rate
    return f"{kind} at +{pct(rate)}" if rate is not None else kind


# --- reading ----------------------------------------------------------------------------


def pilot_market_options(
    view: MemoryView, region: str, rules: GovernanceRules = DEFAULT_RULES
) -> dict[str, str]:
    """Market name -> entity ID, for markets of visible decisions in the region."""
    regional = {decision.id for decision in view.decisions(region=region)}
    markets = {entity.id: entity for entity in view.entities(kind=EntityKind.MARKET)}
    options: dict[str, str] = {}
    for relation in view.relations(relation_type=RelationType.APPLIES_TO):
        market = markets.get(relation.to_id)
        regional_market = relation.from_id in regional and market is not None
        if regional_market and not market.name.endswith(rules.region_wide_marker):
            options[market.name] = market.id
    return dict(sorted(options.items()))


def pilot_record(view: MemoryView, proposal_id: str) -> PilotRecord | None:
    """Everything stored for one proposal that this role may see, or None."""
    proposal = view.proposal(proposal_id)
    if proposal is None:
        return None
    recommendation = view.recommendations(proposal_id=proposal.id)[-1]
    approvals = view.approvals(recommendation.id)
    decision = view.decision(proposal.decision_id) if proposal.decision_id else None
    outcomes = view.outcomes(decision_id=decision.id) if decision else []
    return PilotRecord(
        proposal=proposal,
        recommendation=recommendation,
        approval=approvals[-1] if approvals else None,
        decision=decision,
        milestones=view.milestones(proposal.id),
        kpis=view.kpi_updates(proposal.id),
        outcome=outcomes[-1] if outcomes else None,
        events=view.audit_trail(proposal.id),
    )


def pilot_records(view: MemoryView) -> list[PilotRecord]:
    """Every stored proposal this role may see, newest first."""
    records = [pilot_record(view, proposal.id) for proposal in view.proposals()]
    return sorted(
        (record for record in records if record is not None),
        key=lambda record: record.proposal.id,
        reverse=True,
    )


def pilot_for_question(view: MemoryView, question: str) -> PilotRecord | None:
    """The newest visible approved, running or completed pilot for this question."""
    for record in pilot_records(view):
        if record.proposal.status is not ProposalStatus.REJECTED and _same_question(
            record.proposal.question, question
        ):
            return record
    return None


def open_pilot(view: MemoryView, question: str) -> Proposal | None:
    """A visible approved or running pilot for the same question, if there is one."""
    for proposal in view.proposals():
        if proposal.status in _OPEN and _same_question(proposal.question, question):
            return proposal
    return None


# --- pure rules -------------------------------------------------------------------------


def plan_pilot(
    result: RecommendationResult,
    scenarios: ScenarioSet,
    request: ApprovalRequest,
    rules: GovernanceRules = DEFAULT_RULES,
) -> PilotPlan:
    """The weekly plan an approval commits to, by a visible formula. Pure."""
    rec = result.recommendation
    rate = rec.recommended_rate
    if rate is None or rec.decision_type not in _ACTING:
        raise GovernanceError(NOTHING_TO_APPROVE)
    scenario = next((r for r in scenarios.results if r.surcharge_rate == rate), None)
    if scenario is None:
        raise GovernanceError("The scenarios do not include the recommended rate.")
    chosen = next(option for option in result.options if option.chosen)
    pilot = rec.decision_type is RecommendationType.PHASED_PILOT
    if pilot and len(request.markets) != rules.pilot_markets:
        raise GovernanceError(f"Choose exactly {rules.pilot_markets} pilot markets.")
    if not pilot and request.markets:
        raise GovernanceError("A full rollout has no pilot markets.")
    share = (request.pilot_share or rules.default_pilot_share) if pilot else 1.0
    quarter = scenario.revenue_change_musd
    weekly = round(quarter * share / rules.weeks_per_quarter, 3)  # stored as displayed
    scope = "pilot share of baseline" if pilot else "share of baseline"
    formula = (
        f"planned weekly revenue change = quarterly revenue change × {scope} ÷ "
        f"{rules.weeks_per_quarter} weeks = {musd(quarter)} × {pct(share)} ÷ "
        f"{rules.weeks_per_quarter} = {musd(weekly, 3)} (rounded to $1K)"
    )
    return PilotPlan(
        surcharge_rate=rate,
        pilot=pilot,
        markets=list(request.markets),
        pilot_share=share,
        quarter_revenue_change_musd=quarter,
        planned_weekly_revenue_musd=weekly,
        planned_churn_loss_fraction=chosen.churn_loss_fraction,
        churn_guardrail=scenarios.inputs.churn_guardrail,
        weeks=rules.kpi_weeks,
        formula=formula,
    )


def kpi_flags(kpi: KPIUpdate, guardrail: float) -> list[str]:
    """Why a recorded week needs attention; empty when it is on plan. Pure."""
    actual_revenue, actual_churn = kpi.actual_revenue_uplift_musd, kpi.actual_churn_loss_fraction
    if actual_revenue is None or actual_churn is None:
        return []
    flags = []
    if actual_churn > guardrail + _EPS:
        flags.append(f"churn loss above the {pct(guardrail)} guardrail: pause and review")
    elif actual_churn > kpi.planned_churn_loss_fraction + _EPS:
        flags.append("churn loss above plan")
    if actual_revenue < kpi.planned_revenue_uplift_musd - _EPS:
        flags.append("revenue change below plan")
    if kpi.sla_status in (SlaStatus.AT_RISK, SlaStatus.BREACHED):
        flags.append(f"SLA {kpi.sla_status.value.replace('_', ' ')}")
    return flags


def milestone_states(
    milestones: Sequence[Milestone],
    kpis: Sequence[KPIUpdate],
    status: ProposalStatus,
    today: date,
    rules: GovernanceRules = DEFAULT_RULES,
) -> list[Milestone]:
    """Milestones with their state derived from the KPI record and the pilot status. Pure.

    Done when its week is recorded (or, for the review, when the pilot closes); at risk
    when a recorded week up to it is flagged or it is overdue; the first open one is in
    progress; after closing, unreached milestones go back to planned (not reached).
    """
    by_name = {rule.name: rule for rule in rules.milestones}
    recorded = [kpi for kpi in kpis if kpi.actual_revenue_uplift_musd is not None]
    weeks_done = max((kpi.week for kpi in recorded), default=0)
    flagged = [kpi.week for kpi in recorded if kpi.risk_flag]
    closed = status is ProposalStatus.COMPLETED
    first_open = True
    states = []
    for milestone in sorted(milestones, key=lambda m: (m.due_date, m.id)):
        rule = by_name.get(milestone.name)
        if rule is None:
            states.append(milestone)
            continue
        if milestone.state is MilestoneState.DONE:
            done = True
        elif rule.done_at_close:
            done = closed
        else:
            done = weeks_done >= (rule.done_at_week or 0)
        if done:
            state = MilestoneState.DONE
        elif closed:  # not reached before the pilot closed
            state = MilestoneState.PLANNED
        else:
            horizon = rule.done_at_week or rules.kpi_weeks
            overdue = milestone.due_date < today
            if any(week <= horizon for week in flagged) or overdue:
                state = MilestoneState.AT_RISK
            elif not first_open:
                state = MilestoneState.PLANNED
            else:
                state = MilestoneState.IN_PROGRESS
            first_open = False
        states.append(milestone.model_copy(update={"state": state}))
    return states


def suggest_verdict(
    kpis: Sequence[KPIUpdate], guardrail: float, rules: GovernanceRules = DEFAULT_RULES
) -> VerdictSuggestion:
    """Rule-based verdict from the recorded weeks (see `verdict_rules`). Pure."""
    recorded = [kpi for kpi in kpis if kpi.actual_revenue_uplift_musd is not None]
    if not recorded:
        raise GovernanceError("No weekly KPI has been recorded yet.")
    n = len(recorded)
    planned_total = sum(kpi.planned_revenue_uplift_musd for kpi in recorded)
    actual_total = sum(kpi.actual_revenue_uplift_musd or 0.0 for kpi in recorded)
    planned_churn = sum(kpi.planned_churn_loss_fraction for kpi in recorded) / n
    actual_churn = sum(kpi.actual_churn_loss_fraction or 0.0 for kpi in recorded) / n
    sla = max((kpi.sla_status for kpi in recorded), key=_SLA_SEVERITY.__getitem__)
    sla_weeks = [
        kpi.week for kpi in recorded if kpi.sla_status in (SlaStatus.AT_RISK, SlaStatus.BREACHED)
    ]

    if actual_churn > guardrail + _EPS:
        verdict = OutcomeVerdict.FAILED
        reason = f"Average churn loss {pct(actual_churn)} is above the {pct(guardrail)} guardrail."
    elif actual_total <= _EPS:
        verdict = OutcomeVerdict.FAILED
        reason = f"No revenue gain: actual revenue change {musd(actual_total, 3)} over {n} weeks."
    else:
        shortfalls = []
        if actual_total < planned_total - _EPS:
            shortfalls.append("revenue change below plan")
        if actual_churn > planned_churn + _EPS:
            shortfalls.append("average churn loss above plan")
        if sla_weeks:
            shortfalls.append(f"SLA at risk or breached in week {', '.join(map(str, sla_weeks))}")
        if shortfalls:
            verdict = OutcomeVerdict.PARTIAL
            reason = f"Within the guardrail with a revenue gain, but {'; '.join(shortfalls)}."
        else:
            verdict = OutcomeVerdict.SUCCESS
            reason = (
                f"Revenue change at or above plan, churn loss at or below plan, {_sla(recorded)}."
            )
    scale = rules.weeks_per_quarter / n
    return VerdictSuggestion(
        verdict=verdict,
        reason=reason,
        weeks_recorded=n,
        planned_revenue_musd=round(planned_total * scale, 2),  # stored as displayed
        actual_revenue_musd=round(actual_total * scale, 2),
        planned_churn_loss_fraction=round(planned_churn, 6),
        actual_churn_loss_fraction=round(actual_churn, 6),
        sla_status=sla,
    )


def _sla(recorded: Sequence[KPIUpdate]) -> str:
    """What the recorded weeks say about the SLA, claiming nothing that was not checked."""
    checked = [kpi for kpi in recorded if kpi.sla_status is not SlaStatus.NOT_MODELED]
    if not checked:
        return "SLA not modeled"
    if len(checked) == len(recorded):
        return "SLA held"
    weeks = ", ".join(str(kpi.week) for kpi in checked)
    return f"SLA held in the weeks it was checked (week {weeks})"


# --- generated records ------------------------------------------------------------------


def _compose(lines: Sequence[str]) -> tuple[str, list[tuple[int, int]]]:
    """Join lines into a body and return each line's (start, end) offsets."""
    spans, offset = [], 0
    for line in lines:
        spans.append((offset, offset + len(line)))
        offset += len(line) + 1
    return "\n".join(lines), spans


def _ref(source: SourceDocument, span: tuple[int, int]) -> SourceRef:
    start, end = span
    return SourceRef(
        source_id=source.id,
        excerpt=source.body[start:end],
        start_offset=start,
        end_offset=end,
    )


def _audience(repo: Repository, result: RecommendationResult) -> list[Role]:
    """Roles that can see every excerpt the recommendation cites."""
    refs = [ref for reason in result.recommendation.reasons for ref in reason.source_refs]
    return [role for role in Role if all(MemoryView(repo, role).can_cite(ref) for ref in refs)]


def _generated_source(
    repo: Repository,
    *,
    title: str,
    lines: Sequence[str],
    access_roles: list[Role],
    region: str,
    now: datetime,
) -> tuple[SourceDocument, list[tuple[int, int]]]:
    body, spans = _compose([SYNTHETIC_MARKER, *lines])
    source_id = repo.next_id(SourceDocument)
    source = repo.add(
        SourceDocument(
            id=source_id,
            source_type=SourceType.GOVERNANCE_RECORD,
            title=title,
            document_date=now.date(),
            author_label="Generated in the demo app (Agent 6)",
            body=body,
            access_roles=access_roles,
            mock_link=f"mock://governance_record/{source_id}",  # a label, not a link
            region=region,
            created_at=now,
            updated_at=now,
        )
    )
    return source, spans[1:]  # offsets of `lines`, without the marker


def _segment(proposal: Proposal) -> str:
    base = ", ".join(proposal.target_segments) or "All customers"
    if proposal.exclusions:
        return f"{base} except {', '.join(e.lower() for e in proposal.exclusions)}"
    return base


def _decision_line(proposal: Proposal, plan: PilotPlan, rules: GovernanceRules) -> str:
    rate = f"+{pct(plan.surcharge_rate)}"
    excluding = (
        f", excluding {', '.join(e.lower() for e in proposal.exclusions)}"
        if proposal.exclusions
        else ""
    )
    if plan.pilot:
        return (
            f"Decision: run a {rate} freight surcharge as a phased pilot in "
            f"{' and '.join(plan.markets)} ({proposal.region}){excluding}; wider rollout only "
            f"after the {rules.review_after_days}-day executive review."
        )
    return (
        f"Decision: apply a {rate} freight surcharge in {proposal.region} from "
        f"{proposal.effective_quarter}{excluding}; executive review after "
        f"{rules.review_after_days} days."
    )


# --- writes -----------------------------------------------------------------------------


def _check_approvable(
    view: MemoryView, draft: Proposal, result: RecommendationResult, scenarios: ScenarioSet
) -> None:
    if not can_approve(view.role):
        raise PermissionDeniedError("This demo role cannot approve or reject recommendations.")
    rec = result.recommendation
    if rec.recommended_rate is None or rec.decision_type not in _ACTING:
        raise GovernanceError(NOTHING_TO_APPROVE)
    if draft.status is not ProposalStatus.DRAFT or rec.proposal_id != draft.id:
        raise GovernanceError("This recommendation was not made for this draft proposal.")
    if [r.surcharge_rate for r in scenarios.results] != sorted(draft.surcharge_options):
        raise GovernanceError("The scenarios were not computed for this proposal's options.")
    try:
        verify_recommendation(view, rec)
    except InaccessibleEvidenceError:
        raise PermissionDeniedError("This recommendation is not available to this role.") from None
    existing = open_pilot(view, draft.question)
    if existing is not None:
        raise GovernanceError(f"A pilot for this question is already open ({existing.id}).")


def _store_proposal(
    repo: Repository,
    draft: Proposal,
    result: RecommendationResult,
    now: datetime,
    *,
    guardrail: float | None = None,
) -> tuple[Proposal, str]:
    proposal = repo.add(
        draft.model_copy(
            update={
                "id": repo.next_id(Proposal),
                "status": ProposalStatus.DRAFT,
                "churn_guardrail": guardrail,
                "created_at": now,
                "updated_at": now,
            }
        )
    )
    recommendation = repo.add(
        result.recommendation.model_copy(
            update={
                "id": repo.next_id(type(result.recommendation)),
                "proposal_id": proposal.id,
                "created_at": now,
                "updated_at": now,
            }
        )
    )
    return proposal, recommendation.id


def approve_recommendation(
    repo: Repository,
    *,
    draft: Proposal,
    result: RecommendationResult,
    scenarios: ScenarioSet,
    request: ApprovalRequest,
    role: Role,
    now: datetime,
    rules: GovernanceRules = DEFAULT_RULES,
) -> PilotRecord:
    """Record a simulated approval and open the 60-day tracker. One transaction."""
    view = MemoryView(repo, role)
    _check_approvable(view, draft, result, scenarios)
    if result.required_exclusion and not result.exclusion_in_proposal:
        raise GovernanceError(
            f"The recommendation holds only if {result.required_exclusion.lower()} are "
            "excluded. Tick the exclusion in tab 3 before approving."
        )
    plan = plan_pilot(result, scenarios, request, rules)
    market_ids = pilot_market_options(view, draft.region, rules)
    unknown = [market for market in plan.markets if market not in market_ids]
    if unknown:
        raise GovernanceError("Choose pilot markets from the list.")
    approver = request.approver_label
    actor = {"actor_label": approver, "role": role, "now": now}

    with repo.transaction():
        _check_approvable(view, draft, result, scenarios)  # again, inside the transaction
        proposal, rec_id = _store_proposal(repo, draft, result, now, guardrail=plan.churn_guardrail)
        approval = record_approval(
            repo,
            Approval(
                id=repo.next_id(Approval),
                recommendation_id=rec_id,
                approver_label=approver,
                role=role,
                verdict=ApprovalVerdict.APPROVED,
                chosen_rate=plan.surcharge_rate,
                reasons=_one_line(request.note),
                decided_at=now,
                created_at=now,
                updated_at=now,
            ),
            now=now,
        )
        audience = _audience(repo, result)
        decision_line = _decision_line(proposal, plan, rules)
        approved_line = (
            f"Approved by: {approver} ({ROLE_LABELS[role]}, demo role) on {now.date().isoformat()}."
        )
        risk = result.recommendation.risk_level
        lines = [
            "Simulated approval record, generated in this app. No business action was executed.",
            f"Question: {_one_line(proposal.question)}",
            (
                f"Recommendation: {rec_id}, {headline(result)}, risk level "
                f"{risk.value if risk else 'not rated'}."
            ),
            decision_line,
            (
                f"Plan: churn loss at most {pct(plan.planned_churn_loss_fraction)} of baseline "
                f"revenue each week (pause and review above the {pct(plan.churn_guardrail)} "
                f"guardrail); {plan.formula}."
            ),
            approved_line,
        ]
        if request.note:
            lines.append(f"Approver note: {_one_line(request.note)}")
        source, spans = _generated_source(
            repo,
            title=f"Simulated approval record for {proposal.id}",
            lines=lines,
            access_roles=audience,
            region=proposal.region,
            now=now,
        )
        others = [o for o in result.options if o.surcharge_rate != plan.surcharge_rate]
        scope = "the pilot markets" if plan.pilot else proposal.region
        draft_decision = Decision(
            id=repo.next_id(Decision),
            title=(
                f"{proposal.region}: +{pct(plan.surcharge_rate)} surcharge pilot in "
                f"{' and '.join(plan.markets)}"
                if plan.pilot
                else f"{proposal.region}: +{pct(plan.surcharge_rate)} surcharge from "
                f"{proposal.effective_quarter}"
            ),
            statement=_sentence(decision_line.removeprefix("Decision: ")),
            decision_date=now.date(),
            owner=approver,
            region=proposal.region,
            market=" and ".join(plan.markets)
            if plan.pilot
            else f"{proposal.region} {rules.region_wide_marker}",
            customer_segment=_segment(proposal),
            surcharge_rate=plan.surcharge_rate,
            rationale=(
                f"Advisory recommendation {rec_id} ({headline(result)}) approved by a named "
                "human approver; its reasons cite the precedents and policies behind it."
            ),
            alternatives=[f"+{pct(o.surcharge_rate)}: {o.verdict}" for o in others],
            expected_outcome=(
                f"Planned: revenue change {musd(plan.planned_weekly_revenue_musd, 3)} a week in "
                f"{scope}, churn loss {pct(plan.planned_churn_loss_fraction)} of baseline "
                f"revenue (guardrail {pct(plan.churn_guardrail)}). Scenario figure for the "
                f"whole baseline: {musd(plan.quarter_revenue_change_musd)} a quarter."
            ),
            status=DecisionStatus.DRAFT,
            source_refs=[_ref(source, spans[3]), _ref(source, spans[5])],
            created_at=now,
            updated_at=now,
        )
        captured = capture_decision(repo, draft_decision, **actor)
        decision = review_decision(
            repo,
            captured.model_copy(update={"status": DecisionStatus.CONFIRMED}),
            note=f"Recorded from simulated approval {approval.id}.",
            **actor,
        )
        links = [
            (d, RelationType.INFORMED_BY, f"Precedent used by {rec_id}.")
            for d in dict.fromkeys(result.precedent_ids)
            if view.decision(d)
        ]
        links += [(market_ids[m], RelationType.APPLIES_TO, "Pilot market chosen by the approver.")
                  for m in plan.markets]  # fmt: skip
        for to_id, relation_type, note in links:
            repo.add(
                Relation(
                    id=repo.next_id(Relation),
                    from_id=decision.id,
                    to_id=to_id,
                    relation_type=relation_type,
                    note=note,
                    created_at=now,
                    updated_at=now,
                )
            )
        repo.save(proposal.model_copy(update={"decision_id": decision.id, "updated_at": now}))
        start = now.date()
        milestones = [
            repo.add(
                Milestone(
                    id=repo.next_id(Milestone),
                    proposal_id=proposal.id,
                    name=rule.name,
                    due_date=start + timedelta(days=rule.due_day),
                    created_at=now,
                    updated_at=now,
                )
            )
            for rule in rules.milestones
        ]
        for week in range(1, plan.weeks + 1):
            repo.add(
                KPIUpdate(
                    id=repo.next_id(KPIUpdate),
                    proposal_id=proposal.id,
                    week=week,
                    planned_revenue_uplift_musd=plan.planned_weekly_revenue_musd,
                    planned_churn_loss_fraction=plan.planned_churn_loss_fraction,
                    created_at=now,
                    updated_at=now,
                )
            )
        set_proposal_status(
            repo,
            proposal.id,
            ProposalStatus.APPROVED,
            detail=(
                f"Simulated approval {approval.id}; decision {decision.id} recorded; tracker "
                f"created with {len(milestones)} milestones and {plan.weeks} weekly KPI slots."
            ),
            **actor,
        )
        states = milestone_states(milestones, [], ProposalStatus.APPROVED, start, rules)
        update_milestones(repo, states, **actor)
    logger.info("Pilot approved: %s", proposal.id)
    record = pilot_record(view, proposal.id)
    assert record is not None  # the approver can see what they just approved
    return record


def reject_recommendation(
    repo: Repository,
    *,
    draft: Proposal,
    result: RecommendationResult,
    scenarios: ScenarioSet,
    request: ApprovalRequest,
    role: Role,
    now: datetime,
) -> PilotRecord:
    """Record a simulated rejection. No pilot, decision or tracker is created."""
    view = MemoryView(repo, role)
    _check_approvable(view, draft, result, scenarios)
    actor = {"actor_label": request.approver_label, "role": role, "now": now}
    with repo.transaction():
        _check_approvable(view, draft, result, scenarios)
        proposal, rec_id = _store_proposal(repo, draft, result, now)
        approval = record_approval(
            repo,
            Approval(
                id=repo.next_id(Approval),
                recommendation_id=rec_id,
                approver_label=request.approver_label,
                role=role,
                verdict=ApprovalVerdict.REJECTED,
                reasons=_one_line(request.note),
                decided_at=now,
                created_at=now,
                updated_at=now,
            ),
            now=now,
        )
        set_proposal_status(
            repo,
            proposal.id,
            ProposalStatus.REJECTED,
            detail=f"Simulated rejection {approval.id}; no pilot was created.",
            **actor,
        )
    logger.info("Recommendation rejected: %s", proposal.id)
    record = pilot_record(view, proposal.id)
    assert record is not None
    return record


def _visible_record(view: MemoryView, proposal_id: str) -> PilotRecord:
    record = pilot_record(view, proposal_id)
    if record is None:  # hidden and missing get the same answer
        raise PermissionDeniedError("This pilot is not available to this role.")
    return record


def record_kpi(
    repo: Repository,
    *,
    proposal_id: str,
    week: int,
    actual_revenue_musd: float,
    actual_churn_loss_fraction: float,
    sla_status: SlaStatus,
    note: str,
    actor_label: str,
    role: Role,
    now: datetime,
    rules: GovernanceRules = DEFAULT_RULES,
) -> KPIUpdate:
    """Fill the next weekly KPI slot with demo-entered values. Week 1 starts the pilot."""
    view = MemoryView(repo, role)
    actor_label = _one_line(actor_label)
    if not actor_label:
        raise GovernanceError("Enter your name for the audit trail.")
    actor = {"actor_label": actor_label, "role": role, "now": now}
    with repo.transaction():
        record = _visible_record(view, proposal_id)
        proposal = record.proposal
        if proposal.status not in _OPEN:
            raise GovernanceError(
                f"This pilot is {proposal.status.value.replace('_', ' ')}; weekly KPIs can be "
                "recorded only while it is approved or in progress."
            )
        if record.next_week is None:
            raise GovernanceError("Every weekly KPI slot is recorded. Close the pilot.")
        if week != record.next_week:
            raise GovernanceError(f"The next week to record is week {record.next_week}.")
        slot = next(kpi for kpi in record.kpis if kpi.week == week)
        filled = KPIUpdate.model_validate(
            {
                **slot.model_dump(),
                "actual_revenue_uplift_musd": actual_revenue_musd,
                "actual_churn_loss_fraction": actual_churn_loss_fraction,
                "sla_status": sla_status,
                "note": _one_line(note),
            }
        )
        guardrail = proposal.churn_guardrail
        if guardrail is None:
            raise GovernanceError("This pilot has no churn guardrail on record.")
        filled = filled.model_copy(update={"risk_flag": bool(kpi_flags(filled, guardrail))})
        stored = record_kpi_update(repo, filled, **actor)
        status = proposal.status
        if status is ProposalStatus.APPROVED:
            status = set_proposal_status(
                repo,
                proposal.id,
                ProposalStatus.IN_PROGRESS,
                detail="Pilot started: first weekly KPI recorded.",
                **actor,
            ).status
        kpis = [stored if kpi.week == week else kpi for kpi in record.kpis]
        update_milestones(
            repo, milestone_states(record.milestones, kpis, status, now.date(), rules), **actor
        )
    return stored


def close_pilot(
    repo: Repository,
    *,
    proposal_id: str,
    verdict: OutcomeVerdict,
    note: str,
    actor_label: str,
    role: Role,
    now: datetime,
    rules: GovernanceRules = DEFAULT_RULES,
) -> Outcome:
    """Hold the executive review: write the outcome back to memory and complete the pilot."""
    if not can_approve(role):
        raise PermissionDeniedError("This demo role cannot close a pilot.")
    view = MemoryView(repo, role)
    actor_label, note = _one_line(actor_label), _one_line(note)
    if not actor_label:
        raise GovernanceError("Enter your name for the audit trail.")
    actor = {"actor_label": actor_label, "role": role, "now": now}
    with repo.transaction():
        record = _visible_record(view, proposal_id)
        proposal, decision = record.proposal, record.decision
        if proposal.status is not ProposalStatus.IN_PROGRESS or decision is None:
            raise GovernanceError(
                "A pilot can be closed only while it is in progress, after at least one "
                "weekly KPI is recorded."
            )
        guardrail = proposal.churn_guardrail
        if guardrail is None:
            raise GovernanceError("This pilot has no churn guardrail on record.")
        suggestion = suggest_verdict(record.kpis, guardrail, rules)
        if verdict is not suggestion.verdict and not note:
            raise GovernanceError(
                "Add a note explaining why the verdict differs from the rule's suggestion."
            )
        audience = sorted(
            set.intersection(
                *(
                    set(repo.get(SourceDocument, ref.source_id).access_roles)  # type: ignore[union-attr]
                    for ref in decision.source_refs
                )
            )
        )
        n, weeks = suggestion.weeks_recorded, len(record.kpis)
        pilot = record.recommendation.decision_type is RecommendationType.PHASED_PILOT
        scope = "pilot markets" if pilot else "whole baseline"
        week_lines = [
            f"Week {kpi.week}: revenue change {musd(kpi.actual_revenue_uplift_musd or 0.0, 3)} "
            f"against {musd(kpi.planned_revenue_uplift_musd, 3)} planned; churn loss "
            f"{pct(kpi.actual_churn_loss_fraction or 0.0)} against "
            f"{pct(kpi.planned_churn_loss_fraction)} planned; SLA "
            f"{kpi.sla_status.value.replace('_', ' ')}." + (" Flagged." if kpi.risk_flag else "")
            for kpi in record.recorded
        ]
        result_line = (
            f"Result over {n} of {weeks} weeks ({scope}, quarter-equivalent: weekly average × "
            f"{rules.weeks_per_quarter}): revenue change {musd(suggestion.actual_revenue_musd)} "
            f"against {musd(suggestion.planned_revenue_musd)} planned; average churn loss "
            f"{pct(suggestion.actual_churn_loss_fraction)} against "
            f"{pct(suggestion.planned_churn_loss_fraction)} planned "
            f"(guardrail {pct(guardrail)})."
        )
        verdict_line = (
            f"Verdict: {verdict.value}, set at the executive review by {actor_label} "
            f"({ROLE_LABELS[role]}, demo role); the rule suggested {suggestion.verdict.value}: "
            f"{suggestion.reason}"
        )
        lines = [
            (
                "Close report, generated in this app from demo-entered weekly KPIs. "
                "No business action was executed."
            ),
            (
                f"Tracked: {proposal.id}, decision {decision.id}, "
                f"+{pct(decision.surcharge_rate or 0.0)} in {decision.market}."
            ),
            *week_lines,
            result_line,
            verdict_line,
        ]
        if note:
            lines.append(f"Reviewer note: {note}")
        source, spans = _generated_source(
            repo,
            title=f"Close report for {proposal.id}",
            lines=lines,
            access_roles=audience,
            region=proposal.region,
            now=now,
        )
        result_at = 2 + len(week_lines)
        overridden = (
            f" Verdict set by the reviewer; the rule suggested {suggestion.verdict.value}."
            if verdict is not suggestion.verdict
            else ""
        )
        outcome = record_outcome(
            repo,
            Outcome(
                id=repo.next_id(Outcome),
                decision_id=decision.id,
                verdict=verdict,
                observed_on=now.date(),
                expected_revenue_change_musd=suggestion.planned_revenue_musd,
                actual_revenue_change_musd=suggestion.actual_revenue_musd,
                expected_churn_loss_fraction=suggestion.planned_churn_loss_fraction,
                actual_churn_loss_fraction=suggestion.actual_churn_loss_fraction,
                sla_status=suggestion.sla_status,
                explanation=(
                    f"Closed after {n} of {weeks} weekly checks (demo-entered values, "
                    f"{scope}, quarter-equivalent). {suggestion.reason}{overridden}"
                    + (f" Reviewer note: {note}" if note else "")
                ),
                source_refs=[_ref(source, spans[result_at]), _ref(source, spans[result_at + 1])],
                created_at=now,
                updated_at=now,
            ),
            **actor,
        )
        set_proposal_status(
            repo,
            proposal.id,
            ProposalStatus.COMPLETED,
            detail=f"Executive review held; outcome {outcome.id} recorded on {decision.id}.",
            **actor,
        )
        update_milestones(
            repo,
            milestone_states(
                record.milestones, record.kpis, ProposalStatus.COMPLETED, now.date(), rules
            ),
            **actor,
        )
    logger.info("Pilot closed: %s", proposal.id)
    return outcome
