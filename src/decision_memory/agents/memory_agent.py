"""Agent 2 — Institutional Memory / Decision Graph (stub; built in Stage 05)."""

from __future__ import annotations

from .base import AgentInfo, not_implemented

INFO = AgentInfo(
    number=2,
    key="memory",
    name="Institutional Memory",
    purpose="Connects confirmed decisions, sources and outcomes into one decision graph.",
    reads="Confirmed decisions, sources and outcomes the active role may see",
    produces="Graph of decisions, markets, segments, systems, risks, stakeholders and outcomes",
    build_stage=5,
)


def build_decision_graph(role: str) -> None:
    """Project permission-filtered SQLite records into a graph. Not implemented yet."""
    raise not_implemented(INFO)
