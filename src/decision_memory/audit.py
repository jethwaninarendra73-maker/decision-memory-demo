"""Audited writes: capture, review and approval each leave an audit event.

Each operation writes the record and its audit event in one transaction, so there is
never a change without a trail or a trail without a change. Audit details are neutral
labels; they never copy titles or excerpts, so the log cannot leak hidden content.

Nothing here performs a business action. An approval is a stored, simulated record.
"""

from __future__ import annotations

from datetime import datetime

from .authz import MemoryView, can_approve
from .models import (
    Approval,
    ApprovalVerdict,
    AuditAction,
    AuditEvent,
    Decision,
    DecisionStatus,
    Recommendation,
    Role,
)
from .provenance import InaccessibleEvidenceError, verify_recommendation
from .repository import Repository


class PermissionDeniedError(Exception):
    """The acting demo role may not perform this change. The message stays generic."""


def log_event(
    repo: Repository,
    *,
    now: datetime,
    actor_label: str,
    role: Role | None,
    action: AuditAction,
    entity_type: str,
    entity_id: str,
    detail: str = "",
) -> AuditEvent:
    """Append one audit event with the next free ID."""
    event = AuditEvent(
        id=repo.next_id(AuditEvent),
        at=now,
        actor_label=actor_label,
        role=role,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        detail=detail,
        created_at=now,
        updated_at=now,
    )
    return repo.add(event)


_NOT_AVAILABLE = "This decision is not available to this role."


def _citable(repo: Repository, role: Role, decision: Decision) -> bool:
    view = MemoryView(repo, role)
    return all(view.can_cite(ref) for ref in decision.source_refs)


def _require_citable(repo: Repository, role: Role, decision: Decision) -> None:
    if not _citable(repo, role, decision):
        raise PermissionDeniedError(
            "This change cites evidence that is not available to this role "
            "or cannot be verified."
        )


def capture_decision(
    repo: Repository, draft: Decision, *, actor_label: str, role: Role, now: datetime
) -> Decision:
    """Store a newly captured decision as a draft and record who captured it."""
    if draft.status is not DecisionStatus.DRAFT:
        raise ValueError("A captured decision starts as a draft; confirm it in review.")
    with repo.transaction():
        _require_citable(repo, role, draft)
        stored = repo.add(draft.model_copy(update={"created_at": now, "updated_at": now}))
        log_event(
            repo,
            now=now,
            actor_label=actor_label,
            role=role,
            action=AuditAction.CAPTURE,
            entity_type="decision",
            entity_id=stored.id,
            detail="Draft captured; awaiting human confirmation.",
        )
    return stored


def review_decision(
    repo: Repository,
    decision: Decision,
    *,
    actor_label: str,
    role: Role,
    now: datetime,
    note: str = "",
) -> Decision:
    """Save a reviewed decision. Draft -> confirmed is logged as `confirm`, else `edit`.

    `note` is appended to the audit detail. Keep it neutral: field names, never content.
    """
    with repo.transaction():
        existing = repo.get(Decision, decision.id)
        # A decision the reviewer cannot see gets the same answer as one that does not exist.
        if existing is None or not _citable(repo, role, existing):
            raise PermissionDeniedError(_NOT_AVAILABLE)
        _require_citable(repo, role, decision)
        if (
            existing.status is DecisionStatus.CONFIRMED
            and decision.status is not DecisionStatus.CONFIRMED
        ):
            raise ValueError("A confirmed decision cannot go back to draft.")
        confirming = (
            existing.status is DecisionStatus.DRAFT
            and decision.status is DecisionStatus.CONFIRMED
        )
        stored = repo.save(decision.model_copy(update={"updated_at": now}))
        log_event(
            repo,
            now=now,
            actor_label=actor_label,
            role=role,
            action=AuditAction.CONFIRM if confirming else AuditAction.EDIT,
            entity_type="decision",
            entity_id=stored.id,
            detail=" ".join(
                part
                for part in (
                    "Confirmed by a human reviewer; now part of institutional memory."
                    if confirming
                    else "Edited in review.",
                    note,
                )
                if part
            ),
        )
    return stored


def record_approval(repo: Repository, approval: Approval, *, now: datetime) -> Approval:
    """Store a simulated human approval or rejection and record it in the audit trail."""
    if not can_approve(approval.role):
        raise PermissionDeniedError("This demo role cannot approve or reject recommendations.")
    with repo.transaction():
        recommendation = repo.get(Recommendation, approval.recommendation_id)
        if recommendation is None:
            raise PermissionDeniedError("This recommendation is not available to this role.")
        try:
            verify_recommendation(MemoryView(repo, approval.role), recommendation)
        except InaccessibleEvidenceError:
            raise PermissionDeniedError(
                "This recommendation is not available to this role."
            ) from None
        stored = repo.add(approval.model_copy(update={"created_at": now, "updated_at": now}))
        approved = stored.verdict is ApprovalVerdict.APPROVED
        log_event(
            repo,
            now=now,
            actor_label=stored.approver_label,
            role=stored.role,
            action=AuditAction.APPROVE if approved else AuditAction.REJECT,
            entity_type="recommendation",
            entity_id=stored.recommendation_id,
            detail=(
                "Simulated human approval recorded. No business action was executed."
                if approved
                else "Simulated human rejection recorded."
            ),
        )
    return stored
