"""Impact view (Agent 3): the draft proposal, then every impact item with its label,
confidence, rationale and quoted evidence.

The question is user input and evidence lines are untrusted source text: the question is
shown with `st.text`, and quoted lines go through the escaped Markdown that `provenance`
produces. Item labels, rationales and warnings are built from the rule table and record
IDs only.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from decision_memory.agents.impact_agent import (
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    DEFAULT_RULES,
    HVC_EXCLUSION,
    ImpactRules,
    assess_impact,
    draft_proposal,
    format_musd,
    verify_assessment,
)
from decision_memory.authz import MemoryView
from decision_memory.models import (
    ESTIMATE_CATEGORIES,
    EvidenceBasis,
    ImpactAssessment,
    ImpactItem,
    Proposal,
)
from decision_memory.precedent_search import SearchResult
from decision_memory.provenance import (
    INSUFFICIENT_EVIDENCE_TEXT,
    InaccessibleEvidenceError,
    resolve_ref,
)

EXCLUDE_HVC_KEY = "impact_exclude_hvc"
NO_SEARCH_MESSAGE = (
    "Ask a question in tab 2 · Institutional Memory first. Impact is assessed from the "
    "precedents that search returns."
)
NOTHING_TO_ASSESS = (
    "No confirmed decisions are visible to this role, so there are no precedents to assess "
    "impact from."
)
REFUSED_MESSAGE = (
    f"{INSUFFICIENT_EVIDENCE_TEXT} The impact assessment cites evidence that could not be "
    "verified for this role, so it is not shown."
)
DRAFT_NOTE = (
    "Draft proposal for this analysis only: it is not saved, and nothing is sent, "
    "priced or changed."
)
BASIS_LABELS: dict[EvidenceBasis, str] = {
    EvidenceBasis.SOURCE_SUPPORTED: "Source-supported",
    EvidenceBasis.ASSUMED_FOR_DEMO: "Assumed for demo",
}
METHOD_NOTE = (
    "Each item comes from a rule: it is source-supported when a line in a precedent's own "
    "sources contains one of the rule's terms, and assumed for demo when the precedent is "
    "only linked to it in the memory graph. Confidence counts the precedents with a quoted "
    "line (3+ high, 2 medium, 1 low). Cost and effort are illustrative assumptions."
)


def format_range(item: ImpactItem) -> str:
    """`$0.20M – $0.35M` or `7 – 12 person-weeks`."""
    if item.low is None or item.high is None:
        return ""
    if item.unit == "USD M":
        return f"{format_musd(item.low)} – {format_musd(item.high)}"
    return f"{item.low:g} – {item.high:g} {item.unit}"


def item_rows(assessment: ImpactAssessment) -> list[dict[str, str]]:
    """One summary row per item, in display order."""
    return [
        {
            "Category": CATEGORY_LABELS[item.category],
            "Item": item.label,
            "Basis": BASIS_LABELS[item.basis],
            "Confidence": item.confidence.value,
            "Range": format_range(item),
            "Precedents": ", ".join(item.decision_ids),
            "Citations": ", ".join(dict.fromkeys(f"[{r.source_id}]" for r in item.source_refs)),
        }
        for item in assessment.items
    ]


def rule_rows(rules: ImpactRules) -> list[dict[str, str]]:
    return [
        {
            "Rule": rule.key,
            "Category": CATEGORY_LABELS[rule.category],
            "Item": rule.label,
            "Terms (word starts with)": ", ".join(rule.terms),
            "Only precedents in": ", ".join(rule.markets) or "any market",
        }
        for rule in rules.rules
    ]


def estimate_rows(rules: ImpactRules) -> list[dict[str, str]]:
    labels = {rule.key: rule.label for rule in rules.rules}
    return [
        {
            "System": labels[key],
            "Cost": f"{format_musd(e.cost_low_musd)} – {format_musd(e.cost_high_musd)}",
            "Effort (person-weeks)": f"{e.effort_low_weeks:g} – {e.effort_high_weeks:g}",
            "Basis": BASIS_LABELS[EvidenceBasis.ASSUMED_FOR_DEMO],
        }
        for key, e in rules.system_estimates.items()
    ]


def render_item(view: MemoryView, item: ImpactItem) -> None:
    with st.container(border=True):
        st.markdown(
            f"**{item.label}** · {BASIS_LABELS[item.basis]} · confidence {item.confidence.value}"
        )
        if item.category in ESTIMATE_CATEGORIES:
            st.markdown(f"Range: **{format_range(item)}**")
        if item.warning:
            st.warning(item.warning, icon="⚠️")
        if item.exclusion is not None:
            st.caption(
                "Excluded by this proposal."
                if item.excluded_by_proposal
                else "Not excluded by this proposal."
            )
        st.caption(item.rationale)
        for evidence in item.evidence:
            cited = resolve_ref(view, evidence.ref)
            if cited is None:  # verify_assessment already refused this; fail closed anyway
                continue
            st.markdown(
                f"- {evidence.decision_id}, {evidence.cited_by} source: {cited.format_markdown()}"
            )


def render_proposal(result: SearchResult, now: datetime, view: MemoryView) -> Proposal:
    """The draft proposal derived from the search question, with the one editable choice."""
    query = result.query
    st.markdown("**Proposal (draft)**")
    st.text(f"Question: {query.text}")
    exclude = st.checkbox(f"Exclude {HVC_EXCLUSION.lower()}", key=EXCLUDE_HVC_KEY, value=False)
    proposal = draft_proposal(
        query.text,
        query.region,
        query.as_of,
        now,
        surcharge_rate=query.surcharge_rate,
        customer_segment=query.customer_segment,
        exclusions=[HVC_EXCLUSION] if exclude else [],
        requested_by=view.role,
    )
    options = " / ".join(f"+{rate:.0%}" for rate in proposal.surcharge_options)
    st.caption(
        f"Region: {proposal.region} · effective {proposal.effective_quarter} (the quarter after "
        f"{query.as_of.isoformat()}) · options {options}, compared in Agent 4 · "
        f"exclusions: {', '.join(proposal.exclusions) or 'none'}"
    )
    st.caption(DRAFT_NOTE)
    return proposal


def render_impact(
    view: MemoryView,
    result: SearchResult | None,
    now: datetime,
    rules: ImpactRules = DEFAULT_RULES,
) -> tuple[Proposal | None, ImpactAssessment | None]:
    """Agent 3 tab. Returns the draft proposal and the assessment shown (None when there is
    no search yet, or when the assessment was withheld)."""
    st.subheader("Agent 3 · Impact Analysis")
    if result is None:
        st.info(NO_SEARCH_MESSAGE if view.decisions() else NOTHING_TO_ASSESS)
        return None, None
    proposal = render_proposal(result, now, view)
    assessment = assess_impact(view, proposal, result.precedent_ids, rules)
    try:
        verify_assessment(view, assessment)
    except InaccessibleEvidenceError:
        # Fail closed: show nothing from the refused assessment and keep the page running.
        st.warning(REFUSED_MESSAGE)
        return proposal, None

    if assessment.abstained:
        st.warning(assessment.message)
        return proposal, assessment
    st.markdown(f"{assessment.message} Precedents used: {', '.join(assessment.precedent_ids)}.")
    st.caption(METHOD_NOTE)
    st.dataframe(item_rows(assessment), hide_index=True)

    for category in CATEGORY_ORDER:
        items = assessment.by_category(category)
        if not items:
            continue
        st.markdown(f"##### {CATEGORY_LABELS[category]}")
        for item in items:
            render_item(view, item)

    with st.expander("Impact rules and assumed system ranges"):
        st.caption("Edit `DEFAULT_RULES` in src/decision_memory/agents/impact_agent.py.")
        st.dataframe(rule_rows(rules), hide_index=True)
        st.dataframe(estimate_rows(rules), hide_index=True)
    return proposal, assessment
