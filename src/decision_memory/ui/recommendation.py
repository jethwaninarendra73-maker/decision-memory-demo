"""Recommendation view (Agent 5): one advisory card with cited reasons, the condition it
depends on, an action plan for the approver, and how every option was judged.

Reason, plan and assumption texts are built from stored records and rule text, so they
are Markdown-escaped; citations use the escaped Markdown that `provenance` produces.
Nothing on this tab executes, sends or stores anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import streamlit as st

from decision_memory.agents.recommendation_agent import (
    ADVISORY_NOTE,
    DEFAULT_RULES,
    RecommendationRules,
    recommend,
    rules_summary,
)
from decision_memory.authz import ROLE_LABELS, MemoryView
from decision_memory.brief import Brief, brief_key, briefable, offline_brief, write_brief
from decision_memory.llm_gateway import Gateway, OfflineGateway
from decision_memory.models import (
    ImpactAssessment,
    OptionCheck,
    Proposal,
    Recommendation,
    RecommendationResult,
    RecommendationType,
    ScenarioSet,
)
from decision_memory.precedent_search import SearchResult
from decision_memory.provenance import (
    INSUFFICIENT_EVIDENCE_TEXT,
    InaccessibleEvidenceError,
    escape_markdown,
    resolve_ref,
)

NO_QUESTION_MESSAGE = (
    "Ask a question in tab 2 · Institutional Memory first. The recommendation is built from "
    "its precedents, the impact analysis (tab 3) and the scenarios (tab 4)."
)
REFUSED_MESSAGE = (
    f"{INSUFFICIENT_EVIDENCE_TEXT} The recommendation cites evidence that could not be "
    "verified for this role, so it is not shown."
)
BRIEF_KEY = "recommendation_briefs"  # brief_key -> Brief written by the model
BRIEF_BUTTON_KEY = "recommendation_brief_llm"
BRIEF_CHECKS = (
    "The card above is the recommendation; the brief only rewords its cited reasons. A "
    "model's brief is shown only if every sentence cites sources the card cites and uses "
    "no number missing from the reasons it cites. The checks do not catch a changed "
    "meaning: read it against the card."
)
HEADLINES: dict[RecommendationType, str] = {
    RecommendationType.PHASED_PILOT: "Phased pilot",
    RecommendationType.PROCEED: "Proceed",
    RecommendationType.DO_NOT_PROCEED: "Do not proceed",
    RecommendationType.INSUFFICIENT_EVIDENCE: "Insufficient evidence to recommend",
}


def _pct(fraction: float) -> str:
    return f"{fraction * 100:g}%"


def _musd(value: float) -> str:
    sign = "+" if value >= 0 else "−"
    return f"{sign}${abs(value):,.2f}M"


def headline(recommendation: Recommendation) -> str:
    """`Phased pilot at +5%`, `Do not proceed`, ..."""
    rate = recommendation.recommended_rate
    at = f" at +{_pct(rate)}" if rate is not None else ""
    return f"{HEADLINES[recommendation.decision_type]}{at}"


def option_rows(options: Sequence[OptionCheck]) -> list[dict[str, str]]:
    return [
        {
            "Option": f"+{_pct(o.surcharge_rate)}",
            "Verdict": o.verdict,
            "Revenue change": _musd(o.revenue_change_musd),
            "Margin change": _musd(o.margin_change_musd),
            "Churn loss": _pct(o.churn_loss_fraction),
            "Within guardrail": "yes" if o.within_guardrail else "no",
            "Successful precedents near it": ", ".join(o.supporting_decision_ids) or "none",
            "Cautions near it": ", ".join(o.caution_decision_ids) or "none",
        }
        for o in options
    ]


def condition_text(result: RecommendationResult) -> tuple[bool, str] | None:
    """(met, message) for the exclusion the recommendation depends on, if it has one."""
    if result.required_exclusion is None or result.recommendation.recommended_rate is None:
        return None
    group = result.required_exclusion.lower()
    if result.exclusion_in_proposal:
        return True, f"Condition met: the draft proposal excludes {group}."
    return False, (
        f"Condition not met yet: this recommendation holds only if {group} are excluded, and "
        "the draft proposal does not exclude them. Tick the exclusion in tab 3."
    )


def render_reasons(view: MemoryView, recommendation: Recommendation) -> None:
    for reason in recommendation.reasons:
        # One Markdown block per reason, so its citations nest under it.
        lines = [f"- {escape_markdown(reason.text)}"]
        for ref in reason.source_refs:
            cited = resolve_ref(view, ref)
            if cited is not None:
                lines.append(f"    - {cited.format_markdown()}")
        st.markdown("\n".join(lines))


def render_card(view: MemoryView, result: RecommendationResult) -> None:
    rec = result.recommendation
    with st.container(border=True):
        st.markdown(f"#### Advisory recommendation: {headline(rec)}")
        if rec.risk_level is not None:
            st.markdown(
                f"Risk level: **{rec.risk_level.value}** · requires approval by a named "
                f"**{ROLE_LABELS[rec.approval_required_by]}** (recorded by Agent 6)"
            )
        st.caption(ADVISORY_NOTE)
        condition = condition_text(result)
        if condition is not None:
            met, text = condition
            if met:
                st.caption(text)
            else:
                st.warning(text, icon="⚠️")
        if rec.reasons:
            st.markdown("**Why** (every reason cites a source this role can see)")
            render_reasons(view, rec)
        if rec.action_plan:
            st.markdown("**Action plan for the approver** (nothing here is executed)")
            st.markdown(
                "\n".join(
                    f"{number}. {escape_markdown(step)}"
                    for number, step in enumerate(rec.action_plan, start=1)
                )
            )


def render_brief_sentences(view: MemoryView, rec: Recommendation, brief: Brief) -> None:
    """Each sentence with the card's own citations for the sources it names."""
    refs = {}
    for reason in rec.reasons:
        for ref in reason.source_refs:
            refs.setdefault(ref.source_id, []).append(ref)
    lines = [f"**{escape_markdown(brief.headline)}**"]
    for sentence in brief.sentences:
        lines.append(f"- {escape_markdown(sentence.text)}")
        for source_id in sentence.source_ids:
            for ref in refs.get(source_id, [])[:1]:  # the first excerpt the card cites
                cited = resolve_ref(view, ref)
                if cited is not None:
                    lines.append(f"    - {cited.format_markdown()}")
    st.markdown("\n".join(lines))


def render_brief(view: MemoryView, rec: Recommendation, gateway: Gateway) -> Brief | None:
    """Anthropic mode only: a plain-language brief of the card, reworded by the model on
    request and checked against the card. Offline mode shows nothing new. Returns the
    brief shown."""
    if gateway.mode != "anthropic" or not briefable(rec):
        return None
    head = headline(rec)
    st.markdown("**Plain-language brief**")
    key = brief_key(rec, head, view.role.value)
    stored: dict[str, Brief] = st.session_state.setdefault(BRIEF_KEY, {})
    if st.button(f"Reword with Claude ({gateway.model})", key=BRIEF_BUTTON_KEY):
        with st.spinner(f"Rewording with Claude ({gateway.model})…"):
            stored[key] = write_brief(rec, head, gateway)
    shown = stored.get(key) or offline_brief(rec, head)
    if shown.notice:
        st.warning(shown.notice)
    render_brief_sentences(view, rec, shown)
    if shown.written_by == "anthropic":
        st.caption(f"Reworded by {shown.model} and checked against the card. {BRIEF_CHECKS}")
    elif not shown.notice:
        st.caption(
            "Written by the offline template from the card's cited reasons. Click the "
            "button to have Claude reword it (one request)."
        )
    return shown


def render_recommendation(
    view: MemoryView,
    search: SearchResult | None,
    proposal: Proposal | None,
    impact: ImpactAssessment | None,
    scenarios: ScenarioSet | None,
    now: datetime,
    rules: RecommendationRules = DEFAULT_RULES,
    gateway: Gateway | None = None,
) -> RecommendationResult | None:
    """Agent 5 tab. Returns the result shown, or None when there is nothing to show."""
    st.subheader("Agent 5 · Recommendation")
    if search is None or proposal is None or scenarios is None:
        st.info(NO_QUESTION_MESSAGE)
        return None
    try:
        result = recommend(view, proposal, search.precedent_ids, impact, scenarios, now, rules)
    except InaccessibleEvidenceError:
        # Fail closed: show nothing from the refused recommendation, keep the page running.
        st.warning(REFUSED_MESSAGE)
        return None

    rec = result.recommendation
    if rec.decision_type is RecommendationType.INSUFFICIENT_EVIDENCE:
        st.warning(result.message)
    else:
        if rec.recommended_rate is None:
            st.warning(result.message)
        render_card(view, result)
        render_brief(view, rec, gateway or OfflineGateway())

    if result.options:
        st.markdown("**How each option was judged**")
        st.dataframe(option_rows(result.options), hide_index=True)
        st.caption(rules_summary(rules))
        st.caption(f"Precedents used: {', '.join(result.precedent_ids)}.")
    if rec.assumptions:
        with st.expander("Assumptions behind the figures (not reasons)"):
            st.markdown("\n".join(f"- {escape_markdown(line)}" for line in rec.assumptions))
    if rec.uncertainty and rec.recommended_rate is not None:
        st.caption(escape_markdown(rec.uncertainty))
    return result
