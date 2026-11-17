"""The answer at a glance, at the top of the page, and the option cards.

Drawn into a container placed above the tabs and filled after them (like the guide), so
it shows what the tabs computed on this run for the active role. Text that comes from
records or rules is Markdown-escaped; colours come from Streamlit's own Markdown
(`:green[...]`), never from HTML.
"""

from __future__ import annotations

from collections.abc import Sequence

import streamlit as st

from decision_memory.glance import Glance, OptionCard
from decision_memory.provenance import escape_markdown

NOT_ASKED = "Ask a question in tab 2 · Past decisions to see the answer here."
VERDICT_COLORS = {"Recommended": "green", "Ruled out": "red", "Not chosen": "gray"}
VERDICT_ICONS = {
    "Recommended": ":material/check_circle:",
    "Ruled out": ":material/block:",
    "Not chosen": ":material/remove:",
}


def _pct(fraction: float) -> str:
    return f"{round(fraction * 100, 3):g}%"


def _musd(value: float) -> str:
    sign = "+" if value >= 0 else "−"
    return f"{sign}${abs(value):,.2f}M"


def render_glance(container, glance: Glance) -> None:
    """The question, the answer and the three figures behind it."""
    with container, st.container(border=True):
        st.caption("The question")
        st.markdown(f"##### {escape_markdown(glance.question) or '—'}")
        if not glance.asked:
            st.info(NOT_ASKED, icon=":material/search:")
            return
        answer, money, churn, basis = st.columns(4)
        with answer:
            st.metric(
                "Recommendation",
                glance.answer or "—",
                icon=":material/lightbulb:",
                border=True,
                help="Advice only. A named person decides; the app changes nothing.",
            )
            if glance.answer_note:
                st.caption(escape_markdown(glance.answer_note))
        with money:
            st.metric(
                "Revenue per quarter",
                "—" if glance.revenue_change_musd is None else _musd(glance.revenue_change_musd),
                icon=":material/payments:",
                border=True,
                help="Revenue change for the recommended option, from tab 4's formulas.",
            )
            if glance.margin_change_musd is not None:
                st.caption(f"margin {_musd(glance.margin_change_musd)} a quarter")
        with churn:
            st.metric(
                "Customers lost",
                "—" if glance.churn_loss_fraction is None else _pct(glance.churn_loss_fraction),
                icon=":material/group_remove:",
                border=True,
                help="Revenue lost to churn, as a share of baseline revenue.",
            )
            if glance.churn_loss_fraction is not None and glance.churn_guardrail is not None:
                limit = _pct(glance.churn_guardrail)
                within = glance.churn_loss_fraction <= glance.churn_guardrail
                st.markdown(
                    f":green[within the {limit} limit]"
                    if within
                    else f":red[over the {limit} limit]"
                )
        with basis:
            st.metric(
                "Past decisions",
                str(glance.precedents),
                icon=":material/history:",
                border=True,
                help="Past decisions found for this question, each quoting its sources.",
            )
            if glance.failed_precedents:
                st.markdown(
                    f":orange[{glance.failed_precedents} failure"
                    f"{'s' if glance.failed_precedents != 1 else ''} flagged as a warning]"
                )
            elif glance.precedents:
                st.caption("none of them failed")


def render_option_cards(cards: Sequence[OptionCard]) -> None:
    """One card per option: its figures and the recommendation's verdict on it."""
    for column, card in zip(st.columns(len(cards)), cards, strict=True):
        with column, st.container(border=True):
            st.markdown(f"#### +{_pct(card.rate)}")
            st.badge(
                card.verdict,
                icon=VERDICT_ICONS[card.verdict],
                color=VERDICT_COLORS[card.verdict],  # type: ignore[arg-type]
            )
            st.metric("Revenue per quarter", _musd(card.revenue_change_musd))
            st.caption(f"{_pct(card.churn_loss_fraction)} customers lost")
            st.caption(escape_markdown(card.reason))
