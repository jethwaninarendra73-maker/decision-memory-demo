"""Demo authorization: who may see which evidence.

This is a demonstration control, NOT enterprise authentication. The active role is
picked in the UI and anyone can switch it. What this module does guarantee is that,
for a given role, hidden records are filtered out before search, graph building,
impact analysis, recommendation or display ever see them.

Rules (implemented as SQL in `repository.py`):
- A source is visible if its `access_roles` list the role.
- A decision is eligible only if it is confirmed, cites evidence, and every source it
  cites is visible. One hidden source withholds the whole decision.
- An outcome needs an eligible decision and only visible evidence of its own.
- An entity is visible only through an eligible decision linked to it.
- A relation is visible only if both of its ends are.

A withheld record is indistinguishable from one that does not exist.
"""

from __future__ import annotations

from typing import Any

from .models import (
    Decision,
    Entity,
    Outcome,
    Recommendation,
    Relation,
    Role,
    SourceDocument,
    SourceRef,
)
from .repository import Repository

DEFAULT_ROLE = Role.PRICING_DIRECTOR
DEMO_ROLE_NOTICE = (
    "Demonstration control, not authentication: anyone using this app can switch role."
)
ROLE_LABELS: dict[Role, str] = {
    Role.PRICING_DIRECTOR: "Pricing Director",
    Role.ANALYST: "Analyst",
    Role.RESTRICTED_EXEC: "Restricted Executive",
}
APPROVER_ROLES: frozenset[Role] = frozenset({Role.PRICING_DIRECTOR, Role.RESTRICTED_EXEC})


def parse_role(value: object) -> Role | None:
    """Return the `Role` for a string, or None for anything unrecognised (fail closed)."""
    try:
        return Role(value)
    except ValueError:
        return None


def can_view_source(role: Role, source: SourceDocument) -> bool:
    """True if the role is listed on the source. Unknown roles see nothing."""
    parsed = parse_role(role)
    return parsed is not None and parsed in source.access_roles


def can_approve(role: Role) -> bool:
    """True if the demo role may approve or reject a recommendation."""
    return parse_role(role) in APPROVER_ROLES


def accessible_sources(repo: Repository, role: Role, **equals: Any) -> list[SourceDocument]:
    """Sources the role may see, filtered in the query."""
    return repo.find_visible(SourceDocument, role, **equals)


def accessible_decisions(repo: Repository, role: Role, **equals: Any) -> list[Decision]:
    """Confirmed decisions whose every cited source is visible to the role."""
    return repo.find_visible(Decision, role, **equals)


class MemoryView:
    """A read-only window on institutional memory for one demo role.

    Search, the six agents and the UI read through a `MemoryView`, never through the
    repository directly, so permission filtering always happens before retrieval.
    """

    def __init__(self, repo: Repository, role: Role) -> None:
        self._repo = repo
        self.role = Role(role)  # raises ValueError for an unknown role

    # --- evidence -------------------------------------------------------

    def sources(self, **equals: Any) -> list[SourceDocument]:
        return self._repo.find_visible(SourceDocument, self.role, **equals)

    def source(self, source_id: str) -> SourceDocument | None:
        return self._repo.get_visible(SourceDocument, self.role, source_id)

    def can_cite(self, ref: SourceRef) -> bool:
        """True if the source is visible and the excerpt really is at those offsets."""
        source = self.source(ref.source_id)
        return (
            source is not None
            and source.body[ref.start_offset : ref.end_offset] == ref.excerpt
        )

    # --- memory ---------------------------------------------------------

    def decisions(self, **equals: Any) -> list[Decision]:
        return self._repo.find_visible(Decision, self.role, **equals)

    def decision(self, decision_id: str) -> Decision | None:
        return self._repo.get_visible(Decision, self.role, decision_id)

    def outcomes(self, **equals: Any) -> list[Outcome]:
        return self._repo.find_visible(Outcome, self.role, **equals)

    def outcome(self, outcome_id: str) -> Outcome | None:
        return self._repo.get_visible(Outcome, self.role, outcome_id)

    def entities(self, **equals: Any) -> list[Entity]:
        return self._repo.find_visible(Entity, self.role, **equals)

    def relations(self, **equals: Any) -> list[Relation]:
        return self._repo.find_visible(Relation, self.role, **equals)

    # --- advice ---------------------------------------------------------

    def recommendations(self, **equals: Any) -> list[Recommendation]:
        """Stored recommendations whose every cited excerpt this role may see.

        Citations sit inside a JSON column, so this check runs here rather than in SQL.
        A recommendation with any hidden citation is withheld whole.
        """
        return [
            recommendation
            for recommendation in self._repo.find(Recommendation, **equals)
            if all(
                self.can_cite(ref)
                for reason in recommendation.reasons
                for ref in reason.source_refs
            )
        ]

    def recommendation(self, recommendation_id: str) -> Recommendation | None:
        found = self.recommendations(id=recommendation_id)
        return found[0] if found else None
