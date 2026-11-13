"""Precedent search view: question -> inferred filters -> ranked, cited precedents.

The question is user input and decision titles come from stored records, so neither is
trusted: titles are Markdown-escaped, claims and score explanations are shown with
`st.text` or in data tables (both literal), and citations use the escaped Markdown that
`provenance` produces.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from decision_memory import DEMO_QUESTION
from decision_memory.authz import MemoryView
from decision_memory.precedent_search import (
    DEFAULT_SETTINGS,
    Precedent,
    SearchQuery,
    SearchResult,
    SearchSettings,
    infer_query,
    search_precedents,
)
from decision_memory.provenance import INSUFFICIENT_EVIDENCE_TEXT, Claim, escape_markdown

QUESTION_KEY = "search_question"
REGION_KEY = "search_region"
SEGMENT_KEY = "search_segment"
RATE_KEY = "search_rate_pct"
USE_RATE_KEY = "search_use_rate"
AS_OF_KEY = "search_as_of"
LAST_QUESTION_KEY = "search_last_question"
ANY = "Any"
NOTHING_TO_SEARCH = "No confirmed decisions are visible to this role, so there is nothing to search."
COMPONENT_LABELS = {
    "lexical": "Lexical overlap",
    "metadata": "Region / segment / rate match",
    "outcome": "Outcome relevance",
    "recency": "Recency",
}


def formula_text(settings: SearchSettings) -> str:
    """The scoring rule in words, with the actual weights and thresholds."""
    w = settings.weights
    return (
        f"Score = {w.lexical:.2f} × lexical + {w.metadata:.2f} × metadata + "
        f"{w.outcome:.2f} × outcome + {w.recency:.2f} × recency (if the question sets no "
        "region, segment or rate, the metadata weight is shared across the others). "
        f"A decision needs lexical "
        f"≥ {settings.min_lexical:.2f} and score ≥ {settings.min_score:.2f} to be returned; "
        f"top {settings.top_k} shown. Recency half-life {settings.recency_half_life_days} days."
    )


def component_rows(precedent: Precedent) -> list[dict[str, str]]:
    return [
        {
            "Component": COMPONENT_LABELS.get(c.name, c.name),
            "Value (0–1)": f"{c.value:.2f}" if c.applicable else "n/a",
            "Weight": f"{c.weight:.2f}",
            "Contribution": f"{c.contribution:.3f}",
            "How it was scored": c.explanation,
        }
        for c in precedent.components
    ]


def ranked_rows(result: SearchResult) -> list[dict[str, str]]:
    returned = set(result.precedent_ids)
    return [
        {
            "Rank": str(rank),
            "Decision": p.decision_id,
            "Region": p.region,
            "Score": f"{p.score:.3f}",
            **{
                COMPONENT_LABELS[c.name]: f"{c.value:.2f}" if c.applicable else "n/a"
                for c in p.components
            },
            "Returned": "yes" if p.decision_id in returned else ("below threshold" if p.candidate else "off-topic"),
        }
        for rank, p in enumerate(result.ranked, start=1)
    ]


def _render_claim(label: str, claim: Claim) -> None:
    st.markdown(f"**{label}**")
    if not claim.supported:
        st.text(INSUFFICIENT_EVIDENCE_TEXT)
        return
    st.text(claim.render())
    for item in claim.evidence:
        st.markdown(f"- {item.format_markdown()}")


def render_precedent(rank: int, precedent: Precedent) -> None:
    with st.container(border=True):
        rate = "" if precedent.surcharge_rate is None else f" · {precedent.surcharge_rate:.1%}"
        st.markdown(
            f"**{rank}. {precedent.decision_id} · {escape_markdown(precedent.title)}** "
            f"· score {precedent.score:.2f}"
        )
        st.caption(
            f"{precedent.decision_date.isoformat()} · {precedent.region} · {precedent.market} · "
            f"{precedent.customer_segment}{rate} · outcome: "
            f"{precedent.outcome_verdict.value if precedent.outcome_verdict else 'not observed yet'}"
        )
        if precedent.warning:
            st.warning(precedent.warning, icon="⚠️")
        st.dataframe(component_rows(precedent), hide_index=True)
        _render_claim("What was decided", precedent.decision_claim)
        if precedent.outcome_claim is not None:
            _render_claim("What happened", precedent.outcome_claim)


def _sync_filters(question: str, today: date) -> SearchQuery:
    """Reset the filter widgets to what the question states whenever it changes."""
    inferred = infer_query(question, today)
    if st.session_state.get(LAST_QUESTION_KEY) != question:
        st.session_state[LAST_QUESTION_KEY] = question
        st.session_state[REGION_KEY] = inferred.region or ANY
        st.session_state[SEGMENT_KEY] = ANY
        st.session_state[USE_RATE_KEY] = inferred.surcharge_rate is not None
        st.session_state[RATE_KEY] = round((inferred.surcharge_rate or 0.0) * 100, 2)
    st.session_state.setdefault(AS_OF_KEY, today)
    return inferred


def render_precedent_search(view: MemoryView, today: date) -> SearchResult | None:
    """Question box, editable filters and ranked precedents. Returns the result."""
    st.markdown("#### Ask institutional memory")
    question = st.text_input("Question", value=DEMO_QUESTION, key=QUESTION_KEY).strip()
    if not question:
        st.info("Type a question to search the decisions this role can see.")
        return None
    decisions = view.decisions()
    if not decisions:
        st.info(NOTHING_TO_SEARCH)
        return None
    _sync_filters(question, today)

    regions = sorted({d.region for d in decisions})
    segments = sorted({d.customer_segment for d in decisions})
    for key, options in ((REGION_KEY, regions), (SEGMENT_KEY, segments)):
        if st.session_state.get(key) not in (ANY, *options):
            st.session_state[key] = ANY  # e.g. after a role switch

    with st.expander("Filters read from the question (editable)", expanded=False):
        cols = st.columns(4)
        region = cols[0].selectbox("Region", [ANY, *regions], key=REGION_KEY)
        segment = cols[1].selectbox("Customer segment", [ANY, *segments], key=SEGMENT_KEY)
        with cols[2]:
            use_rate = st.checkbox("Match surcharge rate", key=USE_RATE_KEY)
            rate_pct = st.number_input(
                "Surcharge rate (%)", min_value=0.0, max_value=100.0, step=0.5,
                key=RATE_KEY, disabled=not use_rate,
            )  # fmt: skip
        as_of = cols[3].date_input("Recency reference date", key=AS_OF_KEY)
        st.caption(formula_text(DEFAULT_SETTINGS))

    query = SearchQuery(
        text=question,
        region=None if region == ANY else region,
        customer_segment=None if segment == ANY else segment,
        surcharge_rate=rate_pct / 100 if use_rate else None,
        as_of=as_of,
    )
    result = search_precedents(view, query)
    filters = [
        f"region {query.region}" if query.region else "any region",
        f"segment {query.customer_segment}" if query.customer_segment else "any segment",
        f"rate {query.surcharge_rate:.1%}" if query.surcharge_rate is not None else "any rate",
    ]
    st.caption(
        "Query terms: " + (", ".join(result.query_terms) or "none") + " · " + " · ".join(filters)
    )

    if result.abstained:
        st.warning(result.message)
    else:
        st.markdown(f"**{result.message}**")
        for rank, precedent in enumerate(result.precedents, start=1):
            render_precedent(rank, precedent)
    with st.expander("Every decision this role can see, ranked"):
        st.dataframe(ranked_rows(result), hide_index=True)
    return result
