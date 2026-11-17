"""The simple view: plain headings first and supporting detail under "Show details", with
the same results, the same working widgets and the same permissions as the full view."""

from __future__ import annotations

import pytest

from decision_memory.agents import AGENTS
from decision_memory.ui.guide import TAB_KEY
from decision_memory.ui.recommendation import BRIEF_CHECKS
from decision_memory.ui.scenarios import BASELINE_KEY
from decision_memory.ui.search import QUESTION_KEY
from decision_memory.ui.view import DETAILS_PREFIX, INTROS, SIMPLE_KEY

from conftest import HIDDEN_FROM_ANALYST, rendered_text
from test_ui_guide import LABELS, go_to
from test_ui_scenarios import DEFAULT_MARGIN, DEFAULT_REVENUE, metric_values


def full_view(app):
    app.toggle(key=SIMPLE_KEY).set_value(False)
    return app.run()


def details_sections(app) -> list[str]:
    return [e.label for e in app.expander if e.label.startswith(DETAILS_PREFIX)]


def test_the_simple_view_is_on_by_default_and_names_the_tabs_plainly(run_app) -> None:
    app = run_app()
    assert not app.exception and not app.error
    switch = app.sidebar.toggle(key=SIMPLE_KEY)
    assert switch.label == "Simple view" and switch.value is True
    assert [tab.label for tab in app.tabs] == [
        "1 · Capture a decision",
        "2 · Past decisions",
        "3 · Who is affected",
        "4 · Compare options",
        "5 · Recommendation",
        "6 · Approve and track",
    ]
    intros = [i.value for i in app.info]
    for agent in AGENTS:  # each tab says what it is for, in one line
        assert INTROS[agent.key] in intros, agent.key


def test_detail_moves_under_show_details_and_back(run_app) -> None:
    app = run_app()
    simple = details_sections(app)
    for expected in (
        "how it was scored, and the quoted sources",
        "the evidence for each item",
        "change the assumptions (every figure below follows them)",
        "the formulas and the working for each option",
        "the sources quoted for each reason",
        "the action plan for the approver",
    ):
        assert DETAILS_PREFIX + expected in simple, expected
    app = full_view(app)
    assert details_sections(app) == []  # the full view draws everything in place
    assert not [i.value for i in app.info if i.value in INTROS.values()]


@pytest.mark.parametrize("simple", [True, False], ids=["simple view", "full view"])
def test_both_views_show_the_same_results(run_app, simple: bool) -> None:
    app = run_app()
    if not simple:
        app = full_view(app)
    assert not app.exception and not app.error
    assert metric_values(app, "Revenue change") == DEFAULT_REVENUE
    assert metric_values(app, "Margin change") == DEFAULT_MARGIN
    headlines = [m.value for m in app.markdown if m.value.startswith("#### Advisory")]
    assert headlines == ["#### Advisory recommendation: Phased pilot at +5%"]
    text = rendered_text(app)
    for proof in ("[\\[SRC-006\\]]", "revenue = 400.00 × 5% × (1 − 10%)", "How it was scored"):
        assert proof in text, proof  # the proof is still on the page, only folded away


def test_widgets_inside_show_details_still_work(run_app) -> None:
    app = run_app()
    app.number_input(key=BASELINE_KEY).set_value(500.0)
    app = app.run()
    assert metric_values(app, "Revenue change")[1] == "+$17.50M"  # 500 × 5% × 90% − 500 × 1%


def test_switching_view_keeps_the_open_tab_and_the_question(run_app) -> None:
    app = go_to(run_app(), 3)
    app.text_input(key=QUESTION_KEY).set_value("Should we add a 5% surcharge in Europe?")
    app = full_view(app.run())
    assert app.session_state[TAB_KEY] == LABELS[2]
    assert app.text_input(key=QUESTION_KEY).value == "Should we add a 5% surcharge in Europe?"


@pytest.mark.parametrize("simple", [True, False], ids=["simple view", "full view"])
def test_neither_view_shows_the_analyst_anything_hidden(run_app, simple: bool) -> None:
    app = run_app(role="analyst")
    if not simple:
        app = full_view(app)
    on_screen = rendered_text(app) + "\n".join(details_sections(app))
    for hidden in HIDDEN_FROM_ANALYST:
        assert hidden not in on_screen, hidden


def test_the_brief_note_is_untouched_by_the_view() -> None:
    # The optional brief keeps its warning to read it against the card in both views.
    assert "read it against the card" in BRIEF_CHECKS
