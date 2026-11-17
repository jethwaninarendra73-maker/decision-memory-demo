"""The answer at a glance and the option cards (after the release): every figure comes
from what the agents computed for the role, and the verdicts are the recommendation's."""

from __future__ import annotations

import pytest

from decision_memory.authz import MemoryView
from decision_memory.glance import build_glance, option_card, short_answer
from decision_memory.models import Role
from decision_memory.precedent_search import infer_query, search_precedents
from decision_memory.repository import Repository
from decision_memory.ui.scenarios import BASELINE_KEY, churn_key
from decision_memory.ui.search import QUESTION_KEY

from conftest import HIDDEN_FROM_ANALYST, rendered_text
from test_governance_agent import NOW, OFF_TOPIC, QUESTION, pipeline


def panel(app) -> dict[str, str]:
    """The answer-at-a-glance tiles: the first tile with each label (it sits at the top)."""
    tiles: dict[str, str] = {}
    for metric in app.metric:
        tiles.setdefault(metric.label, metric.value)
    return tiles


def glance_for(repo: Repository, role: Role = Role.PRICING_DIRECTOR, question: str = QUESTION):
    view = MemoryView(repo, role)
    search = search_precedents(view, infer_query(question, NOW.date()))
    _, scenarios, result = pipeline(repo, role, question=question)
    return build_glance(question, search, scenarios, result, 2, "Pricing Director")


def test_the_glance_carries_the_recommended_option_and_its_evidence(seeded: Repository) -> None:
    glance = glance_for(seeded)
    assert glance.asked and glance.names_a_rate
    assert glance.answer == "+5% pilot"
    assert glance.answer_note == "Phased, in 2 markets first; a named Pricing Director approves"
    # The +5% figures are the ones tab 4 computes from the default inputs.
    assert round(glance.revenue_change_musd, 2) == 14.00
    assert round(glance.margin_change_musd, 2) == 9.50
    assert glance.churn_loss_fraction == pytest.approx(0.010)
    assert glance.churn_guardrail == pytest.approx(0.012)
    assert (glance.precedents, glance.failed_precedents, glance.lessons) == (5, 1, 1)


def test_an_off_topic_question_shows_no_figures(seeded: Repository) -> None:
    glance = glance_for(seeded, question=OFF_TOPIC)
    assert glance.abstained and glance.precedents == 0
    assert glance.answer == "Not enough evidence" and not glance.names_a_rate
    assert glance.revenue_change_musd is None and glance.churn_loss_fraction is None


def test_nothing_is_shown_before_a_question_is_asked() -> None:
    glance = build_glance("", None, None, None, 2, None)
    assert not glance.asked and glance.answer is None


def test_option_cards_use_the_recommendations_own_verdicts(seeded: Repository) -> None:
    *_, result = pipeline(seeded)
    cards = {round(o.surcharge_rate, 2): option_card(o) for o in result.options}
    assert cards[0.05].verdict == "Recommended"
    assert cards[0.07].verdict == "Ruled out" and not cards[0.07].within_guardrail
    assert cards[0.03].verdict == "Not chosen"
    assert cards[0.03].reason.startswith("lower uplift")  # the verdict word is the badge
    assert short_answer(result) == "+5% pilot"


def test_the_panel_is_at_the_top_and_follows_the_inputs(run_app) -> None:
    app = run_app()
    tiles = panel(app)
    assert tiles["Recommendation"] == "+5% pilot"
    assert tiles["Revenue per quarter"] == "+$14.00M"
    assert tiles["Customers lost"] == "1%"
    assert tiles["Past decisions"] == "5"
    assert ":green[within the 1.2% limit]" in [m.value for m in app.markdown]
    # Edit an input inside "Show details": the panel follows it, nothing is typed in.
    app.number_input(key=BASELINE_KEY).set_value(500.0)
    tiles = panel(app.run())
    assert tiles["Revenue per quarter"] == "+$17.50M"


def test_over_the_guardrail_the_panel_and_cards_say_so(run_app) -> None:
    app = run_app()
    for rate in (0.03, 0.05):
        app.number_input(key=churn_key(rate)).set_value(1.3)
    app = app.run()
    tiles = panel(app)
    assert tiles["Recommendation"] == "Do not proceed"
    assert tiles["Revenue per quarter"] == "—"
    badges = [m.value for m in app.markdown if "-badge[" in m.value]
    assert any("Over the 1.20% limit" in b for b in badges)  # tab 4 shows inputs exactly


def test_an_unanswered_question_leaves_the_figures_empty(run_app) -> None:
    app = run_app()
    app.text_input(key=QUESTION_KEY).set_value(OFF_TOPIC)
    tiles = panel(app.run())
    assert tiles["Recommendation"] == "Not enough evidence"
    assert tiles["Revenue per quarter"] == "—" and tiles["Past decisions"] == "0"


def test_past_decisions_carry_outcome_badges(run_app) -> None:
    badges = [m.value for m in run_app().markdown if "-badge[" in m.value]
    for label in ("Worked", "Mixed result", "Failed"):
        assert any(label in b for b in badges), label


def test_the_panel_shows_the_analyst_nothing_hidden(run_app) -> None:
    app = run_app(role="analyst")
    on_screen = rendered_text(app) + "\n".join(f"{m.label} {m.value}" for m in app.metric)
    for hidden in HIDDEN_FROM_ANALYST:
        assert hidden not in on_screen, hidden


def test_steps_are_ticked_once_passed_with_their_checks_green(run_app) -> None:
    from decision_memory.ui.guide import STEP_KEY

    from test_ui_guide import next_step

    app = run_app()
    assert not [o for o in app.radio(key=STEP_KEY).options if o.startswith("✓")]  # at start
    app = next_step(app)  # to Capture: the Hook is passed
    assert app.radio(key=STEP_KEY).options[0] == "✓ 1 · Hook"
    app = next_step(next_step(app))  # past Capture (nothing captured yet) and Ask
    options = app.radio(key=STEP_KEY).options
    assert options[1] == "2 · Capture"  # passed, but its check is not green
    assert options[2] == "✓ 3 · Ask"
    assert options[3] == "4 · Analyze"  # the current step is never ticked
