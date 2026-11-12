"""Audited writes: capture, review and approval each leave exactly one audit event."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from decision_memory.audit import (
    PermissionDeniedError,
    capture_decision,
    log_event,
    record_approval,
    review_decision,
)
from decision_memory.authz import MemoryView
from decision_memory.models import (
    Approval,
    ApprovalVerdict,
    AuditAction,
    AuditEvent,
    Decision,
    DecisionStatus,
    Proposal,
    Recommendation,
    RecommendationReason,
    RecommendationType,
    Role,
    SourceDocument,
)
from decision_memory.repository import DuplicateRecordError, Repository

from conftest import HIDDEN_FROM_ANALYST, STAMPS, make_decision

LATER = datetime(2026, 11, 13, 14, 30, tzinfo=UTC)
ANALYST = {"actor_label": "Demo Analyst", "role": Role.ANALYST}
DIRECTOR = {"actor_label": "Demo Pricing Director", "role": Role.PRICING_DIRECTOR}


def draft_citing(repo: Repository, source_id: str, excerpt: str, decision_id: str = "DEC-900") -> Decision:
    source = repo.get(SourceDocument, source_id)
    return make_decision(
        source,
        decision_id,
        status=DecisionStatus.DRAFT,
        source_refs=[source.excerpt_ref(excerpt)],
    )


def store_recommendation(repo: Repository, source_id: str, excerpt: str) -> Recommendation:
    repo.add(
        Proposal(
            id="PRO-900",
            question="Should we apply a 5% surcharge?",
            region="Europe",
            effective_quarter="2027-Q1",
            surcharge_options=[0.03, 0.05, 0.07],
            requested_by_role=Role.PRICING_DIRECTOR,
            **STAMPS,
        )
    )
    ref = repo.get(SourceDocument, source_id).excerpt_ref(excerpt)
    return repo.add(
        Recommendation(
            id="REC-900",
            proposal_id="PRO-900",
            decision_type=RecommendationType.PHASED_PILOT,
            recommended_rate=0.05,
            reasons=[RecommendationReason(text="Precedent.", source_refs=[ref])],
            approval_required_by=Role.PRICING_DIRECTOR,
            **STAMPS,
        )
    )


def approval(role: Role, verdict: ApprovalVerdict = ApprovalVerdict.APPROVED) -> Approval:
    return Approval(
        id="APR-900",
        recommendation_id="REC-900",
        approver_label=f"Demo {role.value}",
        role=role,
        verdict=verdict,
        chosen_rate=0.05,
        decided_at=LATER,
        **STAMPS,
    )


def events(repo: Repository) -> list[AuditEvent]:
    """Audit events written after the seed event."""
    return [event for event in repo.find(AuditEvent) if event.action is not AuditAction.SEED]


# --- capture and review -----------------------------------------------------


def test_capture_stores_a_draft_and_one_audit_event(seeded: Repository) -> None:
    draft = draft_citing(seeded, "SRC-006", "Only if contract customers stay out")
    stored = capture_decision(seeded, draft, now=LATER, **ANALYST)

    assert seeded.get(Decision, "DEC-900").status is DecisionStatus.DRAFT
    assert stored.created_at == LATER
    (event,) = events(seeded)
    assert event.id == "AUD-002"
    assert (event.action, event.entity_type, event.entity_id) == (
        AuditAction.CAPTURE, "decision", "DEC-900",
    )  # fmt: skip
    assert (event.actor_label, event.role, event.at) == ("Demo Analyst", Role.ANALYST, LATER)
    # A draft is not memory yet: no role can retrieve it.
    assert MemoryView(seeded, Role.RESTRICTED_EXEC).decision("DEC-900") is None


def test_confirming_in_review_is_audited_and_enters_memory(seeded: Repository) -> None:
    draft = capture_decision(
        seeded, draft_citing(seeded, "SRC-006", "Only if contract customers stay out"),
        now=LATER, **ANALYST,
    )  # fmt: skip
    confirmed = draft.model_copy(update={"status": DecisionStatus.CONFIRMED})
    review_decision(seeded, confirmed, now=LATER, **DIRECTOR)

    assert [event.action for event in events(seeded)] == [AuditAction.CAPTURE, AuditAction.CONFIRM]
    assert events(seeded)[-1].role is Role.PRICING_DIRECTOR
    assert MemoryView(seeded, Role.ANALYST).decision("DEC-900") is not None


def test_editing_a_confirmed_decision_is_audited_as_an_edit(seeded: Repository) -> None:
    decision = seeded.get(Decision, "DEC-004")
    review_decision(
        seeded, decision.model_copy(update={"rationale": "Clarified."}), now=LATER, **DIRECTOR
    )
    (event,) = events(seeded)
    assert (event.action, event.entity_id) == (AuditAction.EDIT, "DEC-004")
    assert seeded.get(Decision, "DEC-004").updated_at == LATER

    with pytest.raises(ValueError, match="cannot go back to draft"):
        review_decision(
            seeded, decision.model_copy(update={"status": DecisionStatus.DRAFT}), now=LATER, **DIRECTOR
        )
    assert len(events(seeded)) == 1


def test_capture_must_start_as_a_draft(seeded: Repository) -> None:
    confirmed = draft_citing(seeded, "SRC-006", "Only if contract customers stay out").model_copy(
        update={"status": DecisionStatus.CONFIRMED}
    )
    with pytest.raises(ValueError, match="starts as a draft"):
        capture_decision(seeded, confirmed, now=LATER, **ANALYST)
    assert events(seeded) == []


def test_a_role_cannot_capture_using_evidence_it_cannot_see(seeded: Repository) -> None:
    draft = draft_citing(seeded, "SRC-019", "Do not share outside the committee")
    with pytest.raises(PermissionDeniedError) as raised:
        capture_decision(seeded, draft, now=LATER, **DIRECTOR)
    assert "SRC-019" not in str(raised.value)
    assert seeded.get(Decision, "DEC-900") is None
    assert events(seeded) == []


def test_reviewing_a_hidden_decision_looks_like_reviewing_a_missing_one(seeded: Repository) -> None:
    hidden = seeded.get(Decision, "DEC-015")
    missing = hidden.model_copy(update={"id": "DEC-999"})
    messages = []
    for decision in (hidden, missing):
        with pytest.raises(PermissionDeniedError) as raised:
            review_decision(
                seeded, decision.model_copy(update={"rationale": "x"}), now=LATER, **DIRECTOR
            )
        messages.append(str(raised.value))
    assert messages[0] == messages[1]
    assert seeded.get(Decision, "DEC-015").rationale == hidden.rationale
    assert events(seeded) == []


def test_failed_write_leaves_no_audit_event(seeded: Repository) -> None:
    duplicate = draft_citing(seeded, "SRC-006", "Only if contract customers stay out", "DEC-004")
    with pytest.raises(DuplicateRecordError):
        capture_decision(seeded, duplicate, now=LATER, **ANALYST)
    assert events(seeded) == []


# --- approval ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("verdict", "action"),
    [(ApprovalVerdict.APPROVED, AuditAction.APPROVE), (ApprovalVerdict.REJECTED, AuditAction.REJECT)],
)
def test_approval_changes_are_audited(
    seeded: Repository, verdict: ApprovalVerdict, action: AuditAction
) -> None:
    store_recommendation(seeded, "SRC-007", "France delivered +$3.0M a quarter")
    record_approval(seeded, approval(Role.PRICING_DIRECTOR, verdict), now=LATER)
    assert seeded.get(Approval, "APR-900").verdict is verdict
    (event,) = events(seeded)
    assert (event.action, event.entity_type, event.entity_id) == (action, "recommendation", "REC-900")
    assert event.role is Role.PRICING_DIRECTOR


def test_analyst_cannot_approve(seeded: Repository) -> None:
    store_recommendation(seeded, "SRC-007", "France delivered +$3.0M a quarter")
    with pytest.raises(PermissionDeniedError):
        record_approval(seeded, approval(Role.ANALYST), now=LATER)
    assert seeded.count(Approval) == 0
    assert events(seeded) == []


def test_cannot_approve_a_recommendation_built_on_hidden_evidence(seeded: Repository) -> None:
    store_recommendation(seeded, "SRC-019", "Do not share outside the committee")
    with pytest.raises(PermissionDeniedError) as hidden:
        record_approval(seeded, approval(Role.PRICING_DIRECTOR), now=LATER)
    seeded_missing = approval(Role.PRICING_DIRECTOR).model_copy(update={"recommendation_id": "REC-404"})
    with pytest.raises(PermissionDeniedError) as missing:
        record_approval(seeded, seeded_missing, now=LATER)
    assert str(hidden.value) == str(missing.value)
    assert seeded.count(Approval) == 0

    record_approval(seeded, approval(Role.RESTRICTED_EXEC), now=LATER)
    assert seeded.count(Approval) == 1


# --- the log itself ---------------------------------------------------------


def test_audit_ids_increase_and_details_stay_neutral(seeded: Repository) -> None:
    capture_decision(
        seeded, draft_citing(seeded, "SRC-006", "Only if contract customers stay out"),
        now=LATER, **ANALYST,
    )  # fmt: skip
    log_event(
        seeded, now=LATER, actor_label="Demo Analyst", role=Role.ANALYST,
        action=AuditAction.EDIT, entity_type="decision", entity_id="DEC-900",
    )  # fmt: skip
    assert [event.id for event in seeded.find(AuditEvent)] == ["AUD-001", "AUD-002", "AUD-003"]
    log = "\n".join(event.model_dump_json() for event in events(seeded))
    for secret in (*HIDDEN_FROM_ANALYST, "Testland", "contract customers stay out"):
        assert secret not in log
