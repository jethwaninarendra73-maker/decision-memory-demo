"""The six logical agents, in demo order."""

from __future__ import annotations

from . import (
    capture_agent,
    governance_agent,
    impact_agent,
    memory_agent,
    recommendation_agent,
    scenario_agent,
)
from .base import AgentInfo

AGENTS: tuple[AgentInfo, ...] = (
    capture_agent.INFO,
    memory_agent.INFO,
    impact_agent.INFO,
    scenario_agent.INFO,
    recommendation_agent.INFO,
    governance_agent.INFO,
)

__all__ = ["AGENTS", "AgentInfo"]
