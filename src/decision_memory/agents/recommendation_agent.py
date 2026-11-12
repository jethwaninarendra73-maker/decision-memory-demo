"""Agent 5 — Recommendation (stub; built in Stage 09)."""

from __future__ import annotations

from .base import AgentInfo, not_implemented

INFO = AgentInfo(
    number=5,
    key="recommendation",
    name="Recommendation",
    purpose="Combines precedents, impact and scenarios into cited, advisory guidance.",
    reads="Accessible precedents, the impact assessment, scenario results and guardrails",
    produces="Recommended option, cited reasons, excluded options, risks and an action plan",
    build_stage=9,
)


def recommend(proposal_id: str, role: str) -> None:
    """Produce an advisory recommendation; never executes anything. Not implemented yet."""
    raise not_implemented(INFO)
