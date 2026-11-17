"""Simple view: what each tab shows first.

In the simple view (the default) each tab opens with a plain heading and one line on
what it is for, then its main result. Supporting detail (score tables, formulas,
evidence offsets, rule texts, audit trails) sits under "Show details" sections. In the
full view the same detail is drawn in place, as before.

Only presentation changes. Every agent runs the same way, every widget is still drawn
(an expander keeps its contents on the page, collapsed), and nothing the role may not
see is added in either view.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import streamlit as st

from decision_memory.agents.base import AgentInfo

SIMPLE_KEY = "simple_view"
SIMPLE_LABEL = "Simple view"
SIMPLE_HELP = (
    "Shows each tab's main result first, in plain words. The supporting detail is still "
    'there, under "Show details". Turn it off to see everything at once.'
)
DETAILS_PREFIX = "Show details: "

# An icon per tab, in front of its heading in the simple view.
ICONS: dict[str, str] = {
    "capture": ":material/edit_note:",
    "memory": ":material/history:",
    "impact": ":material/groups:",
    "scenario": ":material/balance:",
    "recommendation": ":material/lightbulb:",
    "governance": ":material/verified:",
}

# One line per tab, shown under its heading in the simple view.
INTROS: dict[str, str] = {
    "capture": (
        "Turns meeting notes into a decision record. You check what it found and confirm; "
        "nothing is saved until you do."
    ),
    "memory": (
        "Finds past decisions like this question and what happened after them. A decision "
        "that failed is shown as a warning, not as an example to copy."
    ),
    "impact": (
        "Lists who and what the change would affect, based on what happened in those past "
        "decisions. Each item says whether a document backs it or it is an assumption."
    ),
    "scenario": (
        "Compares +3%, +5% and +7% with simple formulas. The figures are illustrations from "
        "editable synthetic inputs, not forecasts."
    ),
    "recommendation": (
        "Advice from written rules, with the reason for each point. A named person decides; "
        "the app changes nothing."
    ),
    "governance": (
        "A person approves or rejects (simulated), weekly results are recorded, and the "
        "outcome is saved as a new past decision for next time."
    ),
}


def is_simple() -> bool:
    """True in the simple view, the default."""
    return bool(st.session_state.get(SIMPLE_KEY, True))


def render_view_switch() -> None:
    """The sidebar switch between the simple and the full view."""
    st.toggle(SIMPLE_LABEL, value=True, key=SIMPLE_KEY, help=SIMPLE_HELP)


def tab_heading(info: AgentInfo) -> None:
    """The tab's heading: plain words and a one-line purpose in the simple view, the
    agent's name in the full view."""
    if is_simple():
        st.subheader(f"{ICONS[info.key]} {info.tab_name or info.name}")
        st.info(INTROS[info.key], icon="💡")
        st.caption(f"Agent {info.number} · {info.name}")
    else:
        st.subheader(f"Agent {info.number} · {info.name}")


@contextmanager
def details(label: str, *, expanded: bool = False) -> Iterator[None]:
    """Supporting detail: a collapsed "Show details" section in the simple view, drawn in
    place in the full view. Widgets inside keep working either way."""
    if is_simple():
        with st.expander(DETAILS_PREFIX + label, expanded=expanded):
            yield
    else:
        with st.container():
            yield


def expanded_in_full_view(expanded: bool = True) -> bool:
    """For sections that were open by default: open in the full view, closed in the
    simple view."""
    return expanded and not is_simple()
