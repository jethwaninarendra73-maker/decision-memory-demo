"""Recommendation tab in the running app: the advisory card, its condition, abstention
and role filtering."""

from __future__ import annotations

import pytest

from decision_memory.agents.recommendation_agent import ADVISORY_NOTE
from decision_memory.provenance import InaccessibleEvidenceError
from decision_memory.repository import Repository
from decision_memory.ui.impact import EXCLUDE_HVC_KEY
from decision_memory.ui.recommendation import NO_QUESTION_MESSAGE, REFUSED_MESSAGE
from decision_memory.ui.scenarios import GUARDRAIL_KEY, churn_key
from decision_memory.ui.search import QUESTION_KEY

from conftest import HIDDEN_FROM_ANALYST, HIDDEN_FROM_DIRECTOR, rendered_text

OFF_TOPIC = "Should we migrate the payroll system to a new vendor?"
BOARD_QUESTION = "Board strategy: hold fixed pricing for the twenty largest strategic accounts?"


def headlines(app) -> list[str]:
    return [m.value for m in app.markdown if m.value.startswith("#### Advisory recommendation")]


def options_table(app):
    (table,) = [f.value for f in app.dataframe if "Cautions near it" in f.value.columns]
    return table


def block(app, start: str) -> str:
    """The one Markdown block that starts with `start`."""
    (found,) = [m.value for m in app.markdown if m.value.startswith(start)]
    return found


def ask(app, question: str):
    app.text_input(key=QUESTION_KEY).set_value(question)
    return app.run()


def test_demo_card_is_a_phased_pilot_with_cited_reasons(run_app) -> None:
    app = run_app()
    assert not app.exception and not app.error
    assert headlines(app) == ["#### Advisory recommendation: Phased pilot at +5%"]
    text = rendered_text(app)
    assert (
        "Risk level: **medium** · requires approval by a named **Pricing Director** "
        "(recorded by Agent 6)"
    ) in text
    assert ADVISORY_NOTE in text
    # Each reason is one block with its citations nested under it, all escaped.
    success = block(app, "- Successful precedent at \\+5%: DEC\\-004, France\\.")
    assert "\n    - [\\[SRC-006\\]](?role=pricing_director&evidence=SRC-006)" in success
    guardrail = block(app, "- Guardrail: churn loss must stay within 1\\.2%")
    assert "Exceeded by \\+7% \\(1\\.6%\\)" in guardrail and "[\\[SRC-014\\]]" in guardrail
    assert "[\\[SRC-022\\]]" in block(app, "- Policy: high\\-volume contract customers")
    assert block(app, "- Key risk: Competitive response in Germany")
    assert block(app, "- Caution at \\+5%: DEC\\-009, UK and Ireland \\(partial\\)")
    plan = block(app, "1. Exclude high\\-volume contract customers")
    assert "\n8. Human approval required: a named Pricing Director approves or rejects" in plan


def test_every_option_is_judged_in_the_table(run_app) -> None:
    table = options_table(run_app())
    assert list(table["Option"]) == ["+3%", "+5%", "+7%"]
    assert list(table["Verdict"]) == [
        "Not chosen: lower uplift (+$8.40M against +$14.00M at +5%).",
        "Recommended: highest revenue change among the eligible options.",
        (
            "Excluded: churn loss 1.6% exceeds the 1.2% guardrail; no successful precedent within "
            "±0.5 points of this rate among those found for this question."
        ),
    ]
    assert list(table["Successful precedents near it"]) == ["DEC-011", "DEC-004", "none"]
    assert list(table["Cautions near it"]) == ["none", "DEC-009, DEC-007", "none"]


def test_the_exclusion_condition_follows_the_draft_proposal(run_app) -> None:
    app = run_app()
    assert any(w.value.startswith("Condition not met yet") for w in app.warning)
    assert "does not yet: add the exclusion in tab 3" in rendered_text(app)
    app.checkbox(key=EXCLUDE_HVC_KEY).check()
    app = app.run()
    assert not any(w.value.startswith("Condition not met yet") for w in app.warning)
    text = rendered_text(app)
    assert "Condition met: the draft proposal excludes high-volume contract customers." in text
    assert "the draft proposal already does" in text


def test_off_topic_question_gives_insufficient_evidence_to_recommend(run_app) -> None:
    app = ask(run_app(), OFF_TOPIC)
    assert not app.exception and not headlines(app)
    assert any(w.value.startswith("Insufficient evidence to recommend.") for w in app.warning)
    assert not any(w.value.startswith("Condition") for w in app.warning)


def test_scenario_edits_flow_into_the_recommendation(run_app) -> None:
    app = run_app()
    app.number_input(key=GUARDRAIL_KEY).set_value(0.9)
    app = app.run()
    assert headlines(app) == ["#### Advisory recommendation: Proceed at +3%"]
    assumptions = block(app, "- \\+3%: revenue").replace("\\", "")
    assert "Churn guardrail (share of baseline): 0.9% (assumed for demo, edited)." in assumptions
    assert not [m for m in app.markdown if m.value.startswith("- Guardrail")]  # no longer cited


def test_every_backed_option_over_the_guardrail_means_do_not_proceed(run_app) -> None:
    app = run_app()
    for rate in (0.03, 0.05):
        app.number_input(key=churn_key(rate)).set_value(1.3)
    app = app.run()
    assert headlines(app) == ["#### Advisory recommendation: Do not proceed"]
    assert any(w.value.startswith("Do not proceed") for w in app.warning)
    assert block(app, "- Guardrail: churn loss must stay within 1\\.2%")
    # No action plan (its first step is the exclusion; the guide has numbered steps too).
    assert not [m for m in app.markdown if m.value.startswith("1. Exclude")]


@pytest.mark.parametrize(
    ("role", "hidden"),
    [("pricing_director", HIDDEN_FROM_DIRECTOR), ("analyst", HIDDEN_FROM_ANALYST)],
)
def test_recommendation_tab_shows_nothing_hidden(run_app, role: str, hidden) -> None:
    on_screen = rendered_text(run_app(role=role))
    for secret in hidden:
        assert secret not in on_screen


@pytest.mark.parametrize("role", ["pricing_director", "restricted_exec"])
def test_restricted_question_gets_no_recommendation_for_any_role(run_app, role: str) -> None:
    # Others cannot see DEC-015; the executive can, but a price hold backs no surcharge option.
    app = ask(run_app(role=role), BOARD_QUESTION)
    assert not app.exception and not headlines(app)
    assert any(w.value.startswith("Insufficient evidence to recommend.") for w in app.warning)


def test_empty_memory_asks_for_a_question_first(run_app, tmp_path) -> None:
    empty = tmp_path / "empty.db"
    Repository.open(empty).close()
    app = run_app(db=empty)
    assert not app.exception
    assert NO_QUESTION_MESSAGE in rendered_text(app)


def test_a_refused_recommendation_is_withheld_and_the_page_keeps_running(
    run_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args, **kwargs):
        raise InaccessibleEvidenceError("refused")

    monkeypatch.setattr("decision_memory.ui.recommendation.recommend", refuse)
    app = run_app()
    assert not app.exception and not headlines(app)
    assert REFUSED_MESSAGE in [w.value for w in app.warning]
    assert "Evidence explorer" in [s.value for s in app.subheader]
