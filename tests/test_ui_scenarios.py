"""Scenarios tab in the running app: computed figures, editable inputs, formulas, sources."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from decision_memory.agents.impact_agent import HVC_EXCLUSION, draft_proposal
from decision_memory.agents.scenario_agent import DEFAULT_INPUTS, simulate_scenarios
from decision_memory.repository import Repository
from decision_memory.ui.impact import EXCLUDE_HVC_KEY
from decision_memory.ui.scenarios import (
    BASELINE_KEY,
    EXEMPT_KEY,
    EXEMPT_MATCH,
    EXEMPT_MISMATCH,
    EXEMPT_ZERO,
    RESET_KEY,
    SLA_NOTE,
    build_chart,
    churn_key,
    exclusion_check,
    pct,
)
from decision_memory.ui.search import QUESTION_KEY

from conftest import HIDDEN_FROM_ANALYST, HIDDEN_FROM_DIRECTOR, rendered_text

DEFAULT_REVENUE = ["+$8.40M", "+$14.00M", "+$18.80M"]
DEFAULT_MARGIN = ["+$5.58M", "+$9.50M", "+$12.86M"]


def metric_values(app, label: str) -> list[str]:
    return [m.value for m in app.metric if m.label == label]


def scenario_table(app):
    (table,) = [f.value for f in app.dataframe if "Revenue change" in f.value.columns]
    return table


def test_default_figures_formulas_and_labels(run_app) -> None:
    app = run_app()
    assert not app.exception and not app.error
    assert metric_values(app, "Revenue change") == DEFAULT_REVENUE
    assert metric_values(app, "Margin change") == DEFAULT_MARGIN
    text = rendered_text(app)
    assert "revenue = 400.00 × 5% × (1 − 10%) − 400.00 × 1% = 18.00 − 4.00 = 14.00;" in text
    assert any("revenue_change = baseline × rate" in c.value for c in app.code)
    table = scenario_table(app)
    assert list(table["Option"]) == ["+3%", "+5%", "+7%"]
    assert list(table["vs 1.20% guardrail"]) == ["within", "within", "exceeds"]
    assert list(table["Revenue retention proxy"]) == ["99.40%", "99.00%", "98.40%"]
    assert set(table["SLA"]) == {"unchanged / not modeled"}
    assert SLA_NOTE in text and "A labelled proxy" in text


def test_editing_an_input_recomputes_and_marks_it_edited(run_app) -> None:
    app = run_app()
    app.number_input(key=EXEMPT_KEY).set_value(0.0)
    app = app.run()
    assert metric_values(app, "Revenue change")[1] == "+$16.00M"  # 400 × 5% − 4
    assert metric_values(app, "Margin change")[1] == "+$10.90M"
    assert "**Exempt share of revenue**: 0% · Assumed for demo · edited" in rendered_text(app)
    app.number_input(key=churn_key(0.07)).set_value(1.0)
    app = app.run()
    assert list(scenario_table(app)["vs 1.20% guardrail"]) == ["within", "within", "within"]
    app.button(key=RESET_KEY).click()
    app = app.run()
    assert metric_values(app, "Revenue change") == DEFAULT_REVENUE


def test_sources_of_the_defaults_are_cited(run_app) -> None:
    text = rendered_text(run_app())
    assert "**Exempt share of revenue**: 10% · Source-supported" in text
    assert "- Source: [\\[SRC-022\\]](?role=pricing_director&evidence=SRC-022)" in text
    assert "- Source: [\\[SRC-014\\]]" in text
    assert "**Churn loss at +7% (share of baseline)**: 1.6% · Assumed for demo" in text
    assert "- Context: [\\[SRC-010\\]]" in text
    assert "**Baseline revenue (USD M per quarter)**: $400.00M · Assumed for demo" in text


def test_exempt_share_is_checked_against_the_draft_proposal(run_app) -> None:
    app = run_app()
    assert EXEMPT_MISMATCH in [i.value for i in app.info]
    app.checkbox(key=EXCLUDE_HVC_KEY).check()
    app = app.run()
    assert EXEMPT_MISMATCH not in [i.value for i in app.info]
    assert EXEMPT_MATCH in rendered_text(app)


def test_an_asked_rate_gets_its_own_interpolated_option(run_app) -> None:
    app = run_app()
    app.text_input(key=QUESTION_KEY).set_value(
        "Should we increase the freight surcharge by 4% for European customers?"
    )
    app = app.run()
    assert not app.exception
    assert list(scenario_table(app)["Option"]) == ["+3%", "+4%", "+5%", "+7%"]
    assert metric_values(app, "Revenue change")[1] == "+$11.20M"  # 400 × 4% × 90% − 400 × 0.8%
    assert "**Churn loss at +4% (share of baseline)**: 0.8% · Assumed for demo" in (
        rendered_text(app)
    )


def test_empty_memory_still_computes_with_every_input_assumed(run_app, tmp_path) -> None:
    empty = tmp_path / "empty.db"
    Repository.open(empty).close()
    app = run_app(db=empty)
    assert not app.exception
    assert metric_values(app, "Revenue change") == DEFAULT_REVENUE
    assert "Source-supported" not in rendered_text(app)


@pytest.mark.parametrize(
    ("role", "hidden"),
    [("pricing_director", HIDDEN_FROM_DIRECTOR), ("analyst", HIDDEN_FROM_ANALYST)],
)
def test_scenarios_tab_shows_nothing_hidden(run_app, role: str, hidden) -> None:
    on_screen = rendered_text(run_app(role=role))
    for secret in hidden:
        assert secret not in on_screen


# --- review fixes -------------------------------------------------------------------


def test_inputs_are_shown_exactly_as_applied(run_app) -> None:
    # Regression: 0.125% was applied as 0.125% but shown as 0.13 in its box and 0.12% in
    # the table.
    app = run_app()
    app.number_input(key=churn_key(0.05)).set_value(0.125)
    app = app.run()
    assert app.number_input(key=churn_key(0.05)).value == 0.125
    assert scenario_table(app)["Churn loss (share of baseline)"][1] == "0.125%"
    assert metric_values(app, "Revenue change")[1] == "+$17.50M"  # 18 − 400 × 0.125%
    # More decimals than the box shows are rounded to what it shows, then applied.
    app.number_input(key=churn_key(0.05)).set_value(0.1234)
    app = app.run()
    assert app.number_input(key=churn_key(0.05)).value == 0.123
    assert scenario_table(app)["Churn loss (share of baseline)"][1] == "0.123%"
    assert metric_values(app, "Revenue change")[1] == "+$17.51M"  # 18 − 400 × 0.123%
    app.number_input(key=BASELINE_KEY).set_value(400.126)
    app = app.run()
    assert app.number_input(key=BASELINE_KEY).value == 400.13
    assert "revenue = 400.13 × 3%" in rendered_text(app)


@pytest.mark.parametrize(
    ("value", "shown"),
    [(0.012, "1.20%"), (0.00125, "0.125%"), (0.99875, "99.875%"), (0.1, "10.00%")],
)
def test_percentages_show_two_to_three_decimals(value: float, shown: str) -> None:
    assert pct(value) == shown


def test_exclusion_check_works_both_ways(run_app) -> None:
    proposal = draft_proposal("q", "Europe", date(2026, 11, 14), datetime(2026, 11, 14, tzinfo=UTC))
    excluded = proposal.model_copy(update={"exclusions": [HVC_EXCLUSION]})
    no_exempt = DEFAULT_INPUTS.model_copy(update={"exempt_share": 0.0})
    assert exclusion_check(proposal, DEFAULT_INPUTS) == EXEMPT_MISMATCH
    assert exclusion_check(excluded, DEFAULT_INPUTS) is None
    assert exclusion_check(excluded, no_exempt) == EXEMPT_ZERO  # regression: was silent
    assert exclusion_check(proposal, no_exempt) is None
    app = run_app()
    app.checkbox(key=EXCLUDE_HVC_KEY).check()
    app.number_input(key=EXEMPT_KEY).set_value(0.0)
    app = app.run()
    assert EXEMPT_ZERO in [i.value for i in app.info]


def test_chart_draws_the_computed_figures(run_app) -> None:
    results = simulate_scenarios(DEFAULT_INPUTS, "PRO-DRAFT", datetime(2026, 11, 14, tzinfo=UTC))
    figure = build_chart(results)
    assert [trace.name for trace in figure.data] == ["Revenue change", "Margin change"]
    assert list(figure.data[0].y) == [r.revenue_change_musd for r in results]
    assert list(figure.data[1].y) == [r.margin_change_musd for r in results]
    app = run_app()
    (spec,) = [
        json.loads(c.proto.spec)
        for c in app.get("plotly_chart")
        if "Revenue change" in c.proto.spec
    ]
    assert spec["data"][0]["y"] == pytest.approx([8.4, 14.0, 18.8])
    assert spec["data"][1]["y"] == pytest.approx([5.58, 9.5, 12.86])
