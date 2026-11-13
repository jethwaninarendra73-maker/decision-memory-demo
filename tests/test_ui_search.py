"""Precedent search in the running app: results, abstention, filters and graph highlights."""

from __future__ import annotations

import json
from datetime import date

import pytest

from decision_memory.agents.memory_agent import build_decision_graph, layout, neighbourhoods
from decision_memory.authz import MemoryView
from decision_memory.models import NodeKind, Role
from decision_memory.repository import Repository
from decision_memory.ui.memory_graph import (
    FOCUS_KEY,
    HIGHLIGHT_NAME,
    LESSON_NAME,
    MODE_KEY,
    PRECEDENTS,
    build_figure,
    node_rows,
)
from decision_memory.ui.search import (
    AS_OF_KEY,
    LESSONS_HEADING,
    MIN_LEXICAL_KEY,
    MIN_SCORE_KEY,
    NOTHING_TO_SEARCH,
    QUESTION_KEY,
    RATE_KEY,
    REGION_KEY,
    USE_RATE_KEY,
)

from conftest import HIDDEN_FROM_ANALYST, HIDDEN_FROM_DIRECTOR, rendered_text

AS_OF = date(2026, 11, 13)
DEMO_TOP = ["DEC-009", "DEC-007", "DEC-004", "DEC-011", "DEC-001"]
BOARD_QUESTION = "Board strategy: hold fixed pricing for the twenty largest strategic accounts?"


def at_fixed_date(app):
    app.date_input(key=AS_OF_KEY).set_value(AS_OF)
    return app.run()


def ask(app, question: str):
    app.text_input(key=QUESTION_KEY).set_value(question)
    return app.run()


def chart_traces(app) -> list[str]:
    return [
        trace.get("name")
        for chart in app.get("plotly_chart")
        for trace in json.loads(chart.proto.spec)["data"]
    ]


def card_headers(app) -> list[str]:
    return [m.value for m in app.markdown if m.value.startswith("**") and "· score" in m.value]


def precedent_headers(app) -> list[str]:
    return [h for h in card_headers(app) if not h.startswith("**Lesson")]


def lesson_headers(app) -> list[str]:
    return [h for h in card_headers(app) if h.startswith("**Lesson")]


def test_demo_question_lists_cited_precedents_and_highlights_them(run_app) -> None:
    app = at_fixed_date(run_app())
    assert not app.exception and not app.error
    headers = precedent_headers(app)
    assert [h.split(" · ")[0].split(". ")[1] for h in headers] == DEMO_TOP
    text = rendered_text(app)
    assert "5 precedent(s) above the 0.45 threshold." in text
    assert "Failed precedent" in text  # DEC-001 is shown as a warning
    assert "[\\[SRC-006\\]](?role=pricing_director&evidence=SRC-006)" in text  # France citation
    assert "Lexical overlap" in "\n".join(f.value.to_string() for f in app.dataframe)
    assert "Highlighted search precedents: " + ", ".join(DEMO_TOP) in text
    assert HIGHLIGHT_NAME in chart_traces(app)


def test_off_topic_question_abstains_and_highlights_nothing(run_app) -> None:
    app = ask(run_app(), "Should we migrate the payroll system to a new vendor?")
    assert not app.exception
    assert any(w.value.startswith("Insufficient verifiable evidence.") for w in app.warning)
    assert not precedent_headers(app)
    assert "Highlighted search precedents" not in rendered_text(app)
    assert HIGHLIGHT_NAME not in chart_traces(app)


def test_filters_follow_the_question_and_can_be_edited(run_app) -> None:
    app = run_app()
    assert app.selectbox(key=REGION_KEY).value == "Europe"
    assert app.number_input(key=RATE_KEY).value == 5.0 and app.checkbox(key=USE_RATE_KEY).value
    app = ask(app, "What happened when we copied a 5% surcharge to APAC?")
    assert app.selectbox(key=REGION_KEY).value == "Asia-Pacific"
    assert precedent_headers(app)[0].startswith("**1. DEC-014")
    app.selectbox(key=REGION_KEY).set_value("Any")
    app = app.run()
    assert "any region" in rendered_text(app)


@pytest.mark.parametrize(
    ("role", "hidden"), [("pricing_director", HIDDEN_FROM_DIRECTOR), ("analyst", HIDDEN_FROM_ANALYST)]
)
def test_search_view_shows_nothing_hidden(run_app, role: str, hidden) -> None:
    app = at_fixed_date(run_app(role=role))
    tables = "\n".join(f.value.to_string() for f in app.dataframe)
    on_screen = "\n".join([rendered_text(app), tables, *map(str, chart_traces(app))])
    for secret in hidden:
        assert secret not in on_screen


def test_hidden_decision_is_not_found_by_a_role_that_cannot_see_it(run_app) -> None:
    director = ask(run_app(role="pricing_director"), BOARD_QUESTION)
    assert any(w.value.startswith("Insufficient verifiable evidence.") for w in director.warning)
    assert "DEC-015" not in rendered_text(director)
    exec_app = ask(run_app(role="restricted_exec"), BOARD_QUESTION)
    assert precedent_headers(exec_app)[0].startswith("**1. DEC-015")


def test_graph_can_focus_on_the_search_precedents(run_app) -> None:
    app = at_fixed_date(run_app())
    app.selectbox(key=FOCUS_KEY).set_value(PRECEDENTS)
    app.radio(key=MODE_KEY).set_value("Tables")
    app = app.run()
    (nodes,) = [f.value for f in app.dataframe if "Precedent rank" in f.value.columns]
    ranks = dict(zip(nodes["Node"], nodes["Precedent rank"], strict=True))
    assert [ranks[d] for d in DEMO_TOP] == ["1", "2", "3", "4", "5"]
    assert "DEC-013" not in ranks  # not within one hop of any precedent


def test_empty_memory_has_nothing_to_search(run_app, tmp_path) -> None:
    empty = tmp_path / "empty.db"
    Repository.open(empty).close()
    app = run_app(db=empty)
    assert not app.exception
    assert NOTHING_TO_SEARCH in rendered_text(app)


# --- graph helpers used by the highlight ------------------------------------


def test_highlight_ring_only_for_precedents_in_the_drawn_graph(seeded: Repository) -> None:
    graph = build_decision_graph(MemoryView(seeded, Role.ANALYST))
    figure = build_figure(graph, layout(graph), ["DEC-004", "DEC-999"])
    (ring,) = [trace for trace in figure.data if trace.name == HIGHLIGHT_NAME]
    assert len(ring.x) == 1
    assert HIGHLIGHT_NAME not in [t.name for t in build_figure(graph, layout(graph)).data]
    rows = {row["Node"]: row["Precedent rank"] for row in node_rows(graph, ["DEC-004"])}
    assert rows["DEC-004"] == "1" and rows["DEC-001"] == ""


def test_neighbourhoods_is_the_union_of_one_hop_neighbourhoods(seeded: Repository) -> None:
    graph = build_decision_graph(MemoryView(seeded, Role.PRICING_DIRECTOR))
    union = neighbourhoods(graph, ["DEC-004", "DEC-012", "DEC-999"])
    ids = set(union.node_ids(NodeKind.DECISION))
    assert {"DEC-004", "DEC-007", "DEC-012"} <= ids
    assert neighbourhoods(graph, []).nodes == []


# --- wording, lessons and thresholds ----------------------------------------


def test_demo_question_shows_the_other_region_lesson_apart(run_app) -> None:
    app = at_fixed_date(run_app())
    assert [h.split(" · ")[0] for h in lesson_headers(app)] == ["**Lesson 1. DEC-014"]
    text = rendered_text(app)
    assert f"##### {LESSONS_HEADING}" in text
    assert "not precedents for Europe" in text
    assert "Highlighted lessons from other regions: DEC-014" in text
    assert LESSON_NAME in chart_traces(app) and HIGHLIGHT_NAME in chart_traces(app)


def test_reworded_question_lists_the_equivalences_it_used(run_app) -> None:
    app = ask(at_fixed_date(run_app()), "Is a 5% price increase for EU shippers a good idea?")
    text = rendered_text(app)
    assert "Equivalences applied: price increase → surcharge increase; eu → europe" in text
    assert precedent_headers(app)  # it finds precedents instead of abstaining


def test_threshold_sliders_change_the_answer(run_app) -> None:
    app = at_fixed_date(run_app())
    assert app.slider(key=MIN_LEXICAL_KEY).value == 0.5
    assert app.slider(key=MIN_SCORE_KEY).value == 0.45
    app.slider(key=MIN_LEXICAL_KEY).set_value(0.95)
    app = app.run()
    assert not precedent_headers(app)
    assert any(w.value.startswith("Insufficient verifiable evidence.") for w in app.warning)
    assert "lexical ≥ 0.95" in rendered_text(app)


def test_lesson_rows_in_the_graph_table(seeded: Repository) -> None:
    graph = build_decision_graph(MemoryView(seeded, Role.ANALYST))
    rows = {r["Node"]: r["Precedent rank"] for r in node_rows(graph, ["DEC-004"], ["DEC-014"])}
    assert rows["DEC-004"] == "1" and rows["DEC-014"] == "lesson 1"
    figure = build_figure(graph, layout(graph), ["DEC-004"], ["DEC-014"])
    assert [t.name for t in figure.data[:2]] == [HIGHLIGHT_NAME, LESSON_NAME]


def test_slider_half_steps_are_shown_exactly(run_app) -> None:
    app = at_fixed_date(run_app())
    app.slider(key=MIN_LEXICAL_KEY).set_value(0.475)
    app.slider(key=MIN_SCORE_KEY).set_value(0.425)
    app = app.run()
    text = rendered_text(app)
    assert "A decision needs lexical ≥ 0.475 and score ≥ 0.425" in text
    assert "above the 0.425 threshold." in text
