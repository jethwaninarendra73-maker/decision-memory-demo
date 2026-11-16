"""Agent 3 — Impact Analysis.

Scopes a proposal from the precedents the active role can see: which teams, systems,
customer groups and risks it touches, plus an illustrative cost and effort range.

How an item is found (rules in `DEFAULT_RULES`, editable in this module):
- Each rule names the graph entities that indicate it and the terms that evidence it.
- For each precedent the rule looks for a line in the precedent's own sources (the
  sources its decision and its outcome cite) that contains one of the terms. A term
  matches any word that starts with it, ignoring case; a hyphen joins words, so
  "dispute" does not match "invoice-dispute". The first matching line per precedent is
  quoted with exact character offsets. Governance records that Agent 6 generates in the
  app are never quoted: they restate the app's own output.
- A rule with at least one quoted line is `source_supported`. A rule whose entity is
  linked to a precedent in the memory graph, but whose terms appear in none of that
  precedent's sources, is listed as `assumed_for_demo` and says so.
- Confidence counts the precedents with a quoted line: 3 or more high, 2 medium, 1 low.
  Assumptions are always low. Confidence never confirms or approves anything.
- Cost and effort are always `assumed_for_demo`: the sum of per-system ranges set below
  over the affected systems. They are illustrations, not estimates.

Everything is read through a `MemoryView`, so a precedent the role cannot see is
dropped exactly as if it did not exist. Source lines are quoted, never interpreted.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Annotated

from pydantic import Field, model_validator

from ..authz import MemoryView
from ..models import (
    ESTIMATE_CATEGORIES,
    Confidence,
    Contract,
    Decision,
    EvidenceBasis,
    Fraction,
    ImpactAssessment,
    ImpactCategory,
    ImpactEvidence,
    ImpactItem,
    NonEmptyStr,
    OutcomeVerdict,
    Proposal,
    Role,
    SourceDocument,
    SourceRef,
    SourceType,
)
from ..precedent_search import format_setting
from ..provenance import INSUFFICIENT_EVIDENCE_TEXT, InaccessibleEvidenceError, resolve_all
from .base import AgentInfo

INFO = AgentInfo(
    number=3,
    key="impact",
    name="Impact Analysis",
    purpose="Scopes a proposal: who and what it touches, with rough cost and effort.",
    reads="The proposal and accessible precedents",
    produces="Affected teams, systems and customer groups; each item source-backed or assumed",
    build_stage=7,
)

SURCHARGE_OPTIONS: tuple[float, ...] = (0.03, 0.05, 0.07)
DRAFT_PROPOSAL_ID = "PRO-DRAFT"
HVC_EXCLUSION = "High-volume contract customers"
CATEGORY_ORDER: tuple[ImpactCategory, ...] = tuple(ImpactCategory)
CATEGORY_LABELS: dict[ImpactCategory, str] = {
    ImpactCategory.TEAM: "Affected teams",
    ImpactCategory.SYSTEM: "Affected systems",
    ImpactCategory.CUSTOMER_GROUP: "Affected customer groups",
    ImpactCategory.RISK: "Risks",
    ImpactCategory.COST: "Cost range",
    ImpactCategory.EFFORT: "Effort",
}
WARNED_CATEGORIES = frozenset(
    {ImpactCategory.SYSTEM, ImpactCategory.CUSTOMER_GROUP, ImpactCategory.RISK}
)
TROUBLED = frozenset({OutcomeVerdict.FAILED, OutcomeVerdict.PARTIAL})
_MARKER = "[SYNTHETIC DEMO DATA"
# Document header lines ("Reporter: ...", "Title: ..."): good evidence of who took part,
# weaker evidence of what happened, so other categories try content lines first.
_HEADER = re.compile(
    r"^(?:Title|Deck|Meeting|Ticket|Type|Created|Date|Reporter|Attendees|Distribution|Status"
    r"|Slide \d+):"
)


# --- rules -------------------------------------------------------------------


class ImpactRule(Contract):
    """How one team, system, customer group or risk is recognised in precedents."""

    key: NonEmptyStr
    category: ImpactCategory
    label: NonEmptyStr
    entity_ids: list[str] = Field(default_factory=list)
    terms: Annotated[list[NonEmptyStr], Field(min_length=1)]
    markets: list[str] = Field(default_factory=list)  # empty: precedents in any market
    exclusion: str | None = None  # proposal exclusion that covers this group, if any

    @model_validator(mode="after")
    def _not_an_estimate(self) -> ImpactRule:
        if self.category in ESTIMATE_CATEGORIES:
            raise ValueError("Cost and effort come from system ranges, not from rules")
        if any(term != term.lower() for term in self.terms):
            raise ValueError("Rule terms are written in lower case")
        return self


class SystemEstimate(Contract):
    """Illustrative one-off cost and effort for changing one system. Assumed for demo."""

    cost_low_musd: Annotated[float, Field(ge=0)]
    cost_high_musd: Annotated[float, Field(ge=0)]
    effort_low_weeks: Annotated[float, Field(ge=0)]
    effort_high_weeks: Annotated[float, Field(ge=0)]

    @model_validator(mode="after")
    def _ranges_ordered(self) -> SystemEstimate:
        if (
            self.cost_low_musd > self.cost_high_musd
            or self.effort_low_weeks > self.effort_high_weeks
        ):
            raise ValueError("A range's low end must not exceed its high end")
        return self


class ImpactRules(Contract):
    """The full, visible rule set Agent 3 applies."""

    rules: Annotated[list[ImpactRule], Field(min_length=1)]
    system_estimates: dict[str, SystemEstimate]
    high_confidence_min: Annotated[int, Field(ge=1)] = 3
    medium_confidence_min: Annotated[int, Field(ge=1)] = 2

    @model_validator(mode="after")
    def _consistent(self) -> ImpactRules:
        keys = [rule.key for rule in self.rules]
        if len(keys) != len(set(keys)):
            raise ValueError("Rule keys must be unique")
        systems = {r.key for r in self.rules if r.category is ImpactCategory.SYSTEM}
        if unknown := set(self.system_estimates) - systems:
            raise ValueError(f"Estimates for unknown system rules: {sorted(unknown)}")
        if self.medium_confidence_min > self.high_confidence_min:
            raise ValueError("medium_confidence_min must not exceed high_confidence_min")
        return self

    def confidence(self, supporting_precedents: int) -> Confidence:
        if supporting_precedents >= self.high_confidence_min:
            return Confidence.HIGH
        if supporting_precedents >= self.medium_confidence_min:
            return Confidence.MEDIUM
        return Confidence.LOW


def _rule(
    key: str,
    category: ImpactCategory,
    label: str,
    entity_ids: list[str],
    terms: list[str],
    **extra: object,
) -> ImpactRule:
    return ImpactRule(
        key=key, category=category, label=label, entity_ids=entity_ids, terms=terms, **extra
    )


_T, _S, _G, _R = (
    ImpactCategory.TEAM,
    ImpactCategory.SYSTEM,
    ImpactCategory.CUSTOMER_GROUP,
    ImpactCategory.RISK,
)
_COUNTRY_MANAGERS = ["KERN", "FONTAINE", "NYBERG", "DUARTE", "MORETTI", "ZIELAK", "DOYLE"]

DEFAULT_RULES = ImpactRules(
    rules=[
        _rule(
            "team.pricing",
            _T,
            "European pricing",
            ["ENT-STK-LINDQVIST"],
            ["mara lindqvist", "pricing director"],
        ),
        _rule("team.finance", _T, "Finance", ["ENT-STK-RAO"], ["sanjay rao", "finance"]),
        _rule(
            "team.country",
            _T,
            "Country management",
            [f"ENT-STK-{name}" for name in _COUNTRY_MANAGERS],
            [
                "country manager",
                "country team",
                "tobias kern",
                "claire fontaine",
                "astrid nyberg",
                "elena duarte",
                "giulia moretti",
                "piotr zielak",
                "fergus doyle",
            ],
        ),
        _rule(
            "team.customer_success",
            _T,
            "Customer success",
            ["ENT-STK-WHITCOMBE"],
            ["hannah whitcombe", "customer success"],
        ),
        _rule(
            "team.billing_ops",
            _T,
            "Billing operations",
            ["ENT-STK-FERRANTE"],
            ["luca ferrante", "billing operations"],
        ),
        _rule(
            "system.billing",
            _S,
            "Billing and rate tables",
            ["ENT-SYS-BILL"],
            ["billing", "rate table", "invoice", "wrong rate"],
        ),
        _rule(
            "system.contracts",
            _S,
            "Contract management",
            ["ENT-SYS-CTR"],
            ["fixed-price", "contract renewal", "next renewal", "contract clause"],
        ),
        _rule(
            "system.reporting",
            _S,
            "Revenue reporting",
            ["ENT-SYS-RPT"],
            ["kpi", "reporting", "weekly revenue"],
        ),
        _rule(
            "system.service_desk",
            _S,
            "Customer service desk",
            ["ENT-SYS-CS"],
            ["complaint", "response-time", "invoice-dispute", "service desk"],
        ),
        _rule(
            "group.hvc",
            _G,
            "High-volume contract customers",
            ["ENT-SEG-HVC", "ENT-SEG-NONCONTRACT"],
            ["high-volume contract", "contract customer", "contract account"],
            exclusion=HVC_EXCLUSION,
        ),
        _rule(
            "group.price_sensitive",
            _G,
            "Price-sensitive SME customers",
            ["ENT-SEG-SME", "ENT-RSK-SME-CHURN"],
            ["price-sensitive", "sme customer"],
        ),
        _rule("group.mid_market", _G, "Mid-market shippers", ["ENT-SEG-MID"], ["mid-market"]),
        _rule(
            "risk.hvc_churn",
            _R,
            "Churn among high-volume contract customers",
            ["ENT-RSK-HVC-CHURN"],
            ["disputed", "moved volume", "contract customer", "contract account"],
        ),
        _rule(
            "risk.competition_de",
            _R,
            "Competitive response in Germany",
            ["ENT-RSK-COMP-DE"],
            ["competitive response", "competitor"],
            markets=["Germany"],
        ),
        _rule(
            "risk.sme_churn",
            _R,
            "Churn among price-sensitive SME customers",
            ["ENT-RSK-SME-CHURN"],
            ["price-sensitive"],
        ),
        _rule(
            "risk.billing",
            _R,
            "Billing and rate-table errors",
            ["ENT-RSK-BILLING"],
            ["billing error", "wrong rate", "rate table", "credit note"],
        ),
        _rule(
            "risk.contract_clauses",
            _R,
            "Conflict with fixed-price contract clauses",
            ["ENT-RSK-CONTRACT"],
            ["fixed-price", "mid-term"],
        ),
        _rule("risk.notice", _R, "Short customer notice period", ["ENT-RSK-NOTICE"], ["notice"]),
        _rule(
            "risk.margin", _R, "Margin dilution from holding price", ["ENT-RSK-MARGIN"], ["margin"]
        ),
        _rule(
            "risk.transfer",
            _R,
            "Approach copied from a different market",
            ["ENT-RSK-TRANSFER"],
            ["copied", "copying", "without a local pilot"],
        ),
        _rule(
            "risk.guardrail",
            _R,
            "Churn loss above the 1.2% guardrail",
            ["ENT-RSK-GUARDRAIL"],
            ["guardrail"],
        ),
    ],
    system_estimates={
        "system.billing": SystemEstimate(
            cost_low_musd=0.10, cost_high_musd=0.15, effort_low_weeks=3, effort_high_weeks=5
        ),
        "system.contracts": SystemEstimate(
            cost_low_musd=0.05, cost_high_musd=0.10, effort_low_weeks=2, effort_high_weeks=3
        ),
        "system.reporting": SystemEstimate(
            cost_low_musd=0.03, cost_high_musd=0.05, effort_low_weeks=1, effort_high_weeks=2
        ),
        "system.service_desk": SystemEstimate(
            cost_low_musd=0.02, cost_high_musd=0.05, effort_low_weeks=1, effort_high_weeks=2
        ),
    },
)


# --- proposal ------------------------------------------------------------------


def next_quarter(as_of: date) -> str:
    """The calendar quarter after `as_of`, e.g. 2026-11-13 -> `2027-Q1`."""
    quarter = (as_of.month - 1) // 3 + 1
    year, quarter = (as_of.year + 1, 1) if quarter == 4 else (as_of.year, quarter + 1)
    return f"{year}-Q{quarter}"


def surcharge_options(asked_rate: float | None = None) -> list[float]:
    """The options to compare: +3 / +5 / +7 %, plus the asked rate if it is another one."""
    return sorted(set(SURCHARGE_OPTIONS) | ({asked_rate} if asked_rate else set()))


def draft_proposal(
    question: str,
    region: str | None,
    as_of: date,
    now: datetime,
    *,
    surcharge_rate: Fraction | None = None,
    customer_segment: str | None = None,
    exclusions: Sequence[str] = (),
    requested_by: Role = Role.PRICING_DIRECTOR,
) -> Proposal:
    """An unsaved proposal for the question: next quarter, options +3/+5/+7 % (plus the
    asked rate if it is another one). Nothing is stored or sent."""
    options = surcharge_options(surcharge_rate)
    return Proposal(
        id=DRAFT_PROPOSAL_ID,
        question=question,
        region=region or "Any region",
        target_segments=[customer_segment] if customer_segment else [],
        effective_quarter=next_quarter(as_of),
        surcharge_options=options,
        exclusions=list(exclusions),
        requested_by_role=requested_by,
        created_at=now,
        updated_at=now,
    )


# --- evidence lines ------------------------------------------------------------


def source_lines(source: SourceDocument) -> list[SourceRef]:
    """Every content line of a source as an exact excerpt with offsets.

    Leading whitespace and a "- " bullet are left out of the excerpt; the marker line and
    bare headings ending in ":" are skipped.
    """
    refs: list[SourceRef] = []
    offset = 0
    for raw in source.body.splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        text = line.strip()
        if text.startswith("- "):
            text = text[2:].strip()
        # Locate the kept text in the line itself, so any amount of space before or
        # after the bullet still gives offsets that slice back to exactly `text`.
        start = offset + (line.index(text) if text else 0)
        offset += len(raw)
        if not text or text.startswith(_MARKER) or text.endswith(":"):
            continue
        refs.append(
            SourceRef(
                source_id=source.id, excerpt=text, start_offset=start, end_offset=start + len(text)
            )
        )
    return refs


def _matcher(terms: Iterable[str]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(term) for term in sorted(set(terms), key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9-])(?:{alternatives})")


class _Precedent:
    """A visible precedent with its graph links and its quotable source lines."""

    def __init__(self, view: MemoryView, decision: Decision) -> None:
        self.decision = decision
        outcomes = sorted(
            view.outcomes(decision_id=decision.id), key=lambda o: (o.observed_on, o.id)
        )
        self.outcome = outcomes[-1] if outcomes else None
        self.linked = {relation.to_id for relation in view.relations(from_id=decision.id)}
        outcome_ids = list(
            dict.fromkeys(
                ref.source_id for ref in (self.outcome.source_refs if self.outcome else [])
            )
        )
        decision_ids = [
            sid
            for sid in dict.fromkeys(ref.source_id for ref in decision.source_refs)
            if sid not in outcome_ids
        ]
        verdict = self.outcome.verdict if self.outcome else None
        cited_by_decision = {ref.source_id for ref in decision.source_refs}
        self.outcome_lines = self._lines(view, outcome_ids, verdict, cited_by_decision)
        self.decision_lines = self._lines(view, decision_ids, None, cited_by_decision)

    def _lines(
        self,
        view: MemoryView,
        source_ids: list[str],
        verdict: OutcomeVerdict | None,
        cited_by_decision: set[str],
    ) -> list[ImpactEvidence]:
        lines = []
        for source_id in source_ids:
            source = view.source(source_id)
            if source is None:  # cannot happen for an eligible decision; fail closed anyway
                continue
            if source.source_type is SourceType.GOVERNANCE_RECORD:
                # Written by Agent 6 from this app's own output (approval and close
                # records): quoting it would count the app's words as independent evidence.
                continue
            lines += [
                ImpactEvidence(
                    decision_id=self.decision.id,
                    outcome_verdict=verdict,
                    decision_cites=source_id in cited_by_decision,
                    ref=ref,
                )
                for ref in source_lines(source)
            ]
        return lines

    def first_match(
        self, pattern: re.Pattern[str], category: ImpactCategory
    ) -> ImpactEvidence | None:
        """The line to quote. Teams: in document order, decision sources first. Everything
        else: what happened (outcome sources) before what was planned, content lines
        before header lines."""
        if category is ImpactCategory.TEAM:
            ordered = self.decision_lines + self.outcome_lines
        else:
            lines = self.outcome_lines + self.decision_lines
            ordered = sorted(lines, key=lambda line: bool(_HEADER.match(line.ref.excerpt)))
        return next((line for line in ordered if pattern.search(line.ref.excerpt.lower())), None)

    @property
    def tag(self) -> str:
        verdict = self.outcome.verdict.value if self.outcome else "no outcome yet"
        return f"{self.decision.id} ({verdict})"


# --- assessment ----------------------------------------------------------------


def _rule_item(
    rule: ImpactRule, precedents: list[_Precedent], proposal: Proposal, rules: ImpactRules
) -> ImpactItem | None:
    pattern = _matcher(rule.terms)
    markets = {market.casefold() for market in rule.markets}
    evidence: list[ImpactEvidence] = []
    found_in: list[_Precedent] = []
    graph_only: list[_Precedent] = []
    for precedent in precedents:
        if markets and precedent.decision.market.casefold() not in markets:
            continue
        line = precedent.first_match(pattern, rule.category)
        if line is not None:
            evidence.append(line)
            found_in.append(precedent)
        elif precedent.linked & set(rule.entity_ids):
            graph_only.append(precedent)
    if not found_in and not graph_only:
        return None

    excluded = rule.exclusion is not None and rule.exclusion.casefold() in {
        e.casefold() for e in proposal.exclusions
    }
    if found_in:
        tags = ", ".join(p.tag for p in found_in)
        rationale = f"Quoted from the sources of {len(found_in)} precedent(s): {tags}."
        if graph_only:
            rationale += (
                " Also linked in the memory graph to "
                f"{', '.join(p.decision.id for p in graph_only)}, whose sources do not mention it."
            )
        troubled = [
            p for p, e in zip(found_in, evidence, strict=True) if e.outcome_verdict in TROUBLED
        ]
        warning = (
            "Seen in precedent(s) that did not go to plan: "
            f"{', '.join(p.tag for p in troubled)}. Read the quoted lines before relying on it."
            if troubled and rule.category in WARNED_CATEGORIES
            else None
        )
        return ImpactItem(
            key=rule.key,
            category=rule.category,
            label=rule.label,
            basis=EvidenceBasis.SOURCE_SUPPORTED,
            confidence=rules.confidence(len(found_in)),
            rationale=rationale,
            decision_ids=[p.decision.id for p in found_in + graph_only],
            evidence=evidence,
            warning=warning,
            exclusion=rule.exclusion,
            excluded_by_proposal=excluded,
        )
    return ImpactItem(
        key=rule.key,
        category=rule.category,
        label=rule.label,
        basis=EvidenceBasis.ASSUMED_FOR_DEMO,
        confidence=Confidence.LOW,
        rationale=(
            f"Linked in the memory graph to {', '.join(p.decision.id for p in graph_only)}, "
            "but no line in those precedents' sources mentions it. Treated as an assumption "
            "until a source confirms it."
        ),
        decision_ids=[p.decision.id for p in graph_only],
        exclusion=rule.exclusion,
        excluded_by_proposal=excluded,
    )


def format_musd(value: float) -> str:
    """`$0.20M`, `$0.125M`: three decimals at most, two at least, so a range is shown as
    it is applied (an estimate of 0.125 is not shown as 0.12 or 0.13)."""
    return f"${format_setting(value)}M"


def _weeks(value: float) -> str:
    return f"{value:g}"


def _estimates(systems: list[ImpactItem], rules: ImpactRules) -> list[ImpactItem]:
    """Cost and effort ranges summed over the affected systems that have an estimate."""
    priced = [
        (item, rules.system_estimates[item.key])
        for item in systems
        if item.key in rules.system_estimates
    ]
    if not priced:
        return []
    ids = list(dict.fromkeys(d for item, _ in priced for d in item.decision_ids))

    def total(low: str, high: str) -> tuple[float, float]:
        return (
            round(sum(getattr(e, low) for _, e in priced), 4),
            round(sum(getattr(e, high) for _, e in priced), 4),
        )

    cost = total("cost_low_musd", "cost_high_musd")
    effort = total("effort_low_weeks", "effort_high_weeks")
    cost_terms = " + ".join(
        f"{item.label} {format_musd(e.cost_low_musd)}–{format_musd(e.cost_high_musd)}"
        for item, e in priced
    )
    effort_terms = " + ".join(
        f"{item.label} {_weeks(e.effort_low_weeks)}–{_weeks(e.effort_high_weeks)}"
        for item, e in priced
    )
    note = (
        "The per-system ranges are illustrative assumptions for the demo, not derived from "
        "any source; edit them in `system_estimates`."
    )
    return [
        ImpactItem(
            key="cost.implementation",
            category=ImpactCategory.COST,
            label="One-off implementation cost",
            basis=EvidenceBasis.ASSUMED_FOR_DEMO,
            confidence=Confidence.LOW,
            rationale=f"Sum over the affected systems: {cost_terms}. {note}",
            decision_ids=ids,
            low=cost[0],
            high=cost[1],
            unit="USD M",
        ),
        ImpactItem(
            key="effort.implementation",
            category=ImpactCategory.EFFORT,
            label="Implementation effort",
            basis=EvidenceBasis.ASSUMED_FOR_DEMO,
            confidence=Confidence.LOW,
            rationale=f"Sum over the affected systems, in person-weeks: {effort_terms}. {note}",
            decision_ids=ids,
            low=effort[0],
            high=effort[1],
            unit="person-weeks",
        ),
    ]


def _order(item: ImpactItem, rule_index: dict[str, int]) -> tuple[int, int, int, int]:
    return (
        CATEGORY_ORDER.index(item.category),
        0 if item.basis is EvidenceBasis.SOURCE_SUPPORTED else 1,
        -len(item.evidence),
        rule_index.get(item.key, len(rule_index)),
    )


def assess_impact(
    view: MemoryView,
    proposal: Proposal,
    precedent_ids: Sequence[str],
    rules: ImpactRules = DEFAULT_RULES,
) -> ImpactAssessment:
    """Build the impact assessment for `proposal` from the given precedents.

    Precedent IDs the role cannot see are ignored exactly like unknown IDs. With no
    visible precedent, or nothing found in them, the assessment abstains.
    """
    precedents = [
        _Precedent(view, decision)
        for decision_id in dict.fromkeys(precedent_ids)
        if (decision := view.decision(decision_id)) is not None
    ]
    used = [p.decision.id for p in precedents]
    if not precedents:
        return ImpactAssessment(
            proposal_id=proposal.id,
            role=view.role,
            abstained=True,
            message=(
                f"{INSUFFICIENT_EVIDENCE_TEXT} There are no precedents to assess impact from. "
                "Ask a question that finds precedents first."
            ),
        )

    items = [
        item
        for rule in rules.rules
        if (item := _rule_item(rule, precedents, proposal, rules)) is not None
    ]
    if not items:
        return ImpactAssessment(
            proposal_id=proposal.id,
            role=view.role,
            precedent_ids=used,
            abstained=True,
            message=(
                f"{INSUFFICIENT_EVIDENCE_TEXT} None of the impact rules matches these "
                "precedents or their sources."
            ),
        )
    items += _estimates([i for i in items if i.category is ImpactCategory.SYSTEM], rules)
    rule_index = {rule.key: n for n, rule in enumerate(rules.rules)}
    items.sort(key=lambda item: _order(item, rule_index))

    supported = sum(item.basis is EvidenceBasis.SOURCE_SUPPORTED for item in items)
    return ImpactAssessment(
        proposal_id=proposal.id,
        role=view.role,
        precedent_ids=used,
        items=items,
        abstained=False,
        message=(
            f"{len(items)} impact item(s) from {len(used)} precedent(s): {supported} "
            f"source-supported, {len(items) - supported} assumed for demo."
        ),
    )


def verify_assessment(view: MemoryView, assessment: ImpactAssessment) -> None:
    """Refuse an assessment unless every quoted line resolves for `view.role`.

    Call before showing or storing an assessment. The error never names the source.
    """
    for item in assessment.items:
        if item.evidence and resolve_all(view, item.source_refs) is None:
            raise InaccessibleEvidenceError(
                "An impact item cites evidence that is not available to this role "
                "or cannot be verified."
            )
        if any(view.decision(decision_id) is None for decision_id in item.decision_ids):
            raise InaccessibleEvidenceError(
                "An impact item refers to a precedent that is not available to this role."
            )
