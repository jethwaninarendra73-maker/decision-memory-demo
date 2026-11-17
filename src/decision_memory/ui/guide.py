"""Guided demo panel: the six steps of the five-minute demo above the stage tabs.

The panel is drawn into a container placed above the tabs but filled after them, so its
live checks can read what the tabs computed on this run. Its buttons only navigate: they
switch the step, open a tab or open a source in the evidence explorer. Guide text and
talk tracks are Markdown-escaped (a `$` would otherwise start a formula).
"""

from __future__ import annotations

import streamlit as st

from decision_memory.agents import AGENTS
from decision_memory.agents.capture_agent import CAPTURED_AUTHOR
from decision_memory.agents.governance_agent import pilot_for_question
from decision_memory.agents.impact_agent import HVC_EXCLUSION, source_lines
from decision_memory.agents.recommendation_agent import key_risks
from decision_memory.authz import MemoryView
from decision_memory.demo_guide import (
    HOOK_SOURCE_IDS,
    STEPS,
    DemoState,
    PilotSummary,
    checks,
    talk_track,
)
from decision_memory.models import (
    ImpactAssessment,
    OutcomeVerdict,
    Proposal,
    RecommendationResult,
    ScenarioSet,
    SourceDocument,
)
from decision_memory.precedent_search import SearchResult
from decision_memory.provenance import SOURCE_TYPE_LABELS, escape_markdown
from decision_memory.ui.evidence import SELECT_KEY as EVIDENCE_KEY
from decision_memory.ui.recommendation import headline
from decision_memory.ui.view import is_simple

GUIDE_ON_KEY = "guide_on"
STEP_KEY = "guide_step"  # the step selector widget
STEP_STATE_KEY = "guide_step_kept"  # the step, kept while the guide is hidden
TAB_KEY = "stage_tab"  # the stage tabs' state: the label of the open tab

GUIDE_HELP = "Walks through the five-minute demo. Turn it off to use the tabs freely."


def tab_label(agent_key: str) -> str:
    return next(agent.label for agent in AGENTS if agent.key == agent_key)


# --- navigation callbacks (they run before the next rerun draws the tabs) --------------


def _open_step(index: int) -> None:
    st.session_state[STEP_KEY] = index
    _open_step_tab()


def _open_step_tab() -> None:
    # Streamlit forgets a widget's value while it is not drawn, so keep the step apart.
    st.session_state[STEP_STATE_KEY] = st.session_state[STEP_KEY]
    step = STEPS[st.session_state[STEP_KEY]]
    if step.tabs:
        st.session_state[TAB_KEY] = tab_label(step.tabs[0])


def _open_tab(agent_key: str) -> None:
    st.session_state[TAB_KEY] = tab_label(agent_key)


def _read_source(source_id: str) -> None:
    st.session_state[EVIDENCE_KEY] = source_id


# --- state -------------------------------------------------------------------------------


def build_state(
    view: MemoryView,
    search: SearchResult | None,
    proposal: Proposal | None,
    scenarios: ScenarioSet | None,
    result: RecommendationResult | None,
    impact: ImpactAssessment | None = None,
) -> DemoState:
    """Collect what the tabs computed for this role into the guide's state."""
    captured_sources = {s.id for s in view.sources() if s.author_label == CAPTURED_AUTHOR}
    captured = [
        d.id
        for d in view.decisions()
        if any(ref.source_id in captured_sources for ref in d.source_refs)
    ]
    scenario = None
    if scenarios is not None:
        rate = (
            result.recommendation.recommended_rate
            if result is not None and result.recommendation.recommended_rate is not None
            else None
        )
        by_rate = {r.surcharge_rate: r for r in scenarios.results}
        middle = scenarios.results[len(scenarios.results) // 2]
        scenario = by_rate.get(rate, middle) if rate is not None else middle
    pilot = None
    record = pilot_for_question(view, proposal.question) if proposal is not None else None
    if record is not None:
        pilot = PilotSummary(
            proposal_id=record.proposal.id,
            status=record.proposal.status.value,
            outcome_id=record.outcome.id if record.outcome else None,
        )
    names_a_rate = result is not None and result.recommendation.recommended_rate is not None
    return DemoState(
        captured_decision_ids=captured,
        asked=search is not None,
        precedent_ids=search.precedent_ids if search else [],
        failed_precedent_ids=[
            p.decision_id
            for p in (search.precedents if search else [])
            if p.outcome_verdict is OutcomeVerdict.FAILED
        ],
        lesson_ids=search.cross_region_ids if search else [],
        exclusion_ticked=None if proposal is None else HVC_EXCLUSION in proposal.exclusions,
        scenario=scenario,
        recommendation=headline(result.recommendation) if result is not None else None,
        names_a_rate=names_a_rate,
        # The same rule Agent 5 applies; a card that names no rate lists no key risks.
        key_risks=len(key_risks(impact)) if names_a_rate else 0,
        pilot=pilot,
    )


# --- drawing -----------------------------------------------------------------------------


def hook_line(view: MemoryView, source: SourceDocument) -> str | None:
    """The line memory cites from this source (decisions first), else its first line."""
    for record in [*view.decisions(), *view.outcomes()]:
        for ref in record.source_refs:
            if ref.source_id == source.id:
                return ref.excerpt
    lines = source_lines(source)
    return lines[0].excerpt if lines else None


def render_hook(view: MemoryView) -> None:
    """The three scattered artifacts, quoted literally."""
    sources = [s for s in (view.source(sid) for sid in HOOK_SOURCE_IDS) if s is not None]
    if not sources:
        return
    for column, source in zip(st.columns(len(sources)), sources, strict=True):
        with column, st.container(border=True):
            st.caption(
                f"{SOURCE_TYPE_LABELS[source.source_type]} · {source.document_date.isoformat()}"
            )
            st.markdown(f"**{source.id}** · {escape_markdown(source.title)}")
            line = hook_line(view, source)
            if line:
                st.text(line)
            st.button(
                "Read in full below",
                key=f"guide_read_{source.id}",
                on_click=_read_source,
                args=(source.id,),
            )


def render_guide(container, view: MemoryView, state: DemoState) -> None:
    """Fill the guide container for the selected step."""
    with container, st.container(border=True):
        st.markdown("#### :material/route: Guided demo · six steps, about five minutes")
        if STEP_KEY not in st.session_state:
            st.session_state[STEP_KEY] = st.session_state.get(STEP_STATE_KEY, 0)
        # A step is ticked once the presenter has moved past it and its live checks are
        # green (the Hook has none), so the row fills in as the demo goes.
        current = st.session_state[STEP_KEY]
        done = [
            i < current and all(c.done for c in checks(s, state))
            for i, s in enumerate(STEPS)
        ]
        index = st.radio(
            "Step",
            list(range(len(STEPS))),
            format_func=lambda i: f"{'✓ ' if done[i] else ''}{i + 1} · {STEPS[i].title}",
            horizontal=True,
            key=STEP_KEY,
            on_change=_open_step_tab,
            label_visibility="collapsed",
        )
        step = STEPS[index]
        where = (
            "in " + " then ".join(f"tab {tab_label(t)}" for t in step.tabs)
            if step.tabs
            else "on this panel"
        )
        st.markdown(
            f"**Step {index + 1} of {len(STEPS)} · {escape_markdown(step.title)}** "
            f"({step.time}, {escape_markdown(where)})"
        )
        if step.key == "hook":
            render_hook(view)
        # What to do and say: folded away in the simple view, so the audience sees a
        # clean screen while the presenter keeps the prompts one click away.
        notes = (
            st.expander("Presenter notes: what to do and say", icon=":material/record_voice_over:")
            if is_simple()
            else st.container()
        )
        with notes:
            st.markdown(
                "\n".join(
                    f"{number}. {escape_markdown(action)}"
                    for number, action in enumerate(step.actions, start=1)
                )
            )
            st.info(
                "\n\n".join(escape_markdown(line) for line in talk_track(step, state)),
                icon="🗣️",
            )
        live = checks(step, state)
        if live:
            st.markdown(
                "**Live checks**\n"
                + "\n".join(
                    f"- {'✅' if check.done else '⬜'} {escape_markdown(check.text)}"
                    for check in live
                )
            )
        buttons = st.columns(2 + len(step.tabs))
        with buttons[0]:
            st.button(
                "◀ Back",
                key="guide_back",
                disabled=index == 0,
                on_click=_open_step,
                args=(max(index - 1, 0),),
            )
        with buttons[1]:
            st.button(
                "Next step ▶",
                key="guide_next",
                type="primary",
                disabled=index == len(STEPS) - 1,
                on_click=_open_step,
                args=(min(index + 1, len(STEPS) - 1),),
            )
        for column, agent_key in zip(buttons[2:], step.tabs, strict=True):
            with column:
                st.button(
                    f"Open {tab_label(agent_key)}",
                    key=f"guide_open_{agent_key}",
                    on_click=_open_tab,
                    args=(agent_key,),
                )
