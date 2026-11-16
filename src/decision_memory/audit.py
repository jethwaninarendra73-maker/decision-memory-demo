"""Audited writes: capture, review, approval and pilot tracking each leave an audit event.

Each operation writes the record and its audit event in one transaction, so there is
never a change without a trail or a trail without a change. Audit details are neutral
labels; they never copy titles or excerpts, so the log cannot leak hidden content.

Nothing here performs a business action. An approval is a stored, simulated record.
"""

from __future__ import annotations

from datetime import datetime

from .authz import MemoryView, can_approve
from .models import (
    PROPOSAL_TRANSITIONS,
    Approval,
    ApprovalVerdict,
    AuditAction,
    AuditEvent,
    Decision,
    DecisionStatus,
    KPIUpdate,
    Milestone,
    Outcome,
    Proposal,
    ProposalStatus,
    Recommendation,
    Role,
)
from .provenance import InaccessibleEvidenceError, verify_recommendation
from .repository import Repository


class PermissionDeniedError(Exception):
    """The acting demo role may not perform this change. The message stays generic."""


class InvalidTransitionError(ValueError):
    """The proposal cannot move to that status from where it is."""


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


# --- pilot tracking (Agent 6) ---------------------------------------------------------------

_PILOT_NOT_AVAILABLE = "This pilot is not available to this role."
_STATUS_ACTIONS: dict[ProposalStatus, AuditAction] = {
    ProposalStatus.APPROVED: AuditAction.APPROVE,
    ProposalStatus.REJECTED: AuditAction.REJECT,
    ProposalStatus.IN_PROGRESS: AuditAction.KPI_UPDATE,  # the first weekly KPI starts a pilot
    ProposalStatus.COMPLETED: AuditAction.CLOSE,
}
# Moves that only an approver role may make; starting a pilot follows from a KPI entry.
_APPROVER_MOVES = frozenset(
    {ProposalStatus.APPROVED, ProposalStatus.REJECTED, ProposalStatus.COMPLETED}
)


def _visible_proposal(repo: Repository, role: Role, proposal_id: str) -> Proposal:
    proposal = MemoryView(repo, role).proposal(proposal_id)
    if proposal is None:  # hidden and missing get the same answer
        raise PermissionDeniedError(_PILOT_NOT_AVAILABLE)
    return proposal


def set_proposal_status(
    repo: Repository,
    proposal_id: str,
    to: ProposalStatus,
    *,
    actor_label: str,
    role: Role,
    now: datetime,
    detail: str = "",
) -> Proposal:
    """Move a stored proposal along its state machine, or refuse. Audited.

    Allowed: draft -> approved | rejected, approved -> in_progress, in_progress -> completed.
    """
    if to in _APPROVER_MOVES and not can_approve(role):
        raise PermissionDeniedError("This demo role cannot approve, reject or close a pilot.")
    with repo.transaction():
        proposal = _visible_proposal(repo, role, proposal_id)
        if to not in PROPOSAL_TRANSITIONS[proposal.status]:
            raise InvalidTransitionError(
                f"A proposal cannot move from {proposal.status.value} to {to.value}."
            )
        recorded = [
            kpi
            for kpi in repo.find(KPIUpdate, proposal_id=proposal.id)
            if kpi.actual_revenue_uplift_musd is not None
        ]
        if to is ProposalStatus.IN_PROGRESS and not recorded:
            raise InvalidTransitionError("A pilot starts with its first recorded weekly KPI.")
        stored = repo.save(proposal.model_copy(update={"status": to, "updated_at": now}))
        log_event(
            repo,
            now=now,
            actor_label=actor_label,
            role=role,
            action=_STATUS_ACTIONS[to],
            entity_type="proposal",
            entity_id=stored.id,
            detail=" ".join(
                part for part in (f"Status {proposal.status.value} -> {to.value}.", detail) if part
            ),
        )
    return stored


def record_kpi_update(
    repo: Repository, kpi: KPIUpdate, *, actor_label: str, role: Role, now: datetime
) -> KPIUpdate:
    """Fill one open weekly KPI slot. A recorded week is never overwritten. Audited."""
    if kpi.actual_revenue_uplift_musd is None or kpi.actual_churn_loss_fraction is None:
        raise ValueError("A KPI update records both the revenue and the churn figure.")
    with repo.transaction():
        _visible_proposal(repo, role, kpi.proposal_id)
        slot = repo.get(KPIUpdate, kpi.id)
        if slot is None or (slot.proposal_id, slot.week) != (kpi.proposal_id, kpi.week):
            raise ValueError("There is no such weekly KPI slot for this pilot.")
        if slot.actual_revenue_uplift_musd is not None:
            raise ValueError(f"Week {kpi.week} is already recorded; entries are not overwritten.")
        planned = (slot.planned_revenue_uplift_musd, slot.planned_churn_loss_fraction)
        if (kpi.planned_revenue_uplift_musd, kpi.planned_churn_loss_fraction) != planned:
            raise ValueError("The plan is fixed at approval and cannot be edited.")
        stored = repo.save(kpi.model_copy(update={"updated_at": now}))
        log_event(
            repo,
            now=now,
            actor_label=actor_label,
            role=role,
            action=AuditAction.KPI_UPDATE,
            entity_type="kpi_update",
            entity_id=stored.id,
            detail=f"Week {stored.week} KPI recorded (demo-entered values)."
            + (" Flagged for review." if stored.risk_flag else ""),
        )
    return stored


def update_milestones(
    repo: Repository,
    milestones: list[Milestone],
    *,
    actor_label: str,
    role: Role,
    now: datetime,
) -> list[Milestone]:
    """Save milestone states that changed, one audit event each. Returns the changed ones."""
    changed = []
    with repo.transaction():
        for milestone in milestones:
            existing = repo.get(Milestone, milestone.id)
            if existing is None:
                raise ValueError("There is no such milestone.")
            _visible_proposal(repo, role, existing.proposal_id)
            if existing.state is milestone.state:
                continue
            changed.append(
                repo.save(existing.model_copy(update={"state": milestone.state, "updated_at": now}))
            )
            log_event(
                repo,
                now=now,
                actor_label=actor_label,
                role=role,
                action=AuditAction.EDIT,
                entity_type="milestone",
                entity_id=milestone.id,
                detail=f"State {existing.state.value} -> {milestone.state.value}.",
            )
    return changed


def record_outcome(
    repo: Repository, outcome: Outcome, *, actor_label: str, role: Role, now: datetime
) -> Outcome:
    """Attach an outcome to a decision in memory, from a closed pilot. Audited."""
    if not can_approve(role):
        raise PermissionDeniedError("This demo role cannot close a pilot.")
    with repo.transaction():
        view = MemoryView(repo, role)
        if view.decision(outcome.decision_id) is None:
            raise PermissionDeniedError(_NOT_AVAILABLE)
        if not outcome.source_refs or not all(view.can_cite(ref) for ref in outcome.source_refs):
            raise PermissionDeniedError(
                "This change cites evidence that is not available to this role "
                "or cannot be verified."
            )
        stored = repo.add(outcome.model_copy(update={"created_at": now, "updated_at": now}))
        log_event(
            repo,
            now=now,
            actor_label=actor_label,
            role=role,
            action=AuditAction.CLOSE,
            entity_type="outcome",
            entity_id=stored.id,
            detail=f"Outcome recorded on {stored.decision_id} from a closed pilot.",
        )
    return stored
