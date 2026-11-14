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

    `SUPPORTED_BY` (decision or outcome to source) and `RESULTED_IN` (decision to outcome) are
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


# --- decision capture (Agent 1) ---------------------------------------------


class DraftField(StrEnum):
    """Fields the capture agent tries to extract from a transcript."""

    STATEMENT = "statement"
    DECISION_DATE = "decision_date"
    OWNER = "owner"
    STAKEHOLDERS = "stakeholders"
    REGION = "region"
    MARKET = "market"
    CUSTOMER_SEGMENT = "customer_segment"
    SURCHARGE_RATE = "surcharge_rate"
    RATIONALE = "rationale"
    ALTERNATIVES = "alternatives"
    RISKS = "risks"
    EXPECTED_OUTCOME = "expected_outcome"


class FlagKind(StrEnum):
    MISSING = "missing"
    UNCERTAIN = "uncertain"


class FieldFlag(Contract):
    """Why a field needs the reviewer's attention. The agent flags; it never guesses."""

    field: DraftField
    kind: FlagKind
    reason: NonEmptyStr


class EvidenceSpan(Contract):
    """The exact transcript snippet a field was read from: `text[start:end] == excerpt`."""

    field: DraftField
    excerpt: NonEmptyStr
    start_offset: Annotated[int, Field(ge=0)]
    end_offset: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def _offsets_match_excerpt(self) -> EvidenceSpan:
        if self.end_offset - self.start_offset != len(self.excerpt):
            raise ValueError("end_offset - start_offset must equal the excerpt length")
        return self


class DecisionDraft(Contract):
    """What the capture agent proposes. A human must review and confirm it.

    Confidence is rule-based (how explicitly each field was stated), not a model
    prediction, and it is derived from `field_confidence`; it cannot be set directly.
    """

    SCORED_FIELDS: ClassVar[tuple[DraftField, ...]] = (
        DraftField.STATEMENT,
        DraftField.DECISION_DATE,
        DraftField.OWNER,
        DraftField.STAKEHOLDERS,
        DraftField.REGION,
        DraftField.MARKET,
        DraftField.CUSTOMER_SEGMENT,
        DraftField.RATIONALE,
        DraftField.RISKS,
        DraftField.EXPECTED_OUTCOME,
    )
    REQUIRED_TO_CONFIRM: ClassVar[tuple[DraftField, ...]] = (
        DraftField.STATEMENT,
        DraftField.DECISION_DATE,
        DraftField.OWNER,
        DraftField.REGION,
        DraftField.MARKET,
        DraftField.CUSTOMER_SEGMENT,
    )
    INCOMPLETE_CONFIDENCE_CAP: ClassVar[float] = 0.6

    title: str = ""
    statement: NonEmptyStr
    decision_date: date | None = None
    owner: str | None = None
    stakeholders: list[str] = Field(default_factory=list)
    region: str | None = None
    market: str | None = None
    customer_segment: str | None = None
    surcharge_rate: Fraction | None = None
    rationale: str = ""
    alternatives: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    expected_outcome: str = ""
    field_confidence: dict[DraftField, Fraction] = Field(default_factory=dict)
    flags: list[FieldFlag] = Field(default_factory=list)
    evidence: Annotated[list[EvidenceSpan], Field(min_length=1)]

    def has_value(self, field: DraftField) -> bool:
        value = getattr(self, field.value)
        return value is not None and value != "" and value != []

    @property
    def missing_fields(self) -> list[DraftField]:
        return [flag.field for flag in self.flags if flag.kind is FlagKind.MISSING]

    @property
    def uncertain_fields(self) -> list[DraftField]:
        return [flag.field for flag in self.flags if flag.kind is FlagKind.UNCERTAIN]

    @property
    def blocking_fields(self) -> list[DraftField]:
        """Fields a reviewer must fill in before the decision can be confirmed."""
        return [field for field in self.REQUIRED_TO_CONFIRM if not self.has_value(field)]

    @property
    def confidence(self) -> float:
        """Mean field confidence; never above the statement's own, capped while incomplete."""
        scores = [self.field_confidence.get(field, 0.0) for field in self.SCORED_FIELDS]
        overall = min(sum(scores) / len(scores), self.field_confidence[DraftField.STATEMENT])
        if self.blocking_fields:
            overall = min(overall, self.INCOMPLETE_CONFIDENCE_CAP)
        return round(overall, 2)

    @property
    def confidence_label(self) -> str:
        if self.confidence >= 0.8:
            return "high"
        return "medium" if self.confidence >= 0.5 else "low"

    @model_validator(mode="after")
    def _confidence_and_flags_are_consistent(self) -> DecisionDraft:
        missing = set(self.missing_fields)
        for field in DraftField:
            present = self.has_value(field)
            confidence = self.field_confidence.get(field, 0.0)
            if not present and confidence > 0:
                raise ValueError(f"{field.value} has no value, so its confidence must be 0")
            if present and field in missing:
                raise ValueError(f"{field.value} has a value but is flagged missing")
            if field in self.SCORED_FIELDS:
                if present and confidence <= 0:
                    raise ValueError(f"{field.value} has a value but no confidence")
                if not present and field not in missing:
                    raise ValueError(f"{field.value} has no value and must be flagged missing")
        if not any(span.field is DraftField.STATEMENT for span in self.evidence):
            raise ValueError("The statement needs an evidence snippet")
        return self


class ReviewedDecision(Contract):
    """The fields a human reviewer signs off. Required ones cannot be blank."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=True)

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


class NonDecision(Contract):
    """Transcript text that looks decision-like but was deliberately not captured."""

    excerpt: NonEmptyStr
    start_offset: Annotated[int, Field(ge=0)]
    end_offset: Annotated[int, Field(gt=0)]
    reason: NonEmptyStr


class CaptureResult(Contract):
    """Everything the capture agent read out of one transcript."""

    meeting_title: str | None = None
    has_synthetic_marker: bool
    drafts: list[DecisionDraft] = Field(default_factory=list)
    not_decisions: list[NonDecision] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# --- decision graph (Agent 2) -----------------------------------------------


class NodeKind(StrEnum):
    """What a graph node stands for. Entity nodes reuse the `EntityKind` values."""

    DECISION = "decision"
    OUTCOME = "outcome"
    SOURCE = "source"
    MARKET = "market"
    SEGMENT = "segment"
    CUSTOMER_GROUP = "customer_group"
    SYSTEM = "system"
    RISK = "risk"
    INITIATIVE = "initiative"
    STAKEHOLDER = "stakeholder"


class EdgeType(StrEnum):
    """Graph edge types: every stored `RelationType`, plus two derived ones.

    `SUPPORTED_BY` links a decision or outcome to each source it cites, and
    `RESULTED_IN` links a decision to its outcome. Neither is stored as a relation.
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
    SUPPORTED_BY = "SUPPORTED_BY"
    RESULTED_IN = "RESULTED_IN"


DERIVED_EDGE_TYPES: frozenset[EdgeType] = frozenset({EdgeType.SUPPORTED_BY, EdgeType.RESULTED_IN})


class GraphNode(Contract):
    """One node of the decision graph. `label` and `detail` are short display strings."""

    id: NonEmptyStr
    kind: NodeKind
    label: NonEmptyStr
    detail: str = ""
    node_date: date | None = None
    region: str | None = None
    verdict: OutcomeVerdict | None = None


class GraphEdge(Contract):
    """A typed, directed edge. Stored edges carry their relation ID; derived ones do not."""

    from_id: NonEmptyStr
    to_id: NonEmptyStr
    edge_type: EdgeType
    relation_id: str | None = None
    note: str = ""

    @property
    def derived(self) -> bool:
        return self.edge_type in DERIVED_EDGE_TYPES

    @model_validator(mode="after")
    def _stored_edges_name_their_relation(self) -> GraphEdge:
        if self.derived and self.relation_id is not None:
            raise ValueError(f"{self.edge_type} edges are derived and have no relation ID")
        if not self.derived and self.relation_id is None:
            raise ValueError(f"{self.edge_type} edges come from a stored relation; name it")
        if self.from_id == self.to_id:
            raise ValueError("An edge cannot link a node to itself")
        return self


class DecisionGraph(Contract):
    """A permission-filtered projection of memory for one role. Never stored.

    Node IDs are unique and every edge joins two nodes in the graph: a graph with a
    dangling edge cannot be constructed.
    """

    role: Role
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicates_or_dangling_edges(self) -> DecisionGraph:
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("Graph node IDs must be unique")
        known = set(ids)
        for edge in self.edges:
            for end in (edge.from_id, edge.to_id):
                if end not in known:
                    raise ValueError(f"Dangling {edge.edge_type} edge: {end} is not a graph node")
        keys = [(edge.from_id, edge.to_id, edge.edge_type) for edge in self.edges]
        if len(keys) != len(set(keys)):
            raise ValueError("An edge type may join the same two nodes only once")
        return self

    def node(self, node_id: str) -> GraphNode | None:
        return next((node for node in self.nodes if node.id == node_id), None)

    def node_ids(self, kind: NodeKind | None = None) -> list[str]:
        return [node.id for node in self.nodes if kind is None or node.kind is kind]

    def edges_of(self, node_id: str) -> list[GraphEdge]:
        """Edges that start or end at the node."""
        return [edge for edge in self.edges if node_id in (edge.from_id, edge.to_id)]


# --- impact analysis (Agent 3) ----------------------------------------------


class ImpactCategory(StrEnum):
    TEAM = "team"
    SYSTEM = "system"
    CUSTOMER_GROUP = "customer_group"
    RISK = "risk"
    COST = "cost"
    EFFORT = "effort"


ESTIMATE_CATEGORIES: frozenset[ImpactCategory] = frozenset(
    {ImpactCategory.COST, ImpactCategory.EFFORT}
)


class Confidence(StrEnum):
    """Rule-based: how many precedents back an item. It never confirms or approves anything."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ImpactEvidence(Contract):
    """One source line behind an impact item, and the precedent it was found through.

    `outcome_verdict` is set when the line is in a source the precedent's outcome cites;
    `decision_cites` says whether the decision cites that source too. A source cited by
    both can hold planning lines as well as results, so the two are shown together.
    """

    decision_id: Annotated[str, Field(pattern=r"^DEC-[A-Z0-9][A-Z0-9-]*$")]
    outcome_verdict: OutcomeVerdict | None = None
    decision_cites: bool = True
    ref: SourceRef

    @model_validator(mode="after")
    def _cited_by_someone(self) -> ImpactEvidence:
        if self.outcome_verdict is None and not self.decision_cites:
            raise ValueError("Evidence must come from a source the decision or its outcome cites")
        return self

    @property
    def cited_by(self) -> str:
        """Which record cites the quoted source, e.g. `decision and outcome (partial)`."""
        if self.outcome_verdict is None:
            return "decision"
        who = "decision and outcome" if self.decision_cites else "outcome"
        return f"{who} ({self.outcome_verdict.value})"


class ImpactItem(Contract):
    """One team, system, customer group, risk, cost or effort the proposal touches.

    A `source_supported` item quotes at least one source line. Anything else is labelled
    `assumed_for_demo` and its rationale says why. Cost and effort are always assumptions,
    given as a low-high range.
    """

    key: NonEmptyStr
    category: ImpactCategory
    label: NonEmptyStr
    basis: EvidenceBasis
    confidence: Confidence
    rationale: NonEmptyStr
    decision_ids: list[str] = Field(default_factory=list)
    evidence: list[ImpactEvidence] = Field(default_factory=list)
    warning: str | None = None
    exclusion: str | None = None  # the proposal exclusion that would cover this group
    excluded_by_proposal: bool = False
    low: Annotated[float, Field(ge=0)] | None = None
    high: Annotated[float, Field(ge=0)] | None = None
    unit: str = ""

    @model_validator(mode="after")
    def _labelled_and_consistent(self) -> ImpactItem:
        if self.basis is EvidenceBasis.SOURCE_SUPPORTED and not self.evidence:
            raise ValueError(
                "A source-supported impact item must quote at least one source line; "
                "otherwise label it assumed_for_demo"
            )
        if self.basis is EvidenceBasis.ASSUMED_FOR_DEMO and self.evidence:
            raise ValueError("An item with quoted evidence is source_supported, not assumed")
        if self.category in ESTIMATE_CATEGORIES:
            if self.basis is not EvidenceBasis.ASSUMED_FOR_DEMO:
                raise ValueError("Cost and effort ranges are illustrative assumptions")
            if self.low is None or self.high is None or not self.unit:
                raise ValueError("Cost and effort need a low-high range and a unit")
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError("low must not exceed high")
        if self.excluded_by_proposal and self.exclusion is None:
            raise ValueError("Only an item with an exclusion can be excluded by the proposal")
        return self

    @property
    def source_refs(self) -> list[SourceRef]:
        return [evidence.ref for evidence in self.evidence]


class ImpactAssessment(Contract):
    """Agent 3's output for one proposal and one demo role. Advisory; nothing is executed."""

    proposal_id: NonEmptyStr
    role: Role
    precedent_ids: list[str] = Field(default_factory=list)
    items: list[ImpactItem] = Field(default_factory=list)
    abstained: bool
    message: NonEmptyStr

    @model_validator(mode="after")
    def _abstain_means_no_items(self) -> ImpactAssessment:
        if self.abstained and self.items:
            raise ValueError("An abstaining assessment lists no impact items")
        if not self.abstained and not self.items:
            raise ValueError("An assessment with no items must abstain")
        keys = [item.key for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("Impact item keys must be unique")
        return self

    def by_category(self, category: ImpactCategory) -> list[ImpactItem]:
        return [item for item in self.items if item.category is category]

    def item(self, key: str) -> ImpactItem | None:
        return next((item for item in self.items if item.key == key), None)
