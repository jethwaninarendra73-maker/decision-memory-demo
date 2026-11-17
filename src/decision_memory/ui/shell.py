"""Application shell: banner, demo role selector, six stage tabs and the evidence view."""

from __future__ import annotations

from datetime import UTC, date, datetime

import streamlit as st

from decision_memory import DEMO_QUESTION, SYNTHETIC_BANNER
from decision_memory.agents import AGENTS
from decision_memory.authz import (
    DEFAULT_ROLE,
    DEMO_ROLE_NOTICE,
    ROLE_LABELS,
    MemoryView,
    parse_role,
)
from decision_memory.config import Settings
from decision_memory.llm_gateway import Gateway, make_gateway
from decision_memory.models import (
    ImpactAssessment,
    Proposal,
    RecommendationResult,
    Role,
    ScenarioSet,
)
from decision_memory.precedent_search import SearchResult
from decision_memory.repository import Repository
from decision_memory.ui.capture import render_capture
from decision_memory.ui.evidence import render_evidence_explorer
from decision_memory.ui.governance import render_governance
from decision_memory.ui.guide import (
    GUIDE_HELP,
    GUIDE_ON_KEY,
    TAB_KEY,
    build_state,
    render_guide,
)
from decision_memory.ui.impact import render_impact
from decision_memory.ui.memory_graph import render_memory_graph
from decision_memory.ui.recommendation import render_recommendation
from decision_memory.ui.reset import render_reset_control
from decision_memory.ui.scenarios import render_scenarios
from decision_memory.ui.search import render_precedent_search

ROLE_KEY = "demo_role"
ROLE_PARAM = "role"


def render_banner() -> None:
    """Persistent label shown above every view."""
    st.warning(f"**{SYNTHETIC_BANNER}** · fictional data, local only, advisory only", icon="⚠️")


def render_tab_banner() -> None:
    """The same label at the top of each stage tab, where the numbers and advice are."""
    st.caption(f"⚠️ **{SYNTHETIC_BANNER}**")


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


def render_sidebar(settings: Settings, gateway: Gateway, notice: str | None) -> Role:
    with st.sidebar:
        st.header("Demo controls")
        role = select_role()
        st.toggle("Guided demo", value=True, key=GUIDE_ON_KEY, help=GUIDE_HELP)
        if gateway.mode == "anthropic":
            st.markdown(
                f"**Mode:** `anthropic` (`{gateway.model}`, used only to reword the brief in "
                "tab 5, on request; everything else is deterministic)"
            )
        else:
            st.markdown("**Mode:** `offline` (deterministic baseline)")
        if notice:
            st.info(f"{notice} Running offline.")
        render_reset_control()
        st.divider()
        st.caption(
            "No live integrations, no real company or customer data, "
            "and nothing here executes a business action."
        )
    return role


def render_memory_tab(view: MemoryView) -> SearchResult | None:
    """Agent 2: ask a question, then see the cited precedents highlighted in the graph."""
    st.subheader("Agent 2 · Institutional Memory")
    result = render_precedent_search(view, date.today())
    st.divider()
    render_memory_graph(
        view,
        highlight=result.precedent_ids if result else (),
        lessons=result.cross_region_ids if result else (),
    )
    return result


def render_app(settings: Settings, repo: Repository) -> None:
    """Render the whole shell. Called once per Streamlit rerun."""
    render_banner()
    st.title(settings.app_name)
    st.caption(f"Demo question: “{DEMO_QUESTION}”")
    gateway, notice = make_gateway(settings)
    role = render_sidebar(settings, gateway, notice)
    view = MemoryView(repo, role)

    guide = st.container()  # drawn above the tabs, filled after them
    # The tabs track which one is open, so the guide can open the tab for each step.
    tabs = st.tabs([agent.label for agent in AGENTS], key=TAB_KEY, on_change="rerun")
    search: SearchResult | None = None  # tabs render in order, so search runs first
    proposal: Proposal | None = None
    impact: ImpactAssessment | None = None
    scenarios: ScenarioSet | None = None
    recommendation: RecommendationResult | None = None
    now = datetime.now(UTC)
    for tab, agent in zip(tabs, AGENTS, strict=True):
        with tab:
            render_tab_banner()
            if agent.key == "capture":
                render_capture(repo, view, settings.fixtures_dir)
            elif agent.key == "memory":
                search = render_memory_tab(view)
            elif agent.key == "impact":
                proposal, impact = render_impact(view, search, now)
            elif agent.key == "scenario":
                scenarios = render_scenarios(view, proposal, now)
            elif agent.key == "recommendation":
                recommendation = render_recommendation(
                    view, search, proposal, impact, scenarios, now, gateway=gateway
                )
            elif agent.key == "governance":
                render_governance(repo, view, proposal, scenarios, recommendation, now)

    if st.session_state.get(GUIDE_ON_KEY, True):
        state = build_state(view, search, proposal, scenarios, recommendation, impact)
        render_guide(guide, view, state)

    st.divider()
    render_evidence_explorer(view)
