"""Stage 01 application shell: banner, sidebar and six placeholder stage tabs."""

from __future__ import annotations

import streamlit as st

from decision_memory import DEMO_QUESTION, SYNTHETIC_BANNER
from decision_memory.agents import AGENTS, AgentInfo
from decision_memory.config import Settings

RESET_PLACEHOLDER_NOTE = (
    "Placeholder: for now, run `python scripts/reset_demo.py` in a terminal. "
    "The button is wired up in a later stage."
)


def render_banner() -> None:
    """Persistent label shown above every view."""
    st.warning(f"**{SYNTHETIC_BANNER}** · fictional data, local only, advisory only", icon="⚠️")


def render_sidebar(settings: Settings) -> None:
    with st.sidebar:
        st.header("Demo controls")
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


def render_app(settings: Settings) -> None:
    """Render the whole shell. Called once per Streamlit rerun."""
    render_banner()
    st.title(settings.app_name)
    st.caption(f"Demo question: “{DEMO_QUESTION}”")
    render_sidebar(settings)

    tabs = st.tabs([agent.label for agent in AGENTS])
    for tab, agent in zip(tabs, AGENTS, strict=True):
        with tab:
            render_stage_placeholder(agent)
