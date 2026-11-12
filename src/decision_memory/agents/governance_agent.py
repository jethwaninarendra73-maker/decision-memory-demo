"""Agent 6 — Governance (stub; built in Stage 10)."""

from __future__ import annotations

from .base import AgentInfo, not_implemented

INFO = AgentInfo(
    number=6,
    key="governance",
    name="Governance",
    purpose="Records human approval, tracks the pilot and feeds outcomes back into memory.",
    reads="An approved recommendation and weekly KPI entries",
    produces="60-day tracker, milestone status and an outcome linked to the decision",
    build_stage=10,
)


def record_approval(recommendation_id: str, role: str) -> None:
    """Record a simulated human approval or rejection. Not implemented yet."""
    raise not_implemented(INFO)
