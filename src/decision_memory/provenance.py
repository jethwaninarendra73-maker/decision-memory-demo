"""Citations and evidence: every factual claim points at a visible source excerpt.

A claim that cannot be fully backed by sources the active role may see is replaced by
"insufficient verifiable evidence". Nothing is invented and nothing hidden is hinted at.

Source documents are untrusted DATA. Their text is only ever quoted; it is never
interpreted as an instruction, and it is escaped before it reaches a Markdown renderer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import date

from .authz import MemoryView
from .models import (
    Contract,
    EvidenceBasis,
    Recommendation,
    Role,
    SourceRef,
    SourceType,
)

INSUFFICIENT_EVIDENCE = "insufficient verifiable evidence"
INSUFFICIENT_EVIDENCE_TEXT = "Insufficient verifiable evidence."
MOCK_LINK_NOTE = "Mock reference only. This demo is not connected to Teams, Jira or any live system."

# Labels say "-style" so nothing implies a real integration.
SOURCE_TYPE_LABELS: dict[SourceType, str] = {
    SourceType.TEAMS_TRANSCRIPT: "Teams-style transcript",
    SourceType.STEERING_DECK: "Steering deck excerpt",
    SourceType.JIRA_TICKET: "Jira-style ticket",
    SourceType.EMAIL: "Email-style message",
    SourceType.SERVICENOW: "ServiceNow-style record",
    SourceType.GOVERNANCE_RECORD: "Governance-style record (generated in this app)",
}

_CITATION = re.compile(r"\[(SRC-[A-Z0-9][A-Z0-9-]*)\]")
_MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$&])")


class ProvenanceError(Exception):
    """Base class for evidence problems."""


class InaccessibleEvidenceError(ProvenanceError):
    """Something cites evidence the active role cannot see or that cannot be verified.

    The message never names the source, so it cannot confirm that a hidden one exists.
    """


def citation_tag(source_id: str) -> str:
    """`SRC-004` -> `[SRC-004]`."""
    return f"[{source_id}]"


def extract_citation_ids(text: str) -> list[str]:
    """Source IDs cited in a piece of text, in order, without duplicates."""
    return list(dict.fromkeys(_CITATION.findall(text)))


def evidence_link(source_id: str, role: Role | None = None) -> str:
    """Relative link to the local evidence detail view inside this app."""
    if role is None:
        return f"?evidence={source_id}"
    return f"?role={Role(role).value}&evidence={source_id}"


def escape_markdown(text: str) -> str:
    """Neutralise Markdown, HTML and LaTeX syntax in untrusted text."""
    return _MARKDOWN_SPECIALS.sub(r"\\\1", text)


class EvidenceItem(Contract):
    """One citation, ready to show: exact source ID, title, type, date, excerpt and links."""

    source_id: str
    title: str
    source_type: SourceType
    source_type_label: str
    document_date: date
    excerpt: str
    start_offset: int
    end_offset: int
    detail_link: str
    mock_link: str

    @property
    def tag(self) -> str:
        return citation_tag(self.source_id)

    def format(self) -> str:
        """Plain-text citation line."""
        return (
            f"{self.tag} {self.title} ({self.source_type_label}, "
            f'{self.document_date.isoformat()}): "{self.excerpt}"'
        )

    def format_markdown(self) -> str:
        """Markdown citation whose tag links to the local evidence view. Text is escaped."""
        return (
            f"[\\[{self.source_id}\\]]({self.detail_link}) {escape_markdown(self.title)} "
            f"({self.source_type_label}, {self.document_date.isoformat()}): "
            f"“{escape_markdown(self.excerpt)}”"
        )


class Claim(Contract):
    """A statement plus the evidence behind it, or the insufficient-evidence message."""

    text: str
    evidence: list[EvidenceItem]
    supported: bool

    def render(self) -> str:
        """The statement followed by its citation tags."""
        if not self.supported:
            return self.text
        tags = "".join(dict.fromkeys(item.tag for item in self.evidence))
        return f"{self.text} {tags}"


def resolve_ref(view: MemoryView, ref: SourceRef) -> EvidenceItem | None:
    """Turn a stored reference into a displayable citation.

    Returns None if the source is not visible to the role, does not exist, or the
    excerpt is not at the stated offsets. The three cases are deliberately identical.
    """
    source = view.source(ref.source_id)
    if source is None or source.body[ref.start_offset : ref.end_offset] != ref.excerpt:
        return None
    return EvidenceItem(
        source_id=source.id,
        title=source.title,
        source_type=source.source_type,
        source_type_label=SOURCE_TYPE_LABELS[source.source_type],
        document_date=source.document_date,
        excerpt=ref.excerpt,
        start_offset=ref.start_offset,
        end_offset=ref.end_offset,
        detail_link=evidence_link(source.id, view.role),
        mock_link=source.mock_link,
    )


def resolve_all(view: MemoryView, refs: Iterable[SourceRef]) -> list[EvidenceItem] | None:
    """Citations for every reference, or None if any single one cannot be shown."""
    items = []
    for ref in refs:
        item = resolve_ref(view, ref)
        if item is None:
            return None
        items.append(item)
    return items


def _insufficient() -> Claim:
    return Claim(text=INSUFFICIENT_EVIDENCE_TEXT, evidence=[], supported=False)


def supported_claim(view: MemoryView, text: str, refs: Sequence[SourceRef]) -> Claim:
    """Return the statement with its citations, or the insufficient-evidence claim.

    The statement is dropped entirely unless it has at least one reference and every
    reference resolves for this role, so unsupported or hidden facts are never shown.
    """
    items = resolve_all(view, refs) if refs else None
    if not items:
        return _insufficient()
    return Claim(text=text, evidence=items, supported=True)


def decision_evidence(view: MemoryView, decision_id: str) -> list[EvidenceItem]:
    """Citations behind a decision; empty if the role may not see the decision."""
    decision = view.decision(decision_id)
    if decision is None:
        return []
    return resolve_all(view, decision.source_refs) or []


def decision_claim(view: MemoryView, decision_id: str) -> Claim:
    """What was decided, with citations, or the insufficient-evidence claim."""
    decision = view.decision(decision_id)
    if decision is None:
        return _insufficient()
    text = f"{decision.decision_date.isoformat()}, {decision.market}: {decision.statement}"
    return supported_claim(view, text, decision.source_refs)


def outcome_claim(view: MemoryView, outcome_id: str) -> Claim:
    """What happened afterwards, with citations, or the insufficient-evidence claim."""
    outcome = view.outcome(outcome_id)
    if outcome is None:
        return _insufficient()
    text = f"Outcome ({outcome.verdict.value}): {outcome.explanation}"
    return supported_claim(view, text, outcome.source_refs)


def verify_recommendation(view: MemoryView, recommendation: Recommendation) -> None:
    """Refuse a recommendation unless every source-supported reason is verifiable.

    Call this before storing or showing a recommendation for `view.role`. Reasons
    labelled `assumed_for_demo` need no citation; any citation they do carry is checked.
    """
    for reason in recommendation.reasons:
        needs_evidence = reason.basis is EvidenceBasis.SOURCE_SUPPORTED
        if (needs_evidence and not reason.source_refs) or (
            reason.source_refs and resolve_all(view, reason.source_refs) is None
        ):
            raise InaccessibleEvidenceError(
                "A reason cites evidence that is not available to this role "
                "or cannot be verified."
            )
