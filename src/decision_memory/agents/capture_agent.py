"""Agent 1 — Decision Capture (stub; built in Stage 04)."""

from __future__ import annotations

from .base import AgentInfo, not_implemented

INFO = AgentInfo(
    number=1,
    key="capture",
    name="Decision Capture",
    purpose="Extracts a decision draft from a synthetic meeting transcript for human review.",
    reads="Synthetic Teams-style transcript (.txt / .md, pasted or fixture)",
    produces="Decision draft with stakeholders, rationale, risks and exact evidence offsets",
    build_stage=4,
)


def extract_decision_draft(transcript: str) -> None:
    """Parse `transcript` into a reviewable decision draft. Not implemented yet."""
    raise not_implemented(INFO)
