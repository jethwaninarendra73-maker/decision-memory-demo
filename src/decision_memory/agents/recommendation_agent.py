"""Agent 5 — Recommendation.

Turns the precedents, the impact assessment and the scenario bundle into one advisory
recommendation. It recommends and explains; it never executes, sends or stores anything.
A named human must approve (Agent 6).

Rules (`DEFAULT_RULES`, editable in this module). Each option must pass all three:
1. Guardrail: its churn loss is within the churn guardrail in the scenario inputs.
2. Evidence: at least one visible precedent within `rate_tolerance` of its rate has an
   outcome in `supporting_verdicts` (success only, by default). Partial and failed
   precedents near a rate never back it; they are shown as cautions.
3. Upside: its revenue change is positive.
The eligible option with the highest revenue change is recommended (the lower rate on a
tie). Above `pilot_above_rate` it is a phased pilot, otherwise it may proceed. In every
case the recommendation requires `required_exclusion` to be left out of the surcharge.

Abstaining: with no visible precedent, or no option backed by one, the answer is
"Insufficient evidence to recommend." If options are backed but every one breaks the
guardrail or has no upside, the answer is "do not proceed".

Reasons and assumptions are kept apart. Every reason cites source excerpts the active
role can see: precedents, cautions, the guardrail, the two policies and the key risks.
Anything else (computed figures, assumed inputs, demo settings such as the pilot scope)
is listed as an assumption. Risk level is rule-based: high if a precedent near the chosen
rate failed; medium if one was partial, a key risk exists, or the impact analysis is
missing; low otherwise.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Annotated

from pydantic import Field

from ..authz import ROLE_LABELS, MemoryView
from ..models import (
    AssumptionNote,
    Confidence,
    Contract,
    EvidenceBasis,
    ExcludedOption,
    Fraction,
    ImpactAssessment,
    ImpactCategory,
    ImpactItem,
    NonEmptyStr,
    OptionCheck,
    OutcomeVerdict,
    Proposal,
    Recommendation,
    RecommendationReason,
    RecommendationResult,
    RecommendationType,
    RiskLevel,
    Role,
    ScenarioResult,
    ScenarioSet,
    SourceRef,
)
from ..provenance import resolve_all, verify_recommendation
from .base import AgentInfo

INFO = AgentInfo(
    number=5,
    key="recommendation",
    name="Recommendation",
    purpose="Combines precedents, impact and scenarios into cited, advisory guidance.",
    reads="Accessible precedents, the impact assessment, scenario results and guardrails",
    produces="Recommended option, cited reasons, excluded options, risks and an action plan",
    build_stage=9,
)

DRAFT_RECOMMENDATION_ID = "REC-DRAFT"
INSUFFICIENT_TO_RECOMMEND = "Insufficient evidence to recommend."
ADVISORY_NOTE = (
    "Advisory only. Nothing has been priced, sent or changed. A named human approver must "
    "decide, and this app records that decision without executing it."
)
_CONFIDENCE_RANK = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
_EPS = 1e-12


# --- rules -------------------------------------------------------------------------


class PolicyCite(Contract):
    """An excerpt that states a policy. It must occur exactly once in the source."""

    source_id: NonEmptyStr
    excerpt: NonEmptyStr


class RecommendationRules(Contract):
    """The visible rules Agent 5 applies. Tests pin the defaults to the cited policies."""

    rate_tolerance: Annotated[float, Field(ge=0, le=0.05)] = 0.005
    supporting_verdicts: Annotated[tuple[OutcomeVerdict, ...], Field(min_length=1)] = (
        OutcomeVerdict.SUCCESS,
    )
    pilot_above_rate: Fraction = 0.03
    pilot_markets: Annotated[int, Field(ge=1)] = 2
    review_after_days: Annotated[int, Field(ge=1)] = 60
    key_risk_min_confidence: Confidence = Confidence.MEDIUM
    max_precedent_reasons: Annotated[int, Field(ge=1)] = 3
    approver: Role = Role.PRICING_DIRECTOR
    required_exclusion: NonEmptyStr = "High-volume contract customers"
    policy_region: NonEmptyStr = "Europe"
    pilot_policy: PolicyCite = PolicyCite(
        source_id="SRC-014",
        excerpt=(
            "Every surcharge above 3% starts as a phased pilot with weekly KPI checks and a "
            "60-day executive review."
        ),
    )
    exclusion_policy: PolicyCite = PolicyCite(
        source_id="SRC-022",
        excerpt=(
            "Exclude high-volume contract customers from any mid-term freight surcharge in "
            "Europe; apply changes only at contract renewal."
        ),
    )


DEFAULT_RULES = RecommendationRules()


# --- formatting ----------------------------------------------------------------------


def _pct(fraction: float) -> str:
    return f"{fraction * 100:g}%"


def _option(rate: float) -> str:
    return f"+{_pct(rate)}"


def _musd(value: float) -> str:
    sign = "+" if value >= 0 else "−"
    return f"{sign}${abs(value):,.2f}M"


def _backing_kind(rules: RecommendationRules) -> str:
    """`successful` under the default rule, `backing` if other outcomes may back an option."""
    only_success = set(rules.supporting_verdicts) == {OutcomeVerdict.SUCCESS}
    return "successful" if only_success else "backing"


def _tolerance(rules: RecommendationRules) -> str:
    return f"±{rules.rate_tolerance * 100:g} points"


def rules_summary(rules: RecommendationRules = DEFAULT_RULES) -> str:
    """The option rules in words, built from the settings actually in force."""
    backs = " or ".join(v.value for v in rules.supporting_verdicts)
    others = [v.value for v in OutcomeVerdict if v not in rules.supporting_verdicts]
    text = (
        "An option must stay within the churn guardrail, be backed by a precedent within "
        f"{_tolerance(rules)} of its rate whose outcome was a {backs}, and have a positive "
        "revenue change. The eligible option with the highest revenue change is recommended "
        f"(the lower rate on a tie); above {_pct(rules.pilot_above_rate)} it starts as a "
        "phased pilot. Only the precedents found for this question are considered."
    )
    if others:
        text += (
            f" Precedents near a rate with a {' or '.join(others)} outcome are cautions; "
            "they never back it."
        )
    return text


# --- precedents ------------------------------------------------------------------------


class PrecedentFact(Contract):
    """What Agent 5 needs to know about one visible precedent."""

    decision_id: NonEmptyStr
    market: NonEmptyStr
    surcharge_rate: Fraction | None = None
    verdict: OutcomeVerdict | None = None  # None: no outcome observed yet
    explanation: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)

    def near(self, rate: float, tolerance: float) -> bool:
        return (
            self.surcharge_rate is not None and abs(self.surcharge_rate - rate) <= tolerance + _EPS
        )


def _unique(refs: Iterable[SourceRef]) -> list[SourceRef]:
    return list({(r.source_id, r.start_offset, r.end_offset): r for r in refs}.values())


def precedent_facts(view: MemoryView, precedent_ids: Sequence[str]) -> list[PrecedentFact]:
    """Facts for the precedents the role can see, in the order given.

    A hidden ID is dropped exactly like an unknown one. The latest visible outcome counts.
    """
    facts = []
    for decision_id in dict.fromkeys(precedent_ids):
        decision = view.decision(decision_id)
        if decision is None:
            continue
        outcomes = sorted(
            view.outcomes(decision_id=decision_id), key=lambda o: (o.observed_on, o.id)
        )
        outcome = outcomes[-1] if outcomes else None
        facts.append(
            PrecedentFact(
                decision_id=decision.id,
                market=decision.market,
                surcharge_rate=decision.surcharge_rate,
                verdict=outcome.verdict if outcome else None,
                explanation=outcome.explanation if outcome else "",
                source_refs=_unique(
                    [*decision.source_refs, *(outcome.source_refs if outcome else [])]
                ),
            )
        )
    return facts


# --- judging the options -----------------------------------------------------------------


def judge_options(
    results: Sequence[ScenarioResult],
    guardrail: float,
    precedents: Sequence[PrecedentFact],
    rules: RecommendationRules = DEFAULT_RULES,
) -> list[OptionCheck]:
    """Apply the three rules to every option and mark the one to recommend. Pure."""
    backing, points = _backing_kind(rules), _tolerance(rules)
    rows = []
    for result in sorted(results, key=lambda r: r.surcharge_rate):
        rate, churn = result.surcharge_rate, result.assumptions.churn_loss_fraction
        near = [p for p in precedents if p.near(rate, rules.rate_tolerance)]
        support = [p.decision_id for p in near if p.verdict in rules.supporting_verdicts]
        cautions = [
            p.decision_id
            for p in near
            if p.verdict is not None and p.verdict not in rules.supporting_verdicts
        ]
        within = churn <= guardrail + _EPS
        upside = result.revenue_change_musd > 0
        problems = []
        if not within:
            problems.append(f"churn loss {_pct(churn)} exceeds the {_pct(guardrail)} guardrail")
        if not support:
            problems.append(
                f"no {backing} precedent within {points} of this rate among those found for "
                "this question"
            )
        if not upside:
            problems.append(f"no revenue upside ({_musd(result.revenue_change_musd)})")
        rows.append((result, within, upside, support, cautions, problems))

    eligible = [row[0] for row in rows if not row[5]]
    best = max(eligible, key=lambda r: (r.revenue_change_musd, -r.surcharge_rate), default=None)
    checks = []
    for result, within, upside, support, cautions, problems in rows:
        if problems:
            verdict = "Excluded: " + "; ".join(problems) + "."
        elif result is best:
            verdict = "Recommended: highest revenue change among the eligible options."
        else:
            verdict = (
                f"Not chosen: lower uplift ({_musd(result.revenue_change_musd)} against "
                f"{_musd(best.revenue_change_musd)} at {_option(best.surcharge_rate)})."
            )
        checks.append(
            OptionCheck(
                surcharge_rate=result.surcharge_rate,
                revenue_change_musd=result.revenue_change_musd,
                margin_change_musd=result.margin_change_musd,
                churn_loss_fraction=result.assumptions.churn_loss_fraction,
                within_guardrail=within,
                has_upside=upside,
                supporting_decision_ids=support,
                caution_decision_ids=cautions,
                eligible=not problems,
                chosen=result is best,
                verdict=verdict,
            )
        )
    return checks


def key_risks(
    impact: ImpactAssessment | None, rules: RecommendationRules = DEFAULT_RULES
) -> list[ImpactItem]:
    """Impact risks quoted from a failed precedent's outcome, at or above the confidence bar."""
    if impact is None:
        return []
    bar = _CONFIDENCE_RANK[rules.key_risk_min_confidence]
    return [
        item
        for item in impact.by_category(ImpactCategory.RISK)
        if _CONFIDENCE_RANK[item.confidence] >= bar
        and any(e.outcome_verdict is OutcomeVerdict.FAILED for e in item.evidence)
    ]


def risk_level(
    chosen: OptionCheck,
    precedents: Sequence[PrecedentFact],
    impact: ImpactAssessment | None,
    rules: RecommendationRules = DEFAULT_RULES,
) -> RiskLevel:
    """High if a precedent near the chosen rate failed. Medium if one was partial, a key
    risk exists, or there is no impact analysis to check. Low otherwise."""
    verdicts = {p.decision_id: p.verdict for p in precedents}
    if any(verdicts[d] is OutcomeVerdict.FAILED for d in chosen.caution_decision_ids):
        return RiskLevel.HIGH
    if chosen.caution_decision_ids or impact is None or key_risks(impact, rules):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


# --- assembling the recommendation ---------------------------------------------------------


class _Case:
    """Collects cited reasons and labelled assumptions for one recommendation."""

    def __init__(self, view: MemoryView) -> None:
        self.view = view
        self.reasons: list[RecommendationReason] = []
        self.assumptions: list[str] = []

    def cite(self, text: str, refs: Iterable[SourceRef], *, otherwise: str | None = None) -> bool:
        """Add a reason if every excerpt resolves for the role; else note `otherwise`
        as an assumption. Nothing uncited ever becomes a reason."""
        refs = _unique(refs)
        if refs and resolve_all(self.view, refs) is not None:
            self.reasons.append(
                RecommendationReason(
                    text=text, basis=EvidenceBasis.SOURCE_SUPPORTED, source_refs=refs
                )
            )
            return True
        if otherwise:
            self.assumptions.append(otherwise)
        return False

    def policy(self, text: str, cite: PolicyCite, *, otherwise: str) -> bool:
        source = self.view.source(cite.source_id)
        refs = (
            [source.excerpt_ref(cite.excerpt)]
            if source is not None and source.body.count(cite.excerpt) == 1
            else []
        )
        return self.cite(text, refs, otherwise=otherwise)


def _figure_lines(checks: Sequence[OptionCheck]) -> list[str]:
    return [
        f"{_option(c.surcharge_rate)}: revenue {_musd(c.revenue_change_musd)}, margin "
        f"{_musd(c.margin_change_musd)}, churn loss {_pct(c.churn_loss_fraction)} of baseline "
        "(computed in tab 4 from editable synthetic inputs)."
        for c in checks
    ]


def _assumed_inputs(notes: Sequence[AssumptionNote]) -> list[str]:
    return [
        f"{n.label}: {n.display} (assumed for demo{', edited' if n.edited else ''})."
        for n in notes
        if n.basis is EvidenceBasis.ASSUMED_FOR_DEMO
    ]


def _guardrail_reason(case: _Case, scenarios: ScenarioSet, checks: Sequence[OptionCheck]) -> None:
    """Cited only while the guardrail is the sourced default; an edited or unsourced
    guardrail is already listed with the assumed inputs."""
    note = scenarios.note("churn_guardrail")
    if note is None or note.basis is not EvidenceBasis.SOURCE_SUPPORTED:
        return
    text = (
        f"Guardrail: churn loss must stay within {_pct(scenarios.inputs.churn_guardrail)} of "
        "baseline revenue."
    )
    breaches = [
        f"{_option(c.surcharge_rate)} ({_pct(c.churn_loss_fraction)})"
        for c in checks
        if not c.within_guardrail
    ]
    if breaches:
        text += f" Exceeded by {', '.join(breaches)}."
    case.cite(text, note.source_refs)


def _precedent_reasons(
    case: _Case,
    chosen: OptionCheck,
    precedents: Sequence[PrecedentFact],
    rules: RecommendationRules,
) -> int:
    """Backing precedents, then cautions, for the chosen rate. Returns how many backing
    precedents were cited."""
    by_id = {p.decision_id: p for p in precedents}
    cited = 0
    for decision_id in chosen.supporting_decision_ids[: rules.max_precedent_reasons]:
        p = by_id[decision_id]
        label = (
            "Successful precedent"
            if p.verdict is OutcomeVerdict.SUCCESS
            else f"Backing precedent ({p.verdict.value})"
        )
        text = f"{label} at {_option(p.surcharge_rate)}: {p.decision_id}, {p.market}."
        cited += case.cite(f"{text} {p.explanation}".strip(), p.source_refs)
    for decision_id in chosen.caution_decision_ids[: rules.max_precedent_reasons]:
        p = by_id[decision_id]
        text = (
            f"Caution at {_option(p.surcharge_rate)}: {p.decision_id}, {p.market} "
            f"({p.verdict.value}). It does not back this option."
        )
        case.cite(f"{text} {p.explanation}".strip(), p.source_refs)
    return cited


def _action_plan(
    chosen: OptionCheck,
    proposal: Proposal,
    scenarios: ScenarioSet,
    impact: ImpactAssessment | None,
    rules: RecommendationRules,
    *,
    pilot: bool,
    excluded: bool,
) -> list[str]:
    rate = _option(chosen.surcharge_rate)
    status = (
        "the draft proposal already does"
        if excluded
        else "the draft proposal does not yet: add the exclusion in tab 3"
    )
    steps = [
        (
            f"Exclude {rules.required_exclusion.lower()}; apply any change to them only at "
            f"contract renewal ({status})."
        ),
        (
            f"Run {rate} as a phased pilot in {rules.pilot_markets} markets, chosen by the "
            "approver, before any wider rollout."
            if pilot
            else f"Apply {rate} from {proposal.effective_quarter} "
            f"(proposal scope: {proposal.region})."
        ),
        (
            f"Check weekly that churn loss in the pilot markets stays at the planned "
            f"{_pct(chosen.churn_loss_fraction)} of their baseline revenue, and track their "
            "revenue uplift against a plan scaled to those markets."
            if pilot
            else f"Check revenue and churn weekly against the scenario plan "
            f"({_musd(chosen.revenue_change_musd)} a quarter, churn loss "
            f"{_pct(chosen.churn_loss_fraction)} of baseline)."
        ),
        (
            f"Pause and review if churn loss exceeds {_pct(scenarios.inputs.churn_guardrail)} "
            "of baseline revenue."
        ),
    ]
    if pilot:
        steps.append(f"Hold an executive review after {rules.review_after_days} days.")
    if impact is not None:
        key = [i.label for i in key_risks(impact, rules)]
        watch = [
            i.label
            for i in impact.by_category(ImpactCategory.RISK)
            if i.warning and i.label not in key
        ]
        if key:
            steps.append(f"Manage the key risks: {', '.join(key)}.")
        if watch:
            steps.append(f"Also watch (flagged by the impact analysis): {', '.join(watch)}.")
    steps.append(
        f"Human approval required: a named {ROLE_LABELS[rules.approver]} approves or rejects. "
        "This app executes nothing."
    )
    return steps


def recommend(
    view: MemoryView,
    proposal: Proposal,
    precedent_ids: Sequence[str],
    impact: ImpactAssessment | None,
    scenarios: ScenarioSet,
    now: datetime,
    rules: RecommendationRules = DEFAULT_RULES,
) -> RecommendationResult:
    """The advisory recommendation for `proposal`, verified for `view.role`.

    `impact` may be None (no assessment available); key risks are then not assessed and
    the recommendation says so. Raises ValueError if `scenarios` was computed for another
    proposal or for other options.
    """
    if (
        any(r.proposal_id != proposal.id for r in scenarios.results)
        or sorted(proposal.surcharge_options) != scenarios.inputs.rates
    ):
        raise ValueError("The scenarios were not computed for this proposal's options")
    if impact is not None and impact.abstained:
        impact = None

    precedents = precedent_facts(view, precedent_ids)
    checks = (
        judge_options(scenarios.results, scenarios.inputs.churn_guardrail, precedents, rules)
        if precedents
        else []
    )
    chosen = next((c for c in checks if c.chosen), None)
    excluded = rules.required_exclusion.casefold() in {e.casefold() for e in proposal.exclusions}
    case = _Case(view)
    case.assumptions += _figure_lines(checks) + _assumed_inputs(scenarios.notes)

    def result(recommendation: Recommendation, message: str) -> RecommendationResult:
        verify_recommendation(view, recommendation)
        return RecommendationResult(
            recommendation=recommendation,
            options=checks,
            precedent_ids=[p.decision_id for p in precedents],
            required_exclusion=rules.required_exclusion,
            exclusion_in_proposal=excluded,
            message=message,
        )

    def abstain(decision_type: RecommendationType, message: str) -> RecommendationResult:
        return result(
            Recommendation(
                id=DRAFT_RECOMMENDATION_ID,
                proposal_id=proposal.id,
                decision_type=decision_type,
                reasons=case.reasons,
                excluded_options=[
                    ExcludedOption(surcharge_rate=c.surcharge_rate, reason=c.verdict)
                    for c in checks
                ],
                assumptions=case.assumptions,
                uncertainty=message,
                approval_required_by=rules.approver,
                created_at=now,
                updated_at=now,
            ),
            message,
        )

    if not precedents:
        return abstain(
            RecommendationType.INSUFFICIENT_EVIDENCE,
            f"{INSUFFICIENT_TO_RECOMMEND} There are no precedents to recommend from.",
        )
    if chosen is None:
        if not any(c.supporting_decision_ids for c in checks):
            return abstain(
                RecommendationType.INSUFFICIENT_EVIDENCE,
                f"{INSUFFICIENT_TO_RECOMMEND} None of the precedents found for this question "
                f"backs an option ({_backing_kind(rules)} outcome within {_tolerance(rules)} "
                "of its rate).",
            )
        _guardrail_reason(case, scenarios, checks)
        return abstain(
            RecommendationType.DO_NOT_PROCEED,
            "Do not proceed: every option backed by a precedent exceeds the churn guardrail "
            "or has no revenue upside.",
        )

    if not _precedent_reasons(case, chosen, precedents, rules):
        # The backing precedents cannot be cited for this role: no evidence, no advice.
        case.reasons.clear()
        chosen.chosen = False
        chosen.verdict = "Not recommended: its backing precedents could not be cited."
        return abstain(
            RecommendationType.INSUFFICIENT_EVIDENCE,
            f"{INSUFFICIENT_TO_RECOMMEND} The precedents behind the best option could not be "
            "cited.",
        )
    _guardrail_reason(case, scenarios, checks)
    pilot = chosen.surcharge_rate > rules.pilot_above_rate + _EPS
    if pilot:
        case.policy(
            f"Policy: every surcharge above {_pct(rules.pilot_above_rate)} starts as a phased "
            f"pilot with weekly KPI checks and a {rules.review_after_days}-day executive review.",
            rules.pilot_policy,
            otherwise=(
                f"Starting above {_pct(rules.pilot_above_rate)} as a phased pilot is applied as "
                "a demo rule: no source for it is available here."
            ),
        )
        case.assumptions.append(
            f"Pilot scope of {rules.pilot_markets} markets is a demo setting, not taken from "
            "any source."
        )
        case.assumptions.append(
            "The scenario figures cover the whole baseline, not the pilot markets. Scale the "
            "planned revenue to the pilot markets before tracking it."
        )
    case.policy(
        f"Policy: {rules.required_exclusion.lower()} are excluded from mid-term surcharges; "
        "changes apply only at contract renewal.",
        rules.exclusion_policy,
        otherwise=(
            f"Excluding {rules.required_exclusion.lower()} is applied as a demo rule: no "
            "source for it is available here."
        ),
    )
    for risk in key_risks(impact, rules):
        failed = [e for e in risk.evidence if e.outcome_verdict is OutcomeVerdict.FAILED]
        names = ", ".join(dict.fromkeys(e.decision_id for e in failed))
        case.cite(
            f"Key risk: {risk.label} (confidence {risk.confidence.value}; quoted from "
            f"{names}, which failed).",
            [e.ref for e in failed],  # only the failed precedents' lines support this
        )
    case.assumptions.append(
        f"Evidence rule: an option needs a {_backing_kind(rules)} precedent within "
        f"{_tolerance(rules)} of its rate, among the precedents found for this question. "
        "The tolerance is a demo setting."
    )
    if proposal.region.casefold() != rules.policy_region.casefold():
        case.assumptions.append(
            f"The cited policies were written for {rules.policy_region}; applying them to "
            f"{proposal.region} is a demo assumption."
        )
    if impact is None:
        case.assumptions.append("No impact analysis is available, so key risks were not assessed.")

    assumed = sum(n.basis is EvidenceBasis.ASSUMED_FOR_DEMO for n in scenarios.notes)
    label = "Phased pilot" if pilot else "Proceed"
    return result(
        Recommendation(
            id=DRAFT_RECOMMENDATION_ID,
            proposal_id=proposal.id,
            decision_type=(
                RecommendationType.PHASED_PILOT if pilot else RecommendationType.PROCEED
            ),
            recommended_rate=chosen.surcharge_rate,
            reasons=case.reasons,
            excluded_options=[
                ExcludedOption(surcharge_rate=c.surcharge_rate, reason=c.verdict)
                for c in checks
                if not c.chosen
            ],
            risk_level=risk_level(chosen, precedents, impact, rules),
            assumptions=case.assumptions,
            uncertainty=(
                f"Based on {len(precedents)} fictional precedent(s) and synthetic, editable "
                f"scenario inputs ({assumed} of {len(scenarios.notes)} assumed for demo). An "
                "illustration, not a forecast or an authorised decision."
            ),
            action_plan=_action_plan(
                chosen, proposal, scenarios, impact, rules, pilot=pilot, excluded=excluded
            ),
            approval_required_by=rules.approver,
            created_at=now,
            updated_at=now,
        ),
        f"{label} at {_option(chosen.surcharge_rate)}. Advisory only: approval by a named "
        f"{ROLE_LABELS[rules.approver]} is required.",
    )
