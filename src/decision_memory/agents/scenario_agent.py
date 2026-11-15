"""Agent 4 — Scenario Simulation.

Compares surcharge options with two visible formulas over editable synthetic inputs:

    revenue_change = baseline × rate × (1 − exempt_share) − baseline × churn_loss_fraction
    margin_change  = revenue_change × contribution_margin − implementation_cost

Money is USD millions per quarter; rates and shares are fractions. The calculator is pure:
same inputs, same figures, no randomness, no clock and no database. Nothing here is a
forecast; every headline figure is computed, never stored as a constant.

`assumption_notes` says where each default input comes from. A default stated in a
visible fixture source is `source_supported` and cites it; anything else, and any value
edited in the app, is `assumed_for_demo`.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from ..authz import MemoryView
from ..models import (
    AssumptionNote,
    EvidenceBasis,
    ScenarioAssumptions,
    ScenarioInputs,
    ScenarioResult,
    ScenarioSet,
    SourceRef,
)
from .base import AgentInfo
from .impact_agent import SURCHARGE_OPTIONS

INFO = AgentInfo(
    number=4,
    key="scenario",
    name="Scenario Simulation",
    purpose="Compares +3%, +5% and +7% surcharges side by side using visible formulas.",
    reads="Editable synthetic assumptions",
    produces="Revenue and margin change per option, with the formula and inputs shown",
    build_stage=8,
)

REVENUE_FORMULA = (
    "revenue_change = baseline × rate × (1 − exempt_share) − baseline × churn_loss_fraction"
)
MARGIN_FORMULA = "margin_change = revenue_change × contribution_margin − implementation_cost"
RETENTION_PROXY_NOTE = (
    "Revenue retention proxy = 1 − churn loss fraction: the share of baseline revenue not "
    "lost to churn. A labelled proxy, not a measure of customer retention."
)
SLA_NOT_MODELED = "unchanged / not modeled"
_ROUND = 6  # decimals kept in results: removes float noise, far below any displayed digit

# Default churn loss per option (fraction of baseline revenue). Other rates are read off
# the straight lines between these points, starting from no churn at 0%, and continue the
# last slope beyond the highest point.
DEFAULT_CHURN: dict[float, float] = {0.03: 0.006, 0.05: 0.010, 0.07: 0.016}


def default_churn(rate: float, points: Mapping[float, float] = DEFAULT_CHURN) -> float:
    """Churn loss for `rate` by linear interpolation through (0, 0) and `points`."""
    knots = sorted({0.0: 0.0, **points}.items())
    for (x0, y0), (x1, y1) in itertools.pairwise(knots):
        if rate <= x1:
            return round(max(0.0, y0 + (y1 - y0) * (rate - x0) / (x1 - x0)), _ROUND)
    (x0, y0), (x1, y1) = knots[-2], knots[-1]
    return round(min(1.0, y1 + (y1 - y0) * (rate - x1) / (x1 - x0)), _ROUND)


def default_inputs(rates: Iterable[float] = SURCHARGE_OPTIONS) -> ScenarioInputs:
    """The synthetic defaults, with a churn loss for each requested option."""
    return ScenarioInputs(
        baseline_revenue_musd=400.0,
        exempt_share=0.10,
        contribution_margin=0.70,
        implementation_cost_musd=0.3,
        churn_guardrail=0.012,
        churn_loss_by_rate={rate: default_churn(rate) for rate in sorted(set(rates))},
    )


DEFAULT_INPUTS = default_inputs()


# --- pure formulas -------------------------------------------------------------


def gross_uplift(rate: float, a: ScenarioAssumptions) -> float:
    """baseline × rate × (1 − exempt_share)."""
    return round(a.baseline_revenue_musd * rate * (1 - a.exempt_share), _ROUND)


def churn_loss(a: ScenarioAssumptions) -> float:
    """baseline × churn_loss_fraction."""
    return round(a.baseline_revenue_musd * a.churn_loss_fraction, _ROUND)


def revenue_change(rate: float, a: ScenarioAssumptions) -> float:
    """Gross uplift minus churn loss."""
    return round(gross_uplift(rate, a) - churn_loss(a), _ROUND)


def margin_change(rate: float, a: ScenarioAssumptions) -> float:
    """Revenue change × contribution margin − implementation cost."""
    return round(
        revenue_change(rate, a) * a.contribution_margin - a.implementation_cost_musd, _ROUND
    )


def retention_proxy(a: ScenarioAssumptions) -> float:
    """1 − churn loss fraction. A proxy; see `RETENTION_PROXY_NOTE`."""
    return round(1 - a.churn_loss_fraction, _ROUND)


def within_guardrail(a: ScenarioAssumptions, guardrail: float) -> bool:
    """True if the option's churn loss does not exceed the guardrail."""
    return a.churn_loss_fraction <= guardrail + 1e-12


def _num(value: float) -> str:
    return f"{value:,.2f}"


def _pct(value: float) -> str:
    return f"{value * 100:g}%"


def formula_trace(rate: float, a: ScenarioAssumptions) -> str:
    """Both formulas with this option's numbers filled in."""
    gross, loss = gross_uplift(rate, a), churn_loss(a)
    revenue = revenue_change(rate, a)
    return (
        f"revenue = {_num(a.baseline_revenue_musd)} × {_pct(rate)} × (1 − {_pct(a.exempt_share)})"
        f" − {_num(a.baseline_revenue_musd)} × {_pct(a.churn_loss_fraction)}"
        f" = {_num(gross)} − {_num(loss)} = {_num(revenue)}; "
        f"margin = {_num(revenue)} × {_pct(a.contribution_margin)} − "
        f"{_num(a.implementation_cost_musd)} = {_num(margin_change(rate, a))} (USD M per quarter)"
    )


def _scenario_id(proposal_id: str, rate: float) -> str:
    return f"SCN-{proposal_id.removeprefix('PRO-')}-{round(rate * 10_000)}BP"


def simulate_scenarios(
    inputs: ScenarioInputs, proposal_id: str, now: datetime
) -> list[ScenarioResult]:
    """One computed `ScenarioResult` per option in `inputs`, lowest rate first. Not stored."""
    results = []
    for rate in inputs.rates:
        a = inputs.assumptions(rate)
        results.append(
            ScenarioResult(
                id=_scenario_id(proposal_id, rate),
                proposal_id=proposal_id,
                surcharge_rate=rate,
                assumptions=a,
                gross_uplift_musd=gross_uplift(rate, a),
                churn_loss_musd=churn_loss(a),
                revenue_change_musd=revenue_change(rate, a),
                margin_change_musd=margin_change(rate, a),
                formula_trace=formula_trace(rate, a),
                created_at=now,
                updated_at=now,
            )
        )
    return results


# --- where the defaults come from ----------------------------------------------

_Cite = tuple[str, str]  # (source ID, exact excerpt)

# Each default with its rationale and the excerpts that state it. Excerpts must occur
# exactly once in the source body; they are resolved for the active role at run time.
_SOURCED: dict[str, tuple[str, list[_Cite]]] = {
    "exempt_share": (
        (
            "Excluding high-volume contract customers was put at roughly a tenth of the eligible "
            "revenue base."
        ),
        [
            (
                "SRC-022",
                "Excluding them takes roughly a tenth of the eligible revenue base out of scope",
            )
        ],
    ),
    "churn_guardrail": (
        "The European churn guardrail: pause and review above 1.2% churn-related revenue loss.",
        [
            (
                "SRC-014",
                "pause and review if churn-related revenue loss exceeds 1.2% of baseline revenue",
            )
        ],
    ),
    "churn:0.03": (
        "Planned churn loss for earlier 3% surcharges (Benelux pilot, Germany re-entry).",
        [
            ("SRC-004", "Plan is +$1.5M a quarter with churn loss at 0.6% of baseline."),
            ("SRC-015", "plan is +$2.2M a quarter with churn loss of 0.6% of baseline"),
        ],
    ),
    "churn:0.05": (
        "Planned churn loss for earlier 5% surcharges (France, Italy).",
        [
            ("SRC-006", "At 5% we plan +$3.2M a quarter and churn loss of 1.0% of baseline."),
            ("SRC-011", "Plan was +$2.4M a quarter with churn loss of 1.0% of baseline."),
        ],
    ),
}
_ASSUMED: dict[str, tuple[str, list[_Cite]]] = {
    "baseline_revenue_musd": (
        (
            "Synthetic quarterly freight revenue for the European customers in scope. Not taken "
            "from any source."
        ),
        [],
    ),
    "contribution_margin": (
        "Synthetic share of extra revenue that reaches margin. Not taken from any source.",
        [],
    ),
    "implementation_cost_musd": (
        "Synthetic one-off cost charged to the first quarter. Not taken from any source.",
        [],
    ),
    "churn:0.07": (
        (
            "No precedent planned this value. Iberia planned 1.2% at 7% and lost 2.3%; 1.6% is a "
            "demo assumption between the two. The citations are context, not support."
        ),
        [
            ("SRC-009", "The plan assumes +$2.6M a quarter and churn loss of 1.2% of baseline."),
            ("SRC-010", "Churn loss reached 2.3% of baseline against a 1.2% plan"),
        ],
    ),
}

INPUT_LABELS: dict[str, str] = {
    "baseline_revenue_musd": "Baseline revenue (USD M per quarter)",
    "exempt_share": "Exempt share of revenue",
    "contribution_margin": "Contribution margin",
    "implementation_cost_musd": "Implementation cost (USD M, one-off)",
    "churn_guardrail": "Churn guardrail (share of baseline)",
}
EDITED_RATIONALE = (
    "Edited in this session, so it is a demo assumption: the default's rationale and "
    "citations no longer apply."
)
UNSOURCED_RATIONALE = "Synthetic default. Not taken from any source available here."
INTERPOLATED_RATIONALE = (
    "No default for this option: read off the straight lines between the default churn "
    "losses (0% at 0%, 0.6% at 3%, 1.0% at 5%, 1.6% at 7%)."
)


def churn_key(rate: float) -> str:
    return f"churn:{rate:g}"


def churn_label(rate: float) -> str:
    return f"Churn loss at +{_pct(rate)} (share of baseline)"


def _refs(view: MemoryView, cites: Sequence[_Cite]) -> list[SourceRef]:
    """Excerpts the role can see. A hidden or missing source is simply left out."""
    refs = []
    for source_id, excerpt in cites:
        source = view.source(source_id)
        if source is not None and source.body.count(excerpt) == 1:
            refs.append(source.excerpt_ref(excerpt))
    return refs


def _assumed(
    key: str,
    label: str,
    value: float,
    display: str,
    rationale: str,
    refs: list[SourceRef] | None = None,
    *,
    edited: bool = False,
) -> AssumptionNote:
    return AssumptionNote(
        key=key,
        label=label,
        value=value,
        display=display,
        edited=edited,
        basis=EvidenceBasis.ASSUMED_FOR_DEMO,
        rationale=rationale,
        source_refs=refs or [],
    )


def _note(
    view: MemoryView, key: str, label: str, value: float, display: str, default: float | None
) -> AssumptionNote:
    """Supported when the value is its default and that default's source is visible."""
    if default is None:
        return _assumed(key, label, value, display, INTERPOLATED_RATIONALE)
    if not math.isclose(value, default, rel_tol=0, abs_tol=1e-9):
        return _assumed(key, label, value, display, EDITED_RATIONALE, edited=True)
    if key in _SOURCED:
        rationale, cites = _SOURCED[key]
        refs = _refs(view, cites)
        if refs:
            return AssumptionNote(
                key=key,
                label=label,
                value=value,
                display=display,
                basis=EvidenceBasis.SOURCE_SUPPORTED,
                rationale=rationale,
                source_refs=refs,
            )
        return _assumed(key, label, value, display, UNSOURCED_RATIONALE)
    rationale, cites = _ASSUMED.get(key, (UNSOURCED_RATIONALE, []))
    return _assumed(key, label, value, display, rationale, _refs(view, cites))


def assumption_notes(
    view: MemoryView, inputs: ScenarioInputs, defaults: ScenarioInputs | None = None
) -> list[AssumptionNote]:
    """One note per input: its value, basis, rationale and any visible citations.

    `defaults` is what the inputs started from (by default `default_inputs` for the same
    options). A value that differs from its default is reported as edited; the churn loss
    of an option without its own default is reported as interpolated until edited.
    """
    defaults = defaults or default_inputs(inputs.rates)
    shared = [
        ("baseline_revenue_musd", f"${_num(inputs.baseline_revenue_musd)}M"),
        ("exempt_share", _pct(inputs.exempt_share)),
        ("contribution_margin", _pct(inputs.contribution_margin)),
        ("implementation_cost_musd", f"${_num(inputs.implementation_cost_musd)}M"),
        ("churn_guardrail", _pct(inputs.churn_guardrail)),
    ]
    notes = [
        _note(view, key, INPUT_LABELS[key], getattr(inputs, key), display, getattr(defaults, key))
        for key, display in shared
    ]
    for rate in inputs.rates:
        value = inputs.churn_loss_by_rate[rate]
        default = defaults.churn_loss_by_rate.get(rate)
        key, label, display = churn_key(rate), churn_label(rate), _pct(value)
        if rate not in DEFAULT_CHURN and default is not None and math.isclose(value, default):
            notes.append(_assumed(key, label, value, display, INTERPOLATED_RATIONALE))
        else:
            notes.append(_note(view, key, label, value, display, default))
    return notes


def scenario_set(
    view: MemoryView, inputs: ScenarioInputs, proposal_id: str, now: datetime
) -> ScenarioSet:
    """Results and input notes computed together from one set of inputs. Not stored."""
    return ScenarioSet(
        inputs=inputs,
        results=simulate_scenarios(inputs, proposal_id, now),
        notes=assumption_notes(view, inputs),
    )
