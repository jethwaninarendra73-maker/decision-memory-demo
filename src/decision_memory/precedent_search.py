"""Precedent search: rank the decisions a role may see against a pricing question.

Deterministic and transparent. Every result carries its four score components, the
formula that combines them, the query terms it matched, and `[SRC-...]` citations for
the decision and its outcome. Nothing is learned and nothing is random.

    score = w_lexical * lexical + w_metadata * metadata + w_outcome * outcome + w_recency * recency

- lexical: share of the query's content terms found in the decision's text, each term
  weighted by how rare it is among the decisions this role can see (smoothed IDF).
- metadata: mean of the checks the query actually specifies: region match, segment
  match, and surcharge-rate proximity (1 at the same rate, 0 at `rate_tolerance` apart).
  If the query specifies none of them, metadata is not applicable: its weight is shared
  across the other three components in proportion to their weights, so the effective
  weights shown with each result still add up to 1.
- outcome: 1 when the decision has an observed outcome (success, partial or failed are
  all informative), `no_outcome_relevance` when nothing has been observed yet.
- recency: 0.5 ** (age / half-life), with the reference date passed in, never read
  from the clock here.

Abstention: a decision is a candidate only if its lexical score reaches `min_lexical`
(metadata alone does not make something a precedent), and the search abstains with
"insufficient verifiable evidence" when no candidate reaches `min_score`.

Failed precedents are returned with a warning: they show what to avoid, never a
template to repeat.

Search reads only through `MemoryView`, so hidden decisions, outcomes and sources never
enter ranking, term statistics, counts or citations.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from datetime import date
from typing import Annotated

from pydantic import Field, model_validator

from .authz import MemoryView
from .models import Contract, Decision, Fraction, NonEmptyStr, Outcome, OutcomeVerdict
from .provenance import (
    INSUFFICIENT_EVIDENCE_TEXT,
    Claim,
    decision_claim,
    outcome_claim,
)

# --- settings ---------------------------------------------------------------


class SearchWeights(Contract):
    """How much each component counts. The weights must add up to 1."""

    lexical: Fraction = 0.40
    metadata: Fraction = Field(default=0.30, lt=1.0)
    outcome: Fraction = 0.15
    recency: Fraction = 0.15

    @model_validator(mode="after")
    def _sum_to_one(self) -> SearchWeights:
        total = self.lexical + self.metadata + self.outcome + self.recency
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError(f"Search weights must add up to 1, got {total:.3f}")
        return self


class SearchSettings(Contract):
    """Editable search parameters. Defaults are demo choices, not tuned on real data."""

    weights: SearchWeights = Field(default_factory=SearchWeights)
    min_lexical: Fraction = 0.5
    min_score: Fraction = 0.45
    top_k: Annotated[int, Field(ge=3, le=5)] = 5
    recency_half_life_days: Annotated[int, Field(gt=0)] = 730
    no_outcome_relevance: Fraction = 0.3
    rate_tolerance: Annotated[float, Field(gt=0, le=1)] = 0.05


DEFAULT_SETTINGS = SearchSettings()

# --- query ------------------------------------------------------------------


class SearchQuery(Contract):
    """The question plus the structured filters it implies. All filters are optional."""

    text: NonEmptyStr
    region: str | None = None
    customer_segment: str | None = None
    surcharge_rate: Fraction | None = None
    as_of: date


_REGION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Europe", ("europe", "european", "eu")),
    # Not "us": it is far more often a pronoun than a country.
    ("North America", ("north america", "north american", "usa", "united states")),
    ("Asia-Pacific", ("asia-pacific", "asia pacific", "apac", "asia")),
)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)")


def infer_query(text: str, as_of: date) -> SearchQuery:
    """Read the filters a question states outright: a region keyword and a percentage.

    Nothing is guessed. A region is set only if exactly one region is named, and a
    rate only if exactly one percentage appears; otherwise the filter stays empty.
    """
    lowered = text.lower()
    regions = [
        region
        for region, keywords in _REGION_KEYWORDS
        if any(re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", lowered) for k in keywords)
    ]
    rates = {float(match) for match in _PERCENT.findall(lowered)}
    rate = rates.pop() / 100 if len(rates) == 1 else None
    return SearchQuery(
        text=text,
        region=regions[0] if len(regions) == 1 else None,
        surcharge_rate=rate if rate is not None and rate <= 1 else None,
        as_of=as_of,
    )


# --- text normalisation -----------------------------------------------------

STOPWORDS: frozenset[str] = frozenset(
    """
    a an the and or but if of to in on at by for from with without into onto than then
    as is are was were be been being do does did should would could can may might must
    will shall we our us you your they their it its this that these those there here
    what which who whom how why when where whether all any each some such no not only
    also just very more most less least over under up down out about after before
    next last year years quarter quarters month months week weeks
    """.split()  # noqa: SIM905 - one readable block instead of a 100-item literal
)
# Small, visible equivalences. Anything not listed is matched literally.
SYNONYMS: dict[str, str] = {
    "european": "europe",
    "apac": "asia",
    "usa": "america",
    "raise": "increase",
    "raising": "increase",
    "rise": "increase",
    "hike": "increase",
    "increasing": "increase",
    "increased": "increase",
    "charge": "surcharge",
    "contracted": "contract",
}
_PERCENT_WORD = re.compile(r"\s*\bpercent\b")
_TOKEN = re.compile(r"\d+(?:\.\d+)?%|[a-z][a-z-]*[a-z]|[a-z]")


def _stem(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def terms(text: str) -> list[str]:
    """Content terms of a text: lower-cased, hyphen-split, stopwords dropped, crude
    plural stripping and the `SYNONYMS` map applied. Percentages stay as e.g. `5%`."""
    found: list[str] = []
    for token in _TOKEN.findall(_PERCENT_WORD.sub("%", text.lower())):
        for part in [token] if token.endswith("%") else token.split("-"):
            if not part or part in STOPWORDS:
                continue
            word = part if part.endswith("%") else _stem(part)
            word = SYNONYMS.get(word, SYNONYMS.get(part, word))
            if word not in STOPWORDS:
                found.append(word)
    return list(dict.fromkeys(found))


def _decision_text(decision: Decision, outcome: Outcome | None) -> str:
    parts: list[str] = [
        decision.title,
        decision.statement,
        decision.region,
        decision.market,
        decision.customer_segment,
        decision.rationale,
        decision.expected_outcome,
        *decision.risks,
        *decision.alternatives,
    ]
    if decision.surcharge_rate is not None:
        parts.append(f"{decision.surcharge_rate * 100:g}%")
    if outcome is not None:
        parts.append(outcome.explanation)
    return "\n".join(parts)


# --- results ----------------------------------------------------------------


class ScoreComponent(Contract):
    """One part of a precedent's score, with the reasoning shown to the user."""

    name: NonEmptyStr
    value: Fraction
    weight: Fraction  # effective weight used in the score (0 when not applicable)
    applicable: bool = True
    explanation: NonEmptyStr

    @property
    def contribution(self) -> float:
        return self.value * self.weight


FAILED_WARNING = (
    "Failed precedent: shown as a warning about what went wrong, not as a template to repeat."
)


class Precedent(Contract):
    """A ranked decision with its score breakdown and citations."""

    decision_id: NonEmptyStr
    title: NonEmptyStr
    decision_date: date
    region: NonEmptyStr
    market: NonEmptyStr
    customer_segment: NonEmptyStr
    surcharge_rate: Fraction | None = None
    score: Fraction
    components: Annotated[list[ScoreComponent], Field(min_length=4, max_length=4)]
    matched_terms: list[str] = Field(default_factory=list)
    candidate: bool
    outcome_id: str | None = None
    outcome_verdict: OutcomeVerdict | None = None
    warning: str | None = None
    decision_claim: Claim
    outcome_claim: Claim | None = None

    def component(self, name: str) -> ScoreComponent:
        return next(component for component in self.components if component.name == name)


class SearchResult(Contract):
    """Top precedents, or an abstention with the reason. `ranked` lists every decision
    the role may see, in score order, for transparency."""

    query: SearchQuery
    settings: SearchSettings
    query_terms: list[str]
    precedents: list[Precedent]
    ranked: list[Precedent]
    abstained: bool
    message: str

    @property
    def precedent_ids(self) -> list[str]:
        return [precedent.decision_id for precedent in self.precedents]

    @property
    def cited_source_ids(self) -> list[str]:
        """Every source the returned precedents cite, in order, without duplicates."""
        ids = [
            item.source_id
            for precedent in self.precedents
            for claim in (precedent.decision_claim, precedent.outcome_claim)
            if claim is not None
            for item in claim.evidence
        ]
        return list(dict.fromkeys(ids))


# --- scoring ----------------------------------------------------------------


def _idf(corpus: Iterable[set[str]]) -> tuple[dict[str, float], float]:
    """Smoothed IDF per term, plus the weight of a term no visible decision contains."""
    documents = list(corpus)
    total = len(documents)
    frequency: dict[str, int] = {}
    for document in documents:
        for term in document:
            frequency[term] = frequency.get(term, 0) + 1
    idf = {term: math.log((1 + total) / (1 + df)) + 1 for term, df in frequency.items()}
    return idf, math.log(1 + total) + 1


def _lexical(
    query_terms: list[str], document: set[str], idf: dict[str, float], unseen: float
) -> tuple[float, list[str], str]:
    if not query_terms:
        return 0.0, [], "The question has no content terms to match."
    weights = {term: idf.get(term, unseen) for term in query_terms}
    matched = [term for term in query_terms if term in document]
    value = sum(weights[term] for term in matched) / sum(weights.values())
    shown = ", ".join(matched) if matched else "none"
    return value, matched, (
        f"Matched {len(matched)} of {len(query_terms)} query terms ({shown}), "
        "weighted by rarity across visible decisions."
    )


def _same(left: str, right: str) -> bool:
    return left.strip().casefold() == right.strip().casefold()


def _metadata(decision: Decision, query: SearchQuery, tolerance: float) -> tuple[float | None, str]:
    checks: list[tuple[float, str]] = []
    if query.region:
        hit = _same(decision.region, query.region)
        checks.append((1.0 if hit else 0.0, f"region {decision.region} {'=' if hit else '≠'} {query.region}"))
    if query.customer_segment:
        hit = _same(decision.customer_segment, query.customer_segment)
        checks.append(
            (1.0 if hit else 0.0, f"segment {decision.customer_segment} {'=' if hit else '≠'} {query.customer_segment}")
        )
    if query.surcharge_rate is not None:
        if decision.surcharge_rate is None:
            checks.append((0.0, f"no surcharge rate recorded vs {query.surcharge_rate:.1%}"))
        else:
            gap = abs(decision.surcharge_rate - query.surcharge_rate)
            value = max(0.0, 1.0 - gap / tolerance)
            checks.append(
                (value, f"rate {decision.surcharge_rate:.1%} vs {query.surcharge_rate:.1%} → {value:.2f}")
            )
    if not checks:
        return None, (
            "Not applicable: the question sets no region, segment or rate. "
            "Its weight is shared across the other components."
        )
    value = sum(score for score, _ in checks) / len(checks)
    return value, f"Mean of {len(checks)} check(s): " + "; ".join(text for _, text in checks) + "."


def _recency(decided: date, as_of: date, half_life_days: int) -> tuple[float, str]:
    age = max(0, (as_of - decided).days)
    value = 0.5 ** (age / half_life_days)
    return value, (
        f"Decided {decided.isoformat()}, {age} days before {as_of.isoformat()}; "
        f"0.5^({age}/{half_life_days}) = {value:.2f}."
    )


def score_decision(
    view: MemoryView,
    decision: Decision,
    outcome: Outcome | None,
    query: SearchQuery,
    query_terms: list[str],
    idf: dict[str, float],
    unseen: float,
    settings: SearchSettings,
) -> Precedent:
    """Score one visible decision. Pure apart from reading citations through `view`."""
    weights = settings.weights
    document = set(terms(_decision_text(decision, outcome)))
    lexical, matched, lexical_note = _lexical(query_terms, document, idf, unseen)
    metadata, metadata_note = _metadata(decision, query, settings.rate_tolerance)
    if outcome is None:
        outcome_value = settings.no_outcome_relevance
        outcome_note = "No outcome observed yet, so it says less about what to expect."
    else:
        outcome_value = 1.0
        outcome_note = (
            f"Observed outcome: {outcome.verdict.value} ({outcome.observed_on.isoformat()})."
        )
    recency, recency_note = _recency(decision.decision_date, query.as_of, settings.recency_half_life_days)
    # Without metadata checks, scale the other weights up so they still add up to 1.
    scale = 1.0 if metadata is not None else 1.0 / (1.0 - weights.metadata)
    components = [
        ScoreComponent(
            name="lexical", value=lexical, weight=weights.lexical * scale, explanation=lexical_note
        ),
        ScoreComponent(
            name="metadata",
            value=metadata or 0.0,
            weight=weights.metadata if metadata is not None else 0.0,
            applicable=metadata is not None,
            explanation=metadata_note,
        ),
        ScoreComponent(
            name="outcome",
            value=outcome_value,
            weight=weights.outcome * scale,
            explanation=outcome_note,
        ),
        ScoreComponent(
            name="recency", value=recency, weight=weights.recency * scale, explanation=recency_note
        ),
    ]
    score = min(1.0, sum(component.contribution for component in components))
    failed = outcome is not None and outcome.verdict is OutcomeVerdict.FAILED
    return Precedent(
        decision_id=decision.id,
        title=decision.title,
        decision_date=decision.decision_date,
        region=decision.region,
        market=decision.market,
        customer_segment=decision.customer_segment,
        surcharge_rate=decision.surcharge_rate,
        score=round(score, 4),
        components=components,
        matched_terms=matched,
        candidate=lexical >= settings.min_lexical,
        outcome_id=None if outcome is None else outcome.id,
        outcome_verdict=None if outcome is None else outcome.verdict,
        warning=FAILED_WARNING if failed else None,
        decision_claim=decision_claim(view, decision.id),
        outcome_claim=None if outcome is None else outcome_claim(view, outcome.id),
    )


def _latest_outcomes(outcomes: Iterable[Outcome]) -> dict[str, Outcome]:
    """The most recent visible outcome per decision."""
    latest: dict[str, Outcome] = {}
    for outcome in sorted(outcomes, key=lambda o: (o.observed_on, o.id)):
        latest[outcome.decision_id] = outcome
    return latest


def search_precedents(
    view: MemoryView, query: SearchQuery, settings: SearchSettings = DEFAULT_SETTINGS
) -> SearchResult:
    """Rank every decision the role may see and return the top precedents, or abstain."""
    decisions = view.decisions()
    outcomes = _latest_outcomes(view.outcomes())
    query_terms = terms(query.text)
    idf, unseen = _idf(set(terms(_decision_text(d, outcomes.get(d.id)))) for d in decisions)

    ranked = sorted(
        (
            score_decision(view, d, outcomes.get(d.id), query, query_terms, idf, unseen, settings)
            for d in decisions
        ),
        key=lambda p: (-p.score, -p.decision_date.toordinal(), p.decision_id),
    )
    # A precedent must be topical, scored above the bar, and backed by visible evidence.
    precedents = [
        p
        for p in ranked
        if p.candidate and p.score >= settings.min_score and p.decision_claim.supported
    ][: settings.top_k]

    if precedents:
        message = f"{len(precedents)} precedent(s) above the {settings.min_score:.2f} threshold."
    else:
        message = (
            f"{INSUFFICIENT_EVIDENCE_TEXT} No visible decision matches this question closely "
            f"enough (needs lexical ≥ {settings.min_lexical:.2f} and score ≥ "
            f"{settings.min_score:.2f})."
        )
    return SearchResult(
        query=query,
        settings=settings,
        query_terms=query_terms,
        precedents=precedents,
        ranked=ranked,
        abstained=not precedents,
        message=message,
    )
