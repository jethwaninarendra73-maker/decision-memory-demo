"""Governance view (Agent 6): simulated approval of the tab 5 recommendation, the 60-day
tracker with weekly KPI entries, the executive review that writes an outcome back to
memory, and the audit trail.

Writes go through `governance_agent`, which stores everything through `audit.py`.
Text typed by users and generated records is shown in tables or Markdown-escaped.
Nothing on this tab changes a price, sends a message or opens a ticket.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st
from pydantic import ValidationError

from decision_memory.agents.governance_agent import (
    DEFAULT_RULES,
    GOVERNANCE_NOTE,
    NOTHING_TO_APPROVE,
    GovernanceError,
    GovernanceRules,
    approve_recommendation,
    close_pilot,
    kpi_flags,
    musd,
    open_pilot,
    pct,
    pilot_market_options,
    pilot_records,
    plan_pilot,
    record_kpi,
    reject_recommendation,
    suggest_verdict,
    verdict_rules,
)
from decision_memory.audit import InvalidTransitionError, PermissionDeniedError
from decision_memory.authz import ROLE_LABELS, MemoryView, can_approve
from decision_memory.models import (
    ApprovalRequest,
    MilestoneState,
    OutcomeVerdict,
    PilotRecord,
    Proposal,
    ProposalStatus,
    RecommendationResult,
    RecommendationType,
    ScenarioSet,
    SlaStatus,
)
from decision_memory.provenance import escape_markdown, resolve_ref
from decision_memory.repository import Repository
from decision_memory.ui.recommendation import headline

ACTOR_KEY = "gov_actor"
MARKETS_KEY = "gov_markets"
SHARE_KEY = "gov_share_pct"
NOTE_KEY = "gov_note"
APPROVE_KEY = "gov_approve"
REJECT_KEY = "gov_reject"
PILOT_KEY = "gov_pilot"
NOTICE_KEY = "gov_notice"

NO_RECOMMENDATION = (
    "Ask a question in tab 2 · Institutional Memory first. Approval works on the "
    "recommendation in tab 5."
)
NAME_NEEDED = "Enter your name above to record an approval, a KPI entry or a review."
NO_RECORDS = (
    "No approved or rejected recommendations yet. Approve one above to open a 60-day tracker."
)
REFUSALS = (GovernanceError, PermissionDeniedError, InvalidTransitionError)
INVALID_INPUT = "Some inputs are not valid. Check the name, markets, share and note."


def kpi_key(field: str, proposal_id: str, week: int) -> str:
    """Widget key for one KPI input, fresh for every week."""
    return f"gov_kpi_{field}_{proposal_id}_{week}"


def close_key(field: str, proposal_id: str) -> str:
    return f"gov_close_{field}_{proposal_id}"


def verdict_key(proposal_id: str, weeks_recorded: int) -> str:
    """The verdict selector's key. It changes with every recorded week, so the selector
    starts again from the rule's current suggestion instead of keeping an old choice."""
    return f"gov_close_verdict_{proposal_id}_w{weeks_recorded}"


def _done(notice: str, proposal_id: str | None = None) -> None:
    """Show `notice` after a rerun. Pass `proposal_id` only before the record selector
    has been drawn in this run (Streamlit refuses to change a drawn widget's value)."""
    st.session_state[NOTICE_KEY] = notice
    if proposal_id is not None:
        st.session_state[PILOT_KEY] = proposal_id
    st.rerun()  # so every tab, the graph and search included, reads the new records


# --- approval ------------------------------------------------------------------------


def condition_unmet(result: RecommendationResult) -> str | None:
    if result.required_exclusion and not result.exclusion_in_proposal:
        return (
            f"Approval is blocked: the recommendation holds only if "
            f"{result.required_exclusion.lower()} are excluded. Tick the exclusion in tab 3."
        )
    return None


def render_approval(
    repo: Repository,
    view: MemoryView,
    proposal: Proposal | None,
    scenarios: ScenarioSet | None,
    result: RecommendationResult | None,
    actor: str,
    now: datetime,
    rules: GovernanceRules,
) -> None:
    st.markdown("#### 1 · Human approval (simulated)")
    if proposal is None or scenarios is None or result is None:
        st.info(NO_RECOMMENDATION)
        return
    rec = result.recommendation
    if rec.recommended_rate is None:
        st.info(NOTHING_TO_APPROVE)
        return
    existing = open_pilot(view, proposal.question)
    if existing is not None:
        st.info(
            f"This question already has an open pilot: {existing.id} "
            f"({existing.status.value.replace('_', ' ')}). Track it below."
        )
        return
    st.markdown(f"Recommendation to decide on (tab 5): **{escape_markdown(headline(rec))}**")
    if not can_approve(view.role):
        st.info(
            f"The {ROLE_LABELS[view.role]} demo role cannot approve or reject. Switch the demo "
            "role to Pricing Director to record a decision (a demonstration control, not "
            "authentication)."
        )
        return

    pilot = rec.decision_type is RecommendationType.PHASED_PILOT
    markets: list[str] = []
    share = 1.0
    if pilot:
        options = list(pilot_market_options(view, proposal.region, rules))
        markets = st.multiselect(
            f"Pilot markets (choose {rules.pilot_markets})",
            options,
            key=MARKETS_KEY,
            max_selections=rules.pilot_markets,
        )
        share = (
            st.number_input(
                "Pilot markets' share of baseline revenue (%)",
                min_value=1.0,
                max_value=100.0,
                value=rules.default_pilot_share * 100,
                step=1.0,
                format="%.0f",
                key=SHARE_KEY,
            )
            / 100
        )
        st.caption(
            "The pilot share is assumed for demo: it scales the scenario figure (whole "
            "baseline) to the pilot markets for the weekly plan."
        )
    note = st.text_area("Approver note (optional)", key=NOTE_KEY, max_chars=500)

    blocked = condition_unmet(result)
    if blocked:
        st.warning(blocked, icon="⚠️")
    request: ApprovalRequest | None = None
    try:
        request = ApprovalRequest(
            approver_label=actor or "?", markets=markets, pilot_share=share, note=note
        )
        plan = plan_pilot(result, scenarios, request, rules)
        st.caption(f"Weekly plan if approved: {plan.formula}.")
    except (ValidationError, GovernanceError) as exc:
        if pilot and len(markets) != rules.pilot_markets:
            st.caption(f"Choose {rules.pilot_markets} pilot markets to see the weekly plan.")
        elif isinstance(exc, GovernanceError):
            st.caption(str(exc))
    if not actor:
        st.caption(NAME_NEEDED)

    ready = bool(actor) and request is not None
    approve, reject = st.columns(2)
    with approve:
        clicked_approve = st.button(
            "Approve (simulated)",
            key=APPROVE_KEY,
            type="primary",
            disabled=not ready
            or blocked is not None
            or (pilot and len(markets) != rules.pilot_markets),
        )
    with reject:
        clicked_reject = st.button("Reject (simulated)", key=REJECT_KEY, disabled=not ready)
    if not (clicked_approve or clicked_reject):
        return
    try:
        request = ApprovalRequest(
            approver_label=actor, markets=markets, pilot_share=share, note=note
        )
        if clicked_approve:
            record = approve_recommendation(
                repo,
                draft=proposal,
                result=result,
                scenarios=scenarios,
                request=request,
                role=view.role,
                now=now,
                rules=rules,
            )
        else:
            record = reject_recommendation(
                repo,
                draft=proposal,
                result=result,
                scenarios=scenarios,
                request=request,
                role=view.role,
                now=now,
            )
    except REFUSALS as exc:
        st.error(str(exc))
        return
    except ValidationError:
        st.error(INVALID_INPUT)
        return
    if record.decision is not None:
        _done(
            f"Simulated approval recorded: {record.proposal.id}. Decision {record.decision.id} "
            "is now in institutional memory (tab 2) and the 60-day tracker is open below. "
            "Nothing was executed.",
            record.proposal.id,
        )
    else:
        _done(
            f"Simulated rejection recorded: {record.proposal.id}. No pilot was created.",
            record.proposal.id,
        )


# --- tracker -------------------------------------------------------------------------


def _status(status: ProposalStatus) -> str:
    return status.value.replace("_", " ")


def record_rows(records: list[PilotRecord]) -> list[dict[str, str]]:
    return [
        {
            "Proposal": r.proposal.id,
            "Status": _status(r.proposal.status),
            "Recommendation": f"{r.recommendation.id} ({headline(r.recommendation)})",
            "Decided by": (
                f"{r.approval.approver_label} ({ROLE_LABELS[r.approval.role]}): "
                f"{r.approval.verdict.value}"
                if r.approval
                else "—"
            ),
            "Decision in memory": r.decision.id if r.decision else "none",
            "Outcome": f"{r.outcome.id} ({r.outcome.verdict.value})" if r.outcome else "none",
            "Question": r.proposal.question,
        }
        for r in records
    ]


def milestone_rows(record: PilotRecord) -> list[dict[str, str]]:
    closed = record.proposal.status is ProposalStatus.COMPLETED
    return [
        {
            "Milestone": m.name,
            "Due": m.due_date.isoformat(),
            "State": (
                "not reached (closed early)"
                if closed and m.state is MilestoneState.PLANNED
                else m.state.value.replace("_", " ")
            ),
        }
        for m in record.milestones
    ]


def kpi_rows(record: PilotRecord) -> list[dict[str, str]]:
    guardrail = record.proposal.churn_guardrail or 0.0
    rows = []
    for kpi in record.kpis:
        recorded = kpi.actual_revenue_uplift_musd is not None
        rows.append(
            {
                "Week": str(kpi.week),
                "Planned revenue change": musd(kpi.planned_revenue_uplift_musd, 3),
                "Actual revenue change": (
                    musd(kpi.actual_revenue_uplift_musd or 0.0, 3) if recorded else "open"
                ),
                "Planned churn loss": pct(kpi.planned_churn_loss_fraction),
                "Actual churn loss": (
                    pct(kpi.actual_churn_loss_fraction or 0.0) if recorded else "open"
                ),
                "SLA": kpi.sla_status.value.replace("_", " ") if recorded else "",
                "Flags": "; ".join(kpi_flags(kpi, guardrail)) or ("none" if recorded else ""),
                "Note": kpi.note,
            }
        )
    return rows


def event_rows(record: PilotRecord) -> list[dict[str, str]]:
    return [
        {
            "Event": e.id,
            "At (UTC)": e.at.strftime("%Y-%m-%d %H:%M"),
            "Actor": e.actor_label,
            "Role": ROLE_LABELS[e.role] if e.role else "system",
            "Action": e.action.value,
            "Record": e.entity_id,
            "Detail": e.detail,
        }
        for e in record.events
    ]


def _citations(view: MemoryView, refs) -> str:
    cited = [resolve_ref(view, ref) for ref in refs]
    return "\n".join(f"- {c.format_markdown()}" for c in cited if c is not None)


def render_kpi_entry(
    repo: Repository,
    view: MemoryView,
    record: PilotRecord,
    actor: str,
    now: datetime,
    rules: GovernanceRules,
) -> None:
    week = record.next_week
    if week is None:
        st.caption("Every weekly KPI slot is recorded. Close the pilot below.")
        return
    pid = record.proposal.id
    slot = next(kpi for kpi in record.kpis if kpi.week == week)
    st.markdown(f"**Record week {week}** (demo-entered values, synthetic; prefilled with the plan)")
    revenue, churn, sla = st.columns(3)
    with revenue:
        actual_revenue = st.number_input(
            "Actual revenue change ($M, this week)",
            value=slot.planned_revenue_uplift_musd,
            step=0.005,
            format="%.3f",
            key=kpi_key("revenue", pid, week),
        )
    with churn:
        churn_pct = st.number_input(
            "Actual churn loss (% of baseline)",
            min_value=0.0,
            max_value=100.0,
            value=slot.planned_churn_loss_fraction * 100,
            step=0.1,
            format="%.2f",
            key=kpi_key("churn", pid, week),
        )
    with sla:
        sla_status = st.selectbox(
            "SLA",
            [status.value for status in SlaStatus],
            index=[status for status in SlaStatus].index(SlaStatus.NOT_MODELED),
            format_func=lambda value: value.replace("_", " "),
            key=kpi_key("sla", pid, week),
        )
    note = st.text_input("Note (optional)", key=kpi_key("note", pid, week), max_chars=200)
    if not st.button(
        f"Record week {week} KPI", key=kpi_key("record", pid, week), disabled=not actor
    ):
        return
    try:
        stored = record_kpi(
            repo,
            proposal_id=pid,
            week=week,
            actual_revenue_musd=round(actual_revenue, 3),
            actual_churn_loss_fraction=round(churn_pct / 100, 5),
            sla_status=SlaStatus(sla_status),
            note=note,
            actor_label=actor,
            role=view.role,
            now=now,
            rules=rules,
        )
    except REFUSALS as exc:
        st.error(str(exc))
        return
    except ValidationError:
        st.error("The KPI values are not valid.")
        return
    flags = kpi_flags(stored, record.proposal.churn_guardrail or 0.0)
    _done(
        f"Week {week} KPI recorded for {pid}."
        + (f" Flagged: {'; '.join(flags)}." if flags else " On plan.")
    )


def render_close(
    repo: Repository,
    view: MemoryView,
    record: PilotRecord,
    actor: str,
    now: datetime,
    rules: GovernanceRules,
) -> None:
    pid = record.proposal.id
    guardrail = record.proposal.churn_guardrail or 0.0
    suggestion = suggest_verdict(record.kpis, guardrail, rules)
    st.markdown("**Executive review: close the pilot and write the outcome to memory**")
    st.markdown(
        f"Rule suggests **{suggestion.verdict.value}**: {escape_markdown(suggestion.reason)}"
    )
    st.caption(
        f"Over {suggestion.weeks_recorded} recorded weeks, quarter-equivalent (weekly average × "
        f"{rules.weeks_per_quarter}): revenue change {musd(suggestion.actual_revenue_musd)} "
        f"against {musd(suggestion.planned_revenue_musd)} planned; average churn loss "
        f"{pct(suggestion.actual_churn_loss_fraction)} against "
        f"{pct(suggestion.planned_churn_loss_fraction)} planned (guardrail {pct(guardrail)})."
    )
    st.caption("Rule (a demo setting): " + " ".join(verdict_rules(rules)))
    if suggestion.weeks_recorded < len(record.kpis):
        st.caption(
            f"Closing now ends the pilot after {suggestion.weeks_recorded} of "
            f"{len(record.kpis)} weekly checks: the outcome rests on that much demo-entered data."
        )
    if not can_approve(view.role):
        st.info(
            f"The {ROLE_LABELS[view.role]} demo role cannot hold the executive review. Switch "
            "the demo role to Pricing Director to close the pilot."
        )
        return
    verdicts = [verdict.value for verdict in OutcomeVerdict]
    verdict = st.selectbox(
        "Verdict (the reviewer decides; differing from the rule needs a note)",
        verdicts,
        index=verdicts.index(suggestion.verdict.value),
        key=verdict_key(pid, suggestion.weeks_recorded),
    )
    note = st.text_area("Review note", key=close_key("note", pid), max_chars=500)
    if not st.button(
        "Close the pilot and write the outcome to memory",
        key=close_key("submit", pid),
        type="primary",
        disabled=not actor,
    ):
        return
    try:
        outcome = close_pilot(
            repo,
            proposal_id=pid,
            verdict=OutcomeVerdict(verdict),
            note=note,
            actor_label=actor,
            role=view.role,
            now=now,
            rules=rules,
        )
    except REFUSALS as exc:
        st.error(str(exc))
        return
    _done(
        f"Pilot {pid} closed. Outcome {outcome.id} ({outcome.verdict.value}) is now attached to "
        f"{outcome.decision_id}: see it in tab 2's graph and precedent search."
    )


def render_pilot(
    repo: Repository,
    view: MemoryView,
    record: PilotRecord,
    actor: str,
    now: datetime,
    rules: GovernanceRules,
) -> None:
    proposal, decision = record.proposal, record.decision
    st.markdown(
        f"**{proposal.id}** · status **{_status(proposal.status)}** · recommendation "
        f"{record.recommendation.id} ({escape_markdown(headline(record.recommendation))})"
    )
    if record.approval is not None:
        st.caption(
            f"{record.approval.verdict.value.capitalize()} by "
            f"{escape_markdown(record.approval.approver_label)} "
            f"({ROLE_LABELS[record.approval.role]}, demo role) on "
            f"{record.approval.decided_at.date().isoformat()}. Simulated: nothing was executed."
        )
    if decision is None:
        if proposal.status is ProposalStatus.REJECTED:
            st.caption("Rejected: no pilot, decision or tracker was created.")
    else:
        st.markdown(
            f"Decision in memory: **{decision.id}** · {escape_markdown(decision.title)}\n"
            + _citations(view, decision.source_refs)
        )
        if record.kpis:
            slot = record.kpis[0]
            st.caption(
                f"Weekly plan, fixed at approval: revenue change "
                f"{musd(slot.planned_revenue_uplift_musd, 3)}, churn loss "
                f"{pct(slot.planned_churn_loss_fraction)} of baseline "
                f"(pause and review above the {pct(proposal.churn_guardrail or 0.0)} guardrail)."
            )
        st.markdown("**Milestones** (60-day tracker)")
        st.dataframe(milestone_rows(record), hide_index=True)
        st.markdown("**Weekly KPI checks**")
        st.dataframe(kpi_rows(record), hide_index=True)
        if proposal.status in (ProposalStatus.APPROVED, ProposalStatus.IN_PROGRESS):
            render_kpi_entry(repo, view, record, actor, now, rules)
        if proposal.status is ProposalStatus.IN_PROGRESS:
            st.divider()
            render_close(repo, view, record, actor, now, rules)
        if record.outcome is not None:
            outcome = record.outcome
            st.success(
                f"Outcome {outcome.id} ({outcome.verdict.value}) recorded on {decision.id} on "
                f"{outcome.observed_on.isoformat()}. It now shows in tab 2's graph and "
                "precedent search.",
                icon="✅",
            )
            st.markdown(
                escape_markdown(outcome.explanation) + "\n" + _citations(view, outcome.source_refs)
            )
    with st.expander("Audit trail for this record"):
        st.dataframe(event_rows(record), hide_index=True)


def render_tracker(
    repo: Repository,
    view: MemoryView,
    actor: str,
    now: datetime,
    rules: GovernanceRules,
) -> None:
    st.markdown("#### 2 · Pilot tracker")
    records = pilot_records(view)
    if not records:
        st.info(NO_RECORDS)
        return
    st.dataframe(record_rows(records), hide_index=True)
    ids = [record.proposal.id for record in records]
    if st.session_state.get(PILOT_KEY) not in ids:
        st.session_state[PILOT_KEY] = ids[0]
    chosen = st.selectbox(
        "Record to show",
        ids,
        format_func=lambda pid: (
            f"{pid} · {_status(next(r for r in records if r.proposal.id == pid).proposal.status)}"
        ),
        key=PILOT_KEY,
    )
    record = next(r for r in records if r.proposal.id == chosen)
    render_pilot(repo, view, record, actor, now, rules)


def render_governance(
    repo: Repository,
    view: MemoryView,
    proposal: Proposal | None,
    scenarios: ScenarioSet | None,
    result: RecommendationResult | None,
    now: datetime,
    rules: GovernanceRules = DEFAULT_RULES,
) -> None:
    """Agent 6 tab."""
    st.subheader("Agent 6 · Governance")
    st.caption(GOVERNANCE_NOTE)
    notice = st.session_state.pop(NOTICE_KEY, None)
    if notice:
        st.success(notice)
    actor = " ".join(
        st.text_input(
            "Your name (fictional, for the audit trail)", key=ACTOR_KEY, max_chars=80
        ).split()
    )
    render_approval(repo, view, proposal, scenarios, result, actor, now, rules)
    st.divider()
    render_tracker(repo, view, actor, now, rules)
