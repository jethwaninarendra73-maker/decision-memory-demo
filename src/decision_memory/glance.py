"""The answer at a glance: the question, the recommendation and the three figures behind
it, for the panel at the top of the page.

Pure and deterministic: no Streamlit, no database. Everything comes from what the agents
computed for the active role on this run (search, scenarios, recommendation), so a role
never sees more here than in the tabs, and no figure is typed in.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .models import (
    Contract,
    NonEmptyStr,
    OptionCheck,
    OutcomeVerdict,
    RecommendationResult,
    RecommendationType,
    ScenarioSet,
)
from .precedent_search import SearchResult


class OptionCard(Contract):
    """One surcharge option as a card: its figures and a one-word verdict."""

    rate: float
    revenue_change_musd: float
    margin_change_musd: float
    churn_loss_fraction: float
    within_guardrail: bool
    verdict: NonEmptyStr  # "Recommended", "Ruled out" or "Not chosen"
    reason: NonEmptyStr  # the recommendation's own wording, after its verdict word


class Glance(Contract):
    """What the panel shows. Figures are None when there is nothing to show yet."""

    question: str
    asked: bool
    answer: str | None = None  # "+5% pilot", "Do not proceed", ...
    answer_note: str | None = None  # "Phased, in 2 markets; a Pricing Director approves"
    names_a_rate: bool = False
    revenue_change_musd: float | None = None
    margin_change_musd: float | None = None
    churn_loss_fraction: float | None = None
    churn_guardrail: float | None = None
    precedents: Annotated[int, Field(ge=0)] = 0
    failed_precedents: Annotated[int, Field(ge=0)] = 0
    lessons: Annotated[int, Field(ge=0)] = 0
    abstained: bool = False


def _pct(fraction: float) -> str:
    return f"{round(fraction * 100, 3):g}%"


def short_answer(result: RecommendationResult) -> str:
    """The recommendation in two or three words, for a figure tile."""
    rec = result.recommendation
    rate = rec.recommended_rate
    if rec.decision_type is RecommendationType.PHASED_PILOT and rate is not None:
        return f"+{_pct(rate)} pilot"
    if rec.decision_type is RecommendationType.PROCEED and rate is not None:
        return f"+{_pct(rate)} surcharge"
    if rec.decision_type is RecommendationType.DO_NOT_PROCEED:
        return "Do not proceed"
    return "Not enough evidence"


def option_card(option: OptionCheck) -> OptionCard:
    """A card for one option, labelled from the recommendation's own judgement."""
    if option.chosen:
        verdict = "Recommended"
    elif not option.within_guardrail or not option.eligible:
        verdict = "Ruled out"
    else:
        verdict = "Not chosen"
    _, _, reason = option.verdict.partition(":")
    return OptionCard(
        rate=option.surcharge_rate,
        revenue_change_musd=option.revenue_change_musd,
        margin_change_musd=option.margin_change_musd,
        churn_loss_fraction=option.churn_loss_fraction,
        within_guardrail=option.within_guardrail,
        verdict=verdict,
        reason=(reason or option.verdict).strip(),
    )


def build_glance(
    question: str,
    search: SearchResult | None,
    scenarios: ScenarioSet | None,
    result: RecommendationResult | None,
    pilot_markets: int,
    approver_label: str | None,
) -> Glance:
    """Collect the panel's content from this run's results for the active role."""
    if search is None:
        return Glance(question=question, asked=False)
    failed = [p for p in search.precedents if p.outcome_verdict is OutcomeVerdict.FAILED]
    glance = Glance(
        question=search.query.text,
        asked=True,
        precedents=len(search.precedents),
        failed_precedents=len(failed),
        lessons=len(search.cross_region),
        abstained=search.abstained,
        churn_guardrail=scenarios.inputs.churn_guardrail if scenarios else None,
    )
    if result is None:
        return glance
    rec = result.recommendation
    updates: dict[str, object] = {"answer": short_answer(result)}
    chosen = next((o for o in result.options if o.chosen), None)
    if rec.recommended_rate is not None and chosen is not None:
        scope = (
            f"Phased, in {pilot_markets} markets first"
            if rec.decision_type is RecommendationType.PHASED_PILOT
            else "Full rollout"
        )
        approver = f"; a named {approver_label} approves" if approver_label else ""
        updates |= {
            "answer_note": scope + approver,
            "names_a_rate": True,
            "revenue_change_musd": chosen.revenue_change_musd,
            "margin_change_musd": chosen.margin_change_musd,
            "churn_loss_fraction": chosen.churn_loss_fraction,
        }
    else:
        updates["answer_note"] = result.message
    return glance.model_copy(update=updates)
