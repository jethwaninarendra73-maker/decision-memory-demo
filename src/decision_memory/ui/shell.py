"""Application shell: banner, demo role selector, six stage tabs and the evidence view."""

from __future__ import annotations

from datetime import date

import streamlit as st

from decision_memory import DEMO_QUESTION, SYNTHETIC_BANNER
from decision_memory.agents import AGENTS, AgentInfo
from decision_memory.authz import (
    DEFAULT_ROLE,
    DEMO_ROLE_NOTICE,
    ROLE_LABELS,
    MemoryView,
    parse_role,
)
from decision_memory.config import Settings
from decision_memory.models import Role
from decision_memory.repository import Repository
from decision_memory.ui.capture import render_capture
from decision_memory.ui.evidence import render_evidence_explorer
from decision_memory.ui.memory_graph import render_memory_graph
from decision_memory.ui.search import render_precedent_search

ROLE_KEY = "demo_role"
ROLE_PARAM = "role"
RESET_PLACEHOLDER_NOTE = (
    "Placeholder: for now, run `python scripts/reset_demo.py` in a terminal. "
    "The button is wired up in a later stage."
)


def render_banner() -> None:
    """Persistent label shown above every view."""
    st.warning(f"**{SYNTHETIC_BANNER}** · fictional data, local only, advisory only", icon="⚠️")


def select_role() -> Role:
    """Demo role switch. A demonstration control, not authentication."""
    if ROLE_KEY not in st.session_state:
        from_link = parse_role(st.query_params.get(ROLE_PARAM))
        st.session_state[ROLE_KEY] = (from_link or DEFAULT_ROLE).value
    st.selectbox(
        "Demo role",
        options=[role.value for role in Role],
        format_func=lambda value: ROLE_LABELS[Role(value)],
        key=ROLE_KEY,
        help=DEMO_ROLE_NOTICE,
    )
    st.caption(DEMO_ROLE_NOTICE)
    role = Role(st.session_state[ROLE_KEY])
    # Keep the role in the address so evidence links open under the same demo role.
    st.query_params[ROLE_PARAM] = role.value
    return role


def render_sidebar(settings: Settings) -> Role:
    with st.sidebar:
        st.header("Demo controls")
        role = select_role()
        st.markdown(f"**Mode:** `{settings.effective_llm_mode}` (deterministic baseline)")
        if settings.llm_fallback_reason:
            st.info(f"{settings.llm_fallback_reason} Running offline.")
        st.button(
            "Demo reset",
            key="demo_reset",
            disabled=True,
            help=RESET_PLACEHOLDER_NOTE,
        )
        st.caption(RESET_PLACEHOLDER_NOTE)
        st.divider()
        st.caption(
            "No live integrations, no real company or customer data, "
            "and nothing here executes a business action."
        )
    return role


def render_stage_placeholder(agent: AgentInfo) -> None:
    st.subheader(f"Agent {agent.label}")
    st.write(agent.purpose)
    reads, produces = st.columns(2)
    with reads:
        st.markdown("**Reads**")
        st.write(agent.reads)
    with produces:
        st.markdown("**Produces**")
        st.write(agent.produces)
    st.info(f"Not built yet. This agent arrives in Stage {agent.build_stage:02d}.")


def render_memory_tab(view: MemoryView) -> None:
    """Agent 2: ask a question, then see the cited precedents highlighted in the graph."""
    st.subheader("Agent 2 · Institutional Memory")
    result = render_precedent_search(view, date.today())
    st.divider()
    render_memory_graph(
        view,
        highlight=result.precedent_ids if result else (),
        lessons=result.cross_region_ids if result else (),
    )


def render_app(settings: Settings, repo: Repository) -> None:
    """Render the whole shell. Called once per Streamlit rerun."""
    render_banner()
    st.title(settings.app_name)
    st.caption(f"Demo question: “{DEMO_QUESTION}”")
    role = render_sidebar(settings)
    view = MemoryView(repo, role)

    tabs = st.tabs([agent.label for agent in AGENTS])
    for tab, agent in zip(tabs, AGENTS, strict=True):
        with tab:
            if agent.key == "capture":
                render_capture(repo, view, settings.fixtures_dir)
            elif agent.key == "memory":
                render_memory_tab(view)
            else:
                render_stage_placeholder(agent)

    st.divider()
    render_evidence_explorer(view)
