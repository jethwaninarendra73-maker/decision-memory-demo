"""Shared description type for the six logical agents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentInfo:
    """Static description of one agent, used by the UI shell and by tests."""

    number: int
    key: str
    name: str
    purpose: str
    reads: str
    produces: str
    build_stage: int

    @property
    def label(self) -> str:
        return f"{self.number} · {self.name}"


def not_implemented(info: AgentInfo) -> NotImplementedError:
    """Uniform error for stubbed agent entry points."""
    return NotImplementedError(
        f"Agent {info.number} ({info.name}) is a stub; it is built in Stage {info.build_stage:02d}."
    )
