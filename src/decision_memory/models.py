"""Typed contracts for everything the app stores or passes between modules.

Conventions:
- Money is USD millions per quarter (`*_musd`). Rates and shares are fractions (0.05 = 5%).
- Every stored record has a stable prefixed ID, timezone-aware timestamps and a `synthetic` flag.
- A `SourceRef` is an exact excerpt of a source body, addressed by character offsets.
"""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

Fraction = Annotated[float, Field(ge=0.0, le=1.0)]
NonEmptyStr = Annotated[str, Field(min_length=1)]


# --- enumerations -----------------------------------------------------------


class Role(StrEnum):
    """Demo roles selected in the UI. Not authentication."""

    PRICING_DIRECTOR = "pricing_director"
    ANALYST = "analyst"
    RESTRICTED_EXEC = "restricted_exec"


class SourceType(StrEnum):
    TEAMS_TRANSCRIPT = "teams_transcript"
    STEERING_DECK = "steering_deck"
    JIRA_TICKET = "jira_ticket"
    EMAIL = "email"
    SERVICENOW = "servicenow"


class DecisionStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class EntityKind(StrEnum):
    MARKET = "market"
    SEGMENT = "segment"
    CUSTOMER_GROUP = "customer_group"
    SYSTEM = "system"
    RISK = "risk"
    INITIATIVE = "initiative"
    STAKEHOLDER = "stakeholder"


class RelationType(StrEnum):
    """Stored edge types.

    `SUPPORTED_BY` (decision to source) and `RESULTED_IN` (decision to outcome) are
    not stored as relations: the graph derives them from source refs and outcomes.
    """

    APPLIES_TO = "APPLIES_TO"
    AFFECTS = "AFFECTS"
    HAS_RISK = "HAS_RISK"
    OWNED_BY = "OWNED_BY"
    INVOLVES = "INVOLVES"
    PART_OF = "PART_OF"
    SIMILAR_TO = "SIMILAR_TO"
    CONTRADICTS = "CONTRADICTS"
    SUPERSEDES = "SUPERSEDES"
    INFORMED_BY = "INFORMED_BY"


class OutcomeVerdict(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class SlaStatus(StrEnum):
    MET = "met"
    AT_RISK = "at_risk"
    BREACHED = "breached"
    NOT_MODELED = "not_modeled"


class ProposalStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"


class RecommendationType(StrEnum):
    PHASED_PILOT = "phased_pilot"
    PROCEED = "proceed"
    DO_NOT_PROCEED = "do_not_proceed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class EvidenceBasis(StrEnum):
    """Whether a statement rests on cited sources or is a labelled demo assumption."""

    SOURCE_SUPPORTED = "source_supported"
    ASSUMED_FOR_DEMO = "assumed_for_demo"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalVerdict(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class MilestoneState(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    AT_RISK = "at_risk"


class AuditAction(StrEnum):
    SEED = "seed"
    CAPTURE = "capture"
    CONFIRM = "confirm"
    EDIT = "edit"
    APPROVE = "approve"
    REJECT = "reject"
    KPI_UPDATE = "kpi_update"
    CLOSE = "close"


# --- shared building blocks -------------------------------------------------


class Contract(BaseModel):
    """Base for all contracts: unknown fields are rejected, assignments re-validated."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class SourceRef(Contract):
    """An exact excerpt of a source document: `body[start_offset:end_offset] == excerpt`."""

    source_id: Annotated[str, Field(pattern=r"^SRC-[A-Z0-9][A-Z0-9-]*$")]
    excerpt: NonEmptyStr
    start_offset: Annotated[int, Field(ge=0)]
    end_offset: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def _offsets_match_excerpt(self) -> SourceRef:
        if self.end_offset - self.start_offset != len(self.excerpt):
            raise ValueError("end_offset - start_offset must equal the excerpt length")
        return self


class Record(Contract):
    """A stored record with a stable prefixed ID."""

    ID_PREFIX: ClassVar[str] = ""

    id: str
    synthetic: bool = True
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("id")
    @classmethod
    def _id_has_prefix(cls, value: str) -> str:
        if not re.fullmatch(rf"{cls.ID_PREFIX}-[A-Z0-9][A-Z0-9-]*", value):
            raise ValueError(f"id must look like {cls.ID_PREFIX}-001, got {value!r}")
        return value


# --- evidence and memory ----------------------------------------------------


class SourceDocument(Record):
    """A synthetic evidence artifact. `access_roles` lists the demo roles that may see it."""

    ID_PREFIX: ClassVar[str] = "SRC"

    source_type: SourceType
    title: NonEmptyStr
    document_date: date
    author_label: str = ""
    body: NonEmptyStr
    access_roles: Annotated[list[Role], Field(min_length=1)]
    mock_link: str = ""
    region: str | None = None

    @field_validator("access_roles")
    @classmethod
    def _canonical_roles(cls, value: list[Role]) -> list[Role]:
        return sorted(set(value))

    def excerpt_ref(self, excerpt: str) -> SourceRef:
        """Build a `SourceRef` for `excerpt`, which must occur exactly once in the body."""
        occurrences = self.body.count(excerpt) if excerpt else 0
        if occurrences != 1:
            raise ValueError(
                f"Excerpt must occur exactly once in {self.id}; found {occurrences}: {excerpt!r}"
            )
        start = self.body.index(excerpt)
        return SourceRef(
            source_id=self.id, excerpt=excerpt, start_offset=start, end_offset=start + len(excerpt)
        )


class Decision(Record):
    """A business decision. Only `confirmed` decisions are institutional memory."""

    ID_PREFIX: ClassVar[str] = "DEC"

    title: NonEmptyStr
    statement: NonEmptyStr
    decision_date: date
    owner: NonEmptyStr
    stakeholders: list[str] = Field(default_factory=list)
    region: NonEmptyStr
    market: NonEmptyStr
    customer_segment: NonEmptyStr
    surcharge_rate: Fraction | None = None
    rationale: str = ""
    alternatives: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    expected_outcome: str = ""
    status: DecisionStatus = DecisionStatus.DRAFT
    source_refs: list[SourceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _confirmed_needs_evidence(self) -> Decision:
        if self.status is DecisionStatus.CONFIRMED and not self.source_refs:
            raise ValueError("A confirmed decision must cite at least one source excerpt")
        return self


class Entity(Record):
    """A market, segment, customer group, system, risk, initiative or stakeholder."""

    ID_PREFIX: ClassVar[str] = "ENT"

    kind: EntityKind
    name: NonEmptyStr
    description: str = ""


class Relation(Record):
    """A typed edge between two stored records (decisions, entities, sources, outcomes)."""

    ID_PREFIX: ClassVar[str] = "REL"

    from_id: NonEmptyStr
    to_id: NonEmptyStr
    relation_type: RelationType
    note: str = ""

    @model_validator(mode="after")
    def _no_self_loop(self) -> Relation:
        if self.from_id == self.to_id:
            raise ValueError("A relation cannot link a record to itself")
        return self


class Outcome(Record):
    """What actually happened after a decision, next to what was expected."""

    ID_PREFIX: ClassVar[str] = "OUT"

    decision_id: NonEmptyStr
    verdict: OutcomeVerdict
    observed_on: date
    expected_revenue_change_musd: float | None = None
    actual_revenue_change_musd: float | None = None
    expected_churn_loss_fraction: Fraction | None = None
    actual_churn_loss_fraction: Fraction | None = None
    sla_status: SlaStatus = SlaStatus.NOT_MODELED
    explanation: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)


# --- proposal, analysis and advice ------------------------------------------


class Proposal(Record):
    """A question put to the system, e.g. the +5% European freight surcharge."""

    ID_PREFIX: ClassVar[str] = "PRO"

    question: NonEmptyStr
    region: NonEmptyStr
    target_segments: list[str] = Field(default_factory=list)
    effective_quarter: Annotated[str, Field(pattern=r"^\d{4}-Q[1-4]$")]
    surcharge_options: Annotated[list[Fraction], Field(min_length=1)]
    exclusions: list[str] = Field(default_factory=list)
    status: ProposalStatus = ProposalStatus.DRAFT
    requested_by_role: Role
    decision_id: str | None = None


class ScenarioAssumptions(Contract):
    """Editable synthetic inputs for one scenario."""

    baseline_revenue_musd: Annotated[float, Field(gt=0)]
    exempt_share: Fraction
    churn_loss_fraction: Fraction
    contribution_margin: Fraction
    implementation_cost_musd: Annotated[float, Field(ge=0)]


class ScenarioResult(Record):
    """Computed result for one surcharge option. Values come from formulas, never hard-coded."""

    ID_PREFIX: ClassVar[str] = "SCN"

    proposal_id: NonEmptyStr
    surcharge_rate: Fraction
    assumptions: ScenarioAssumptions
    gross_uplift_musd: float
    churn_loss_musd: float
    revenue_change_musd: float
    margin_change_musd: float
    formula_trace: str = ""


class RecommendationReason(Contract):
    """One reason behind a recommendation: cited, or openly labelled as an assumption."""

    text: NonEmptyStr
    basis: EvidenceBasis = EvidenceBasis.SOURCE_SUPPORTED
    source_refs: list[SourceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _supported_reasons_cite_sources(self) -> RecommendationReason:
        if self.basis is EvidenceBasis.SOURCE_SUPPORTED and not self.source_refs:
            raise ValueError(
                "A source-supported reason must cite at least one excerpt; "
                "otherwise label it assumed_for_demo"
            )
        return self


class ExcludedOption(Contract):
    surcharge_rate: Fraction
    reason: NonEmptyStr


class Recommendation(Record):
    """Advisory output. It never triggers an action; a named human role must approve."""

    ID_PREFIX: ClassVar[str] = "REC"

    proposal_id: NonEmptyStr
    decision_type: RecommendationType
    recommended_rate: Fraction | None = None
    reasons: list[RecommendationReason] = Field(default_factory=list)
    excluded_options: list[ExcludedOption] = Field(default_factory=list)
    risk_level: RiskLevel | None = None
    assumptions: list[str] = Field(default_factory=list)
    uncertainty: str = ""
    action_plan: list[str] = Field(default_factory=list)
    approval_required_by: Role


# --- governance -------------------------------------------------------------


class Approval(Record):
    """A simulated human approval or rejection of a recommendation."""

    ID_PREFIX: ClassVar[str] = "APR"

    recommendation_id: NonEmptyStr
    approver_label: NonEmptyStr
    role: Role
    verdict: ApprovalVerdict
    chosen_rate: Fraction | None = None
    reasons: str = ""
    decided_at: AwareDatetime


class Milestone(Record):
    ID_PREFIX: ClassVar[str] = "MIL"

    proposal_id: NonEmptyStr
    name: NonEmptyStr
    due_date: date
    state: MilestoneState = MilestoneState.PLANNED


class KPIUpdate(Record):
    """One weekly check of an approved pilot: planned next to demo-observed values."""

    ID_PREFIX: ClassVar[str] = "KPI"

    proposal_id: NonEmptyStr
    week: Annotated[int, Field(ge=1, le=8)]
    planned_revenue_uplift_musd: float
    actual_revenue_uplift_musd: float | None = None
    planned_churn_loss_fraction: Fraction
    actual_churn_loss_fraction: Fraction | None = None
    sla_status: SlaStatus = SlaStatus.NOT_MODELED
    risk_flag: bool = False
    note: str = ""


class AuditEvent(Record):
    """Who did what to which record. `role` is None for system actions such as seeding."""

    ID_PREFIX: ClassVar[str] = "AUD"

    at: AwareDatetime
    actor_label: NonEmptyStr
    role: Role | None = None
    action: AuditAction
    entity_type: NonEmptyStr
    entity_id: NonEmptyStr
    detail: str = ""
