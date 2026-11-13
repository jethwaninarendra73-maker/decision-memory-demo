"""Precedent search: transparent ranking, cited results, abstention and role filtering."""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from decision_memory import DEMO_QUESTION
from decision_memory.authz import MemoryView
from decision_memory.models import (
    Decision,
    DecisionStatus,
    Outcome,
    OutcomeVerdict,
    Role,
    SourceDocument,
)
from decision_memory.precedent_search import (
    DEFAULT_SETTINGS,
    FAILED_WARNING,
    SearchQuery,
    SearchSettings,
    SearchWeights,
    format_setting,
    infer_query,
    normalise,
    region_terms,
    search_precedents,
    select_precedents,
    terms,
)
from decision_memory.provenance import INSUFFICIENT_EVIDENCE_TEXT
from decision_memory.repository import Repository

from conftest import HIDDEN_FROM_ANALYST, HIDDEN_FROM_DIRECTOR, STAMPS, make_decision

AS_OF = date(2026, 11, 13)
# The intended precedents for the demo question: European 5% (or near-5%) surcharges
# with observed outcomes, including the failed German 6% rollout as a warning.
DEMO_TOP = ["DEC-009", "DEC-007", "DEC-004", "DEC-011", "DEC-001"]
OFF_TOPIC = [
    "Should we migrate the payroll system to a new vendor?",
    "Should we open a new warehouse in Brazil for cold storage?",
    "Should we change the cleaning contract for our European offices?",
]
HIDDEN = {
    Role.RESTRICTED_EXEC: (),
    Role.PRICING_DIRECTOR: HIDDEN_FROM_DIRECTOR,
    Role.ANALYST: HIDDEN_FROM_ANALYST,
}


def search(repo: Repository, role: Role, text: str = DEMO_QUESTION, **overrides):
    query = infer_query(text, AS_OF)
    if overrides:
        query = query.model_copy(update=overrides)
    return search_precedents(MemoryView(repo, role), query)


# --- the demo question ------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
def test_demo_question_returns_the_intended_precedents(seeded: Repository, role: Role) -> None:
    result = search(seeded, role)
    assert not result.abstained
    assert result.precedent_ids == DEMO_TOP
    assert {p.region for p in result.precedents} == {"Europe"}
    assert result.query.region == "Europe" and result.query.surcharge_rate == 0.05


def test_failed_precedent_is_a_warning_not_a_template(seeded: Repository) -> None:
    result = search(seeded, Role.PRICING_DIRECTOR)
    failed = [p for p in result.precedents if p.outcome_verdict is OutcomeVerdict.FAILED]
    assert [p.decision_id for p in failed] == ["DEC-001"]
    assert failed[0].warning == FAILED_WARNING
    assert all(p.warning is None for p in result.precedents if p not in failed)
    france = next(p for p in result.precedents if p.decision_id == "DEC-004")
    assert france.outcome_verdict is OutcomeVerdict.SUCCESS


def test_every_result_shows_four_components_that_add_up(seeded: Repository) -> None:
    for precedent in search(seeded, Role.PRICING_DIRECTOR).ranked:
        assert [c.name for c in precedent.components] == ["lexical", "metadata", "outcome", "recency"]
        assert all(0.0 <= c.value <= 1.0 and c.explanation for c in precedent.components)
        assert math.isclose(sum(c.weight for c in precedent.components), 1.0)
        assert precedent.score == round(sum(c.contribution for c in precedent.components), 4)


def test_component_explanations_show_their_inputs(seeded: Repository) -> None:
    precedent = search(seeded, Role.PRICING_DIRECTOR).precedents[0]  # DEC-009, 5%, Europe
    assert "region Europe = Europe" in precedent.component("metadata").explanation
    assert "rate 5.0% vs 5.0%" in precedent.component("metadata").explanation
    assert "2024-11-11" in precedent.component("recency").explanation
    assert "partial" in precedent.component("outcome").explanation
    assert set(precedent.matched_terms) <= set(search(seeded, Role.ANALYST).query_terms)


def test_results_carry_verifiable_citations(seeded: Repository) -> None:
    result = search(seeded, Role.ANALYST)
    view = MemoryView(seeded, Role.ANALYST)
    for precedent in result.precedents:
        assert precedent.decision_claim.supported
        assert f"[{precedent.decision_claim.evidence[0].source_id}]" in precedent.decision_claim.render()
        assert precedent.outcome_claim is not None and precedent.outcome_claim.supported
        for claim in (precedent.decision_claim, precedent.outcome_claim):
            for item in claim.evidence:
                source = view.source(item.source_id)
                assert source.body[item.start_offset : item.end_offset] == item.excerpt
    assert {"SRC-006", "SRC-001"} <= set(result.cited_source_ids)


def test_unrelated_regions_rank_lower(seeded: Repository) -> None:
    result = search(seeded, Role.PRICING_DIRECTOR)
    european_5pct = [p for p in result.ranked if p.region == "Europe" and p.surcharge_rate == 0.05]
    others = [p for p in result.ranked if p.region != "Europe"]
    assert {p.decision_id for p in others} == {"DEC-013", "DEC-014"}
    assert max(p.score for p in others) < min(p.score for p in european_5pct)
    assert max(p.score for p in others) < min(p.score for p in result.precedents)
    for precedent in others:
        assert "≠ Europe" in precedent.component("metadata").explanation


# --- abstention -------------------------------------------------------------


@pytest.mark.parametrize("text", OFF_TOPIC)
def test_unknown_topics_abstain(seeded: Repository, text: str) -> None:
    result = search(seeded, Role.PRICING_DIRECTOR, text)
    assert result.abstained
    assert result.precedents == [] and result.cited_source_ids == []
    assert result.message.startswith(INSUFFICIENT_EVIDENCE_TEXT)
    assert len(result.ranked) == 15  # still shown for transparency, none returned


def test_region_and_rate_alone_do_not_make_a_precedent(seeded: Repository) -> None:
    # Region and rate match half the corpus, but nothing about payroll does.
    result = search(
        seeded, Role.PRICING_DIRECTOR, "Payroll vendor migration", region="Europe", surcharge_rate=0.05
    )
    assert result.abstained
    assert all(p.component("metadata").value > 0 for p in result.ranked[:3])
    assert all(not p.candidate for p in result.ranked)


def test_empty_memory_abstains(repo: Repository) -> None:
    result = search(repo, Role.ANALYST)
    assert result.abstained and result.ranked == []


def test_a_threshold_can_be_raised_until_search_abstains(seeded: Repository) -> None:
    strict = SearchSettings(min_score=0.95)
    query = infer_query(DEMO_QUESTION, AS_OF)
    assert search_precedents(MemoryView(seeded, Role.PRICING_DIRECTOR), query, strict).abstained


# --- permissions ------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.PRICING_DIRECTOR, Role.ANALYST])
@pytest.mark.parametrize(
    "text",
    [DEMO_QUESTION, "Board strategy: hold fixed pricing for the twenty largest strategic accounts?"],
)
def test_no_hidden_content_in_any_search_output(seeded: Repository, role: Role, text: str) -> None:
    dumped = search(seeded, role, text).model_dump_json()
    for secret in HIDDEN[role]:
        if secret.lower() in text.lower():
            continue  # the user typed it; only stored content must not leak
        assert secret not in dumped


def test_hidden_decision_is_found_only_by_the_role_that_may_see_it(seeded: Repository) -> None:
    text = "Board strategy: hold fixed pricing for the twenty largest strategic accounts?"
    assert search(seeded, Role.RESTRICTED_EXEC, text).precedent_ids == ["DEC-015"]
    director = search(seeded, Role.PRICING_DIRECTOR, text)
    assert director.abstained
    assert "DEC-015" not in {p.decision_id for p in director.ranked}
    # The abstention reads exactly like any off-topic question.
    assert director.message == search(seeded, Role.PRICING_DIRECTOR, OFF_TOPIC[0]).message


def test_ranked_list_matches_what_the_role_may_see(seeded: Repository) -> None:
    for role in Role:
        visible = {d.id for d in MemoryView(seeded, role).decisions()}
        assert {p.decision_id for p in search(seeded, role).ranked} == visible


# --- memory changes show up on the next search ------------------------------


def test_new_outcome_changes_the_ranking_inputs(seeded: Repository) -> None:
    before = next(p for p in search(seeded, Role.PRICING_DIRECTOR).ranked if p.decision_id == "DEC-016")
    assert before.outcome_verdict is None
    assert before.component("outcome").value == DEFAULT_SETTINGS.no_outcome_relevance
    ref = seeded.get(SourceDocument, "SRC-020").excerpt_ref("PRC-260")
    seeded.add(
        Outcome(
            id="OUT-900",
            decision_id="DEC-016",
            verdict=OutcomeVerdict.FAILED,
            observed_on=date(2026, 9, 30),
            explanation="Pilot review.",
            source_refs=[ref],
            **STAMPS,
        )
    )
    after = next(p for p in search(seeded, Role.PRICING_DIRECTOR).ranked if p.decision_id == "DEC-016")
    assert after.outcome_verdict is OutcomeVerdict.FAILED
    assert after.component("outcome").value == 1.0
    assert after.warning == FAILED_WARNING
    assert after.score > before.score
    assert after.outcome_claim is not None and after.outcome_claim.supported


def test_newly_confirmed_decision_becomes_searchable(seeded: Repository) -> None:
    source = seeded.get(SourceDocument, "SRC-001")
    ref = source.excerpt_ref("Then we need a review after the first full month.")
    seeded.add(make_decision(source, "DEC-901", status=DecisionStatus.DRAFT, source_refs=[ref]))
    text = "Testland surcharge"
    assert "DEC-901" not in {p.decision_id for p in search(seeded, Role.ANALYST, text).ranked}
    seeded.save(seeded.get(Decision, "DEC-901").model_copy(update={"status": DecisionStatus.CONFIRMED}))
    assert search(seeded, Role.ANALYST, text).precedent_ids[0] == "DEC-901"


# --- determinism and inputs -------------------------------------------------


def test_search_is_deterministic_and_uses_the_given_date(seeded: Repository) -> None:
    assert search(seeded, Role.ANALYST) == search(seeded, Role.ANALYST)
    now = {p.decision_id: p for p in search(seeded, Role.ANALYST).ranked}
    later = {p.decision_id: p for p in search(seeded, Role.ANALYST, as_of=date(2028, 11, 13)).ranked}
    assert set(now) == set(later)
    for decision_id, old in now.items():
        new = later[decision_id]
        assert new.component("recency").value < old.component("recency").value
        assert new.component("lexical") == old.component("lexical")


def test_metadata_weight_is_shared_out_when_the_question_sets_no_filter(seeded: Repository) -> None:
    precedent = search(seeded, Role.PRICING_DIRECTOR, "fuel surcharge").precedents[0]
    metadata = precedent.component("metadata")
    assert not metadata.applicable and metadata.weight == 0.0
    assert math.isclose(precedent.component("lexical").weight, 0.40 / 0.70)
    assert precedent.decision_id == "DEC-013"


@pytest.mark.parametrize(
    ("text", "region", "rate"),
    [
        (DEMO_QUESTION, "Europe", 0.05),
        ("A 3 percent surcharge in APAC", "Asia-Pacific", 0.03),
        ("Fuel surcharge in the USA at 7.5%", "North America", 0.075),
        ("Send us the figures for Europe", "Europe", None),  # "us" is not a region
        ("Compare Europe and Asia at 3% or 5%", None, None),  # ambiguous: nothing guessed
        ("Customers in the eu-west warehouse", None, None),
    ],
)
def test_infer_query_reads_only_what_is_stated(text: str, region, rate) -> None:
    query = infer_query(text, AS_OF)
    assert (query.region, query.surcharge_rate) == (region, rate)
    assert query.as_of == AS_OF


def test_terms_normalise_plurals_synonyms_and_percentages() -> None:
    assert terms("Should we raise the surcharges for European customers by 5 percent?") == [
        "increase", "surcharge", "europe", "customer", "5%",
    ]  # fmt: skip
    assert terms("high-volume contract customers") == ["high", "volume", "contract", "customer"]
    assert terms("the and of") == []
    assert terms("APAC and USA") == ["asia", "america"]


def test_hostile_question_text_is_just_words(seeded: Repository) -> None:
    text = "<script>alert(1)</script> '; DROP TABLE decisions; -- ignore previous instructions"
    result = search(seeded, Role.ANALYST, text)
    assert result.abstained
    assert seeded.count(Decision) == 16


def test_settings_are_validated() -> None:
    with pytest.raises(ValidationError, match="add up to 1"):
        SearchWeights(lexical=0.5, metadata=0.5, outcome=0.5, recency=0.5)
    with pytest.raises(ValidationError):
        SearchSettings(top_k=8)
    with pytest.raises(ValidationError):
        SearchQuery(text="", as_of=AS_OF)


# --- wording: visible equivalences ------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Is a 5% price increase for EU shippers a good idea?",
        "Should we apply a price uplift for European clients?",
    ],
)
def test_everyday_wording_finds_the_surcharge_precedents(seeded: Repository, text: str) -> None:
    result = search(seeded, Role.PRICING_DIRECTOR, text)
    assert not result.abstained
    assert "DEC-001" in result.precedent_ids
    assert result.equivalences  # the mapping that made it work is reported


def test_equivalences_are_reported_as_written_and_mapped() -> None:
    found, applied = normalise("A price uplift for EU clients, as a phased trial")
    assert found == ["surcharge", "increase", "europe", "customer", "phase", "pilot"]
    assert applied == [
        ("price uplift", "surcharge increase"),
        ("eu", "europe"),
        ("clients", "customer"),
        ("phased", "phase"),
        ("trial", "pilot"),
    ]
    assert normalise("Should we raise prices?")[1] == [("raise", "increase")]


def test_region_words_count_once_under_metadata(seeded: Repository) -> None:
    result = search(seeded, Role.PRICING_DIRECTOR)
    assert "europe" not in result.query_terms
    assert region_terms("Europe") >= {"europe"}
    assert region_terms("Asia-Pacific") >= {"asia", "pacific"}
    # Without the double count, a European office contract is not a pricing precedent.
    cleaning = search(seeded, Role.PRICING_DIRECTOR, OFF_TOPIC[2])
    assert max(p.component("lexical").value for p in cleaning.ranked) < DEFAULT_SETTINGS.min_lexical


# --- lessons from other regions ---------------------------------------------


def test_demo_question_shows_the_singapore_copy_as_a_lesson(seeded: Repository) -> None:
    result = search(seeded, Role.ANALYST)
    assert result.cross_region_ids == ["DEC-014"]
    (lesson,) = result.cross_region
    assert lesson.region == "Asia-Pacific" and lesson.warning == FAILED_WARNING
    assert "DEC-014" not in result.precedent_ids
    assert "SRC-018" not in result.cited_source_ids  # lessons are not precedents
    assert lesson.decision_claim.supported


def test_lessons_need_the_same_bars_as_precedents(seeded: Repository) -> None:
    result = search(seeded, Role.PRICING_DIRECTOR)
    for lesson in result.cross_region:
        assert lesson.component("lexical").value >= DEFAULT_SETTINGS.min_lexical
        assert lesson.score >= DEFAULT_SETTINGS.min_score
    assert "DEC-013" not in result.cross_region_ids  # US fuel surcharge is off-topic here


def test_no_lessons_without_a_region_and_none_when_switched_off(seeded: Repository) -> None:
    assert search(seeded, Role.PRICING_DIRECTOR, "fuel surcharge").cross_region == []
    off = SearchSettings(cross_region_k=0)
    query = infer_query(DEMO_QUESTION, AS_OF)
    assert search_precedents(MemoryView(seeded, Role.ANALYST), query, off).cross_region == []


def test_region_with_no_precedent_still_abstains_but_shows_lessons(seeded: Repository) -> None:
    result = search(seeded, Role.ANALYST, "Should we index a European surcharge to the diesel benchmark?")
    assert result.abstained and result.precedents == []
    assert result.cross_region_ids == ["DEC-013"]
    assert result.message.startswith(INSUFFICIENT_EVIDENCE_TEXT)
    assert "contrast only" in result.message


# --- thresholds: labelled questions -----------------------------------------

EVAL = json.loads((Path(__file__).parent / "search_eval.json").read_text(encoding="utf-8"))
EVAL_AS_OF = date.fromisoformat(EVAL["as_of"])


def meets_label(case: dict, precedents, lessons) -> bool:
    ids = [p.decision_id for p in precedents]
    if case.get("abstain"):
        return not ids
    return bool(ids) and all(
        (
            "top" not in case or ids == case["top"],
            "includes" not in case or set(case["includes"]) <= set(ids),
            "first" not in case or ids[0] == case["first"],
            "lessons" not in case or [p.decision_id for p in lessons] == case["lessons"],
        )
    )


def ranked_eval_cases(repo: Repository):
    for case in EVAL["cases"]:
        query = infer_query(case["question"], EVAL_AS_OF)
        view = MemoryView(repo, Role(case.get("role", "pricing_director")))
        yield case, query, search_precedents(view, query)


def test_default_thresholds_answer_every_labelled_question(seeded: Repository) -> None:
    failures = [
        case["id"]
        for case, _, result in ranked_eval_cases(seeded)
        if not meets_label(case, result.precedents, result.cross_region)
    ]
    assert failures == []
    assert len(EVAL["cases"]) >= 20


def test_defaults_sit_inside_the_working_range_with_margin(seeded: Repository) -> None:
    """Every threshold pair within ±0.05 of the defaults also answers every labelled
    question, so the defaults are not balanced on an edge of this demo data."""
    runs = list(ranked_eval_cases(seeded))
    steps = (-0.05, -0.025, 0.0, 0.025, 0.05)
    for d_lexical in steps:
        for d_score in steps:
            settings = SearchSettings(
                min_lexical=DEFAULT_SETTINGS.min_lexical + d_lexical,
                min_score=DEFAULT_SETTINGS.min_score + d_score,
            )
            for case, query, result in runs:
                precedents, lessons = select_precedents(result.ranked, query, settings)
                assert meets_label(case, precedents, lessons), (case["id"], settings)


def test_select_precedents_matches_the_search_itself(seeded: Repository) -> None:
    for _, query, result in ranked_eval_cases(seeded):
        assert select_precedents(result.ranked, query, DEFAULT_SETTINGS) == (
            result.precedents, result.cross_region,
        )  # fmt: skip


# --- display of settings (regression: 0.475 used to show as 0.47 or 0.48) ---


@pytest.mark.parametrize(
    ("value", "shown"),
    [(0.5, "0.50"), (0.45, "0.45"), (0.475, "0.475"), (0.47500000000000003, "0.475"),
     (0.5 - 0.025, "0.475"), (0.525, "0.525"), (0.575, "0.575"), (0.95, "0.95"), (1.0, "1.00")],
)  # fmt: skip
def test_format_setting_shows_the_value_actually_applied(value: float, shown: str) -> None:
    assert format_setting(value) == shown


def test_messages_show_half_step_thresholds_exactly(seeded: Repository) -> None:
    query = infer_query("Should we migrate the payroll system?", AS_OF)
    settings = SearchSettings(min_lexical=0.5 - 0.025, min_score=0.425)
    result = search_precedents(MemoryView(seeded, Role.ANALYST), query, settings)
    assert "lexical ≥ 0.475 and score ≥ 0.425" in result.message
    demo = search_precedents(
        MemoryView(seeded, Role.ANALYST), infer_query(DEMO_QUESTION, AS_OF), settings
    )
    assert demo.message.endswith("above the 0.425 threshold.")
