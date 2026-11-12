"""Agent 3 — Impact Analysis (stub; built in Stage 07)."""

from __future__ import annotations

from .base import AgentInfo, not_implemented

INFO = AgentInfo(
    number=3,
    key="impact",
    name="Impact Analysis",
    purpose="Scopes a proposal: who and what it touches, with rough cost and effort.",
    reads="The proposal and accessible precedents",
    produces="Affected teams, systems and customer groups; each item source-backed or assumed",
    build_stage=7,
)


def assess_impact(proposal_id: str, precedent_ids: list[str], role: str) -> None:
    """Build an impact assessment for a proposal. Not implemented yet."""
    raise not_implemented(INFO)
