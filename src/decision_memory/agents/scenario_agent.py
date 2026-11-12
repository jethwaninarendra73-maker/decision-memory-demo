"""Agent 4 — Scenario Simulation (stub; built in Stage 08)."""

from __future__ import annotations

from .base import AgentInfo, not_implemented

INFO = AgentInfo(
    number=4,
    key="scenario",
    name="Scenario Simulation",
    purpose="Compares +3%, +5% and +7% surcharges side by side using visible formulas.",
    reads="Editable synthetic assumptions",
    produces="Revenue and margin change per option, with the formula and inputs shown",
    build_stage=8,
)


def simulate_scenarios(surcharge_rates: tuple[float, ...] = (0.03, 0.05, 0.07)) -> None:
    """Compute one deterministic result per surcharge rate. Not implemented yet."""
    raise not_implemented(INFO)
