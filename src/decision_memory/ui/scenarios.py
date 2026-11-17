"""Scenario view (Agent 4): editable inputs, the formulas, and one column per option.

Every figure on this tab is computed by `scenario_agent` from the inputs shown here; the
tab holds no numbers of its own. Inputs are entered as percentages and USD millions and
stored as fractions. Citations go through the escaped Markdown that `provenance` produces.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import plotly.graph_objects as go
import streamlit as st

from decision_memory.agents.impact_agent import HVC_EXCLUSION, SURCHARGE_OPTIONS
from decision_memory.agents.scenario_agent import (
    INFO,
    INPUT_LABELS,
    MARGIN_FORMULA,
    RETENTION_PROXY_NOTE,
    REVENUE_FORMULA,
    SLA_NOT_MODELED,
    churn_label,
    default_inputs,
    retention_proxy,
    scenario_set,
    within_guardrail,
)
from decision_memory.authz import MemoryView
from decision_memory.models import (
    AssumptionNote,
    EvidenceBasis,
    Proposal,
    ScenarioInputs,
    ScenarioResult,
    ScenarioSet,
)
from decision_memory.precedent_search import format_setting
from decision_memory.provenance import resolve_ref
from decision_memory.ui.view import details, expanded_in_full_view, tab_heading

BASELINE_KEY = "scenario_baseline_musd"
EXEMPT_KEY = "scenario_exempt_pct"
MARGIN_KEY = "scenario_margin_pct"
COST_KEY = "scenario_cost_musd"
GUARDRAIL_KEY = "scenario_guardrail_pct"
RESET_KEY = "scenario_reset"
SCENARIO_PROPOSAL_ID = "PRO-DRAFT"
ILLUSTRATION_NOTE = (
    "Illustrations from editable synthetic assumptions, not forecasts. Change any input and "
    "every figure below is recomputed with the formulas shown."
)
SLA_NOTE = f"SLA: {SLA_NOT_MODELED}. The formulas do not include service levels."
EXEMPT_MISMATCH = (
    "The exempt share assumes high-volume contract customers are left out, but the draft "
    "proposal in tab 3 does not exclude them."
)
EXEMPT_ZERO = (
    "The draft proposal in tab 3 excludes high-volume contract customers, but the exempt "
    "share is 0%, so the figures still count their revenue."
)
EXEMPT_MATCH = (
    "The exempt share matches the draft proposal's exclusion of high-volume contract customers."
)
BASIS_LABELS: dict[EvidenceBasis, str] = {
    EvidenceBasis.SOURCE_SUPPORTED: "Source-supported",
    EvidenceBasis.ASSUMED_FOR_DEMO: "Assumed for demo",
}


def churn_key(rate: float) -> str:
    return f"scenario_churn_pct_{round(rate * 10_000)}"


def signed_musd(value: float) -> str:
    """`+$14.00M` / `−$1.10M`."""
    sign = "+" if value >= 0 else "−"
    return f"{sign}${abs(value):,.2f}M"


def pct(value: float) -> str:
    """A fraction as a percentage with two to three decimals, exactly as applied."""
    return f"{format_setting(value * 100)}%"


# Inputs are applied at the precision their boxes show, so what is on screen is what the
# formulas use: percentages to 3 decimals, USD millions to 2.
PERCENT_DECIMALS = 3
MONEY_DECIMALS = 2


def _snap(key: str, decimals: int) -> None:
    """on_change: round a typed value to the precision its box displays."""
    st.session_state[key] = round(st.session_state[key], decimals)


def _number(label: str, key: str, decimals: int, **options: float) -> None:
    st.number_input(
        label,
        format=f"%.{decimals}f",
        key=key,
        on_change=_snap,
        args=(key, decimals),
        **options,
    )


def _applied(key: str, decimals: int) -> float:
    return round(st.session_state[key], decimals)


def _fraction(key: str) -> float:
    return round(_applied(key, PERCENT_DECIMALS) / 100, 8)


def _defaults_by_key(defaults: ScenarioInputs) -> dict[str, float]:
    values = {
        BASELINE_KEY: defaults.baseline_revenue_musd,
        EXEMPT_KEY: defaults.exempt_share * 100,
        MARGIN_KEY: defaults.contribution_margin * 100,
        COST_KEY: defaults.implementation_cost_musd,
        GUARDRAIL_KEY: defaults.churn_guardrail * 100,
    }
    values |= {churn_key(r): f * 100 for r, f in defaults.churn_loss_by_rate.items()}
    return {key: round(value, 6) for key, value in values.items()}


def _restore(defaults: dict[str, float]) -> None:
    for key, value in defaults.items():
        st.session_state[key] = value


def render_inputs(rates: Sequence[float]) -> ScenarioInputs:
    """Editable inputs, seeded with the defaults the first time each one is shown."""
    defaults = _defaults_by_key(default_inputs(rates))
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)

    shared, churn = st.columns(2)
    percent = {"min_value": 0.0, "max_value": 100.0}
    with shared:
        _number(
            INPUT_LABELS["baseline_revenue_musd"],
            BASELINE_KEY,
            MONEY_DECIMALS,
            min_value=0.01,
            step=10.0,
        )
        _number("Exempt share of revenue (%)", EXEMPT_KEY, PERCENT_DECIMALS, step=1.0, **percent)
        _number("Contribution margin (%)", MARGIN_KEY, PERCENT_DECIMALS, step=1.0, **percent)
        _number(
            INPUT_LABELS["implementation_cost_musd"],
            COST_KEY,
            MONEY_DECIMALS,
            min_value=0.0,
            step=0.05,
        )
    with churn:
        for rate in rates:
            label = churn_label(rate).removesuffix(" (share of baseline)")
            _number(
                f"{label} (% of baseline revenue)",
                churn_key(rate),
                PERCENT_DECIMALS,
                step=0.1,
                **percent,
            )
        _number(
            "Churn guardrail (% of baseline revenue)",
            GUARDRAIL_KEY,
            PERCENT_DECIMALS,
            step=0.1,
            **percent,
        )
    st.button("Restore default inputs", key=RESET_KEY, on_click=_restore, args=(defaults,))
    return ScenarioInputs(
        baseline_revenue_musd=_applied(BASELINE_KEY, MONEY_DECIMALS),
        exempt_share=_fraction(EXEMPT_KEY),
        contribution_margin=_fraction(MARGIN_KEY),
        implementation_cost_musd=_applied(COST_KEY, MONEY_DECIMALS),
        churn_guardrail=_fraction(GUARDRAIL_KEY),
        churn_loss_by_rate={rate: _fraction(churn_key(rate)) for rate in rates},
    )


def result_rows(results: Sequence[ScenarioResult], guardrail: float) -> list[dict[str, str]]:
    """One row per option. Every value is a computed result or an input."""
    return [
        {
            "Option": f"+{r.surcharge_rate * 100:g}%",
            "Gross uplift": signed_musd(r.gross_uplift_musd),
            "Churn loss": signed_musd(-r.churn_loss_musd),
            "Revenue change": signed_musd(r.revenue_change_musd),
            "Margin change": signed_musd(r.margin_change_musd),
            "Churn loss (share of baseline)": pct(r.assumptions.churn_loss_fraction),
            f"vs {pct(guardrail)} guardrail": (
                "within" if within_guardrail(r.assumptions, guardrail) else "exceeds"
            ),
            "Revenue retention proxy": pct(retention_proxy(r.assumptions)),
            "SLA": SLA_NOT_MODELED,
        }
        for r in results
    ]


def build_chart(results: Sequence[ScenarioResult]) -> go.Figure:
    labels = [f"+{r.surcharge_rate * 100:g}%" for r in results]
    figure = go.Figure(
        [
            go.Bar(name="Revenue change", x=labels, y=[r.revenue_change_musd for r in results]),
            go.Bar(name="Margin change", x=labels, y=[r.margin_change_musd for r in results]),
        ]
    )
    figure.update_layout(
        barmode="group",
        height=320,
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        yaxis_title="USD M per quarter (synthetic)",
        title="Computed from the inputs above",
    )
    return figure


def exclusion_check(proposal: Proposal, inputs: ScenarioInputs) -> str | None:
    """How the exempt share lines up with the draft proposal's contract exclusion.

    Returns a mismatch message, or None when they agree (or neither applies)."""
    excluded = HVC_EXCLUSION in proposal.exclusions
    if inputs.exempt_share > 0 and not excluded:
        return EXEMPT_MISMATCH
    if inputs.exempt_share == 0 and excluded:
        return EXEMPT_ZERO
    return None


def render_exclusion_check(proposal: Proposal, inputs: ScenarioInputs) -> None:
    mismatch = exclusion_check(proposal, inputs)
    if mismatch is not None:
        st.info(mismatch)
    elif HVC_EXCLUSION in proposal.exclusions:
        st.caption(EXEMPT_MATCH)


def render_note(view: MemoryView, note: AssumptionNote) -> None:
    edited = " · edited" if note.edited else ""
    st.markdown(f"**{note.label}**: {note.display} · {BASIS_LABELS[note.basis]}{edited}")
    st.caption(note.rationale)
    prefix = "Context" if note.basis is EvidenceBasis.ASSUMED_FOR_DEMO else "Source"
    for ref in note.source_refs:
        cited = resolve_ref(view, ref)
        if cited is not None:
            st.markdown(f"- {prefix}: {cited.format_markdown()}")


def render_scenarios(view: MemoryView, proposal: Proposal | None, now: datetime) -> ScenarioSet:
    """Agent 4 tab. Returns the inputs, computed results and input notes (not stored)."""
    tab_heading(INFO)
    st.caption(ILLUSTRATION_NOTE)
    rates = proposal.surcharge_options if proposal is not None else list(SURCHARGE_OPTIONS)
    with details("change the assumptions (every figure below follows them)"):
        inputs = render_inputs(rates)
    if proposal is not None:
        render_exclusion_check(proposal, inputs)

    scenarios = scenario_set(view, inputs, proposal.id if proposal else SCENARIO_PROPOSAL_ID, now)
    results = scenarios.results
    limit = pct(inputs.churn_guardrail)
    for column, result in zip(st.columns(len(results)), results, strict=True):
        with column, st.container(border=True):
            st.markdown(f"##### +{result.surcharge_rate * 100:g}%")
            if within_guardrail(result.assumptions, inputs.churn_guardrail):
                st.badge(f"Within the {limit} limit", icon=":material/check:", color="green")
            else:
                st.badge(f"Over the {limit} limit", icon=":material/warning:", color="red")
            st.metric("Revenue change", signed_musd(result.revenue_change_musd))
            st.metric("Margin change", signed_musd(result.margin_change_musd))
            st.caption(f"{pct(result.assumptions.churn_loss_fraction)} customers lost")
    with details("the formulas and the working for each option"):
        st.markdown("**Formulas** (USD M per quarter; rates and shares as fractions)")
        st.code(f"{REVENUE_FORMULA}\n{MARGIN_FORMULA}", language=None)
        for column, result in zip(st.columns(len(results)), results, strict=True):
            with column:
                st.markdown(f"**+{result.surcharge_rate * 100:g}%**")
                st.text(result.formula_trace.replace("; ", ";\n"))

    st.dataframe(result_rows(results, inputs.churn_guardrail), hide_index=True)
    st.caption(RETENTION_PROXY_NOTE)
    st.caption(SLA_NOTE)
    st.plotly_chart(build_chart(results), key="scenario_chart")

    with st.expander("Where the inputs come from", expanded=expanded_in_full_view()):
        for note in scenarios.notes:
            render_note(view, note)
    return scenarios
