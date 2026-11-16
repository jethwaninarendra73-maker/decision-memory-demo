"""Governance tab in the running app: approve, track, record a KPI, close, and see the
outcome in memory; role limits and refusals."""

from __future__ import annotations

import pytest

from decision_memory.agents.governance_agent import GOVERNANCE_NOTE, GovernanceError
from decision_memory.models import Role
from decision_memory.repository import Repository
from decision_memory.ui.governance import (
    ACTOR_KEY,
    APPROVE_KEY,
    MARKETS_KEY,
    NO_RECORDS,
    REJECT_KEY,
    close_key,
    kpi_key,
    verdict_key,
)
from decision_memory.ui.impact import EXCLUDE_HVC_KEY
from decision_memory.ui.search import QUESTION_KEY

from conftest import rendered_text
from test_governance_agent import restricted_approval
from test_ui_memory_graph import as_tables, graph_metrics, graph_tables, show_all_kinds

OFF_TOPIC = "Should we migrate the payroll system to a new vendor?"


def ready_to_approve(run_app, role: str | None = None):
    app = run_app(role=role)
    app.checkbox(key=EXCLUDE_HVC_KEY).check()
    app.text_input(key=ACTOR_KEY).set_value("Dana Example")
    app = app.run()
    app.multiselect(key=MARKETS_KEY).set_value(["France", "Italy"])
    return app.run()


def approved(run_app):
    app = ready_to_approve(run_app)
    app.button(key=APPROVE_KEY).click()
    return app.run()


def table(app, column: str):
    (found,) = [f.value for f in app.dataframe if column in f.value.columns]
    return found


def test_the_tab_explains_itself_and_starts_with_no_records(run_app) -> None:
    app = run_app()
    assert not app.exception
    text = rendered_text(app)
    assert "Agent 6 · Governance" in [s.value for s in app.subheader]
    assert GOVERNANCE_NOTE in text and NO_RECORDS in text


def test_approval_is_blocked_until_the_exclusion_is_ticked(run_app) -> None:
    app = run_app()
    app.text_input(key=ACTOR_KEY).set_value("Dana Example")
    app = app.run()
    app.multiselect(key=MARKETS_KEY).set_value(["France", "Italy"])
    app = app.run()
    assert any(w.value.startswith("Approval is blocked") for w in app.warning)
    assert app.button(key=APPROVE_KEY).disabled
    assert not app.button(key=REJECT_KEY).disabled  # rejecting needs no exclusion


def test_the_weekly_plan_is_shown_before_approving(run_app) -> None:
    app = ready_to_approve(run_app)
    assert not app.button(key=APPROVE_KEY).disabled
    assert (
        "Weekly plan if approved: planned weekly revenue change = quarterly revenue change × "
        "pilot share of baseline ÷ 13 weeks = +$14.00M × 20% ÷ 13 = +$0.215M"
    ) in rendered_text(app)


def test_approving_opens_the_tracker_and_puts_the_decision_in_memory(run_app) -> None:
    app = approved(run_app)
    assert not app.exception and not app.error
    assert any(s.value.startswith("Simulated approval recorded: PRO-001") for s in app.success)
    text = rendered_text(app)
    assert "This question already has an open pilot: PRO-001 (approved)" in text
    assert list(table(app, "Milestone")["Milestone"]) == [
        "Live: week 1 KPI recorded",
        "Week 4 KPI checkpoint",
        "Week 8 KPI checkpoint",
        "60-day executive review",
    ]
    kpis = table(app, "Planned revenue change")
    assert len(kpis) == 8 and set(kpis["Planned revenue change"]) == {"+$0.215M"}
    assert set(kpis["Actual revenue change"]) == {"open"}
    # Tab 2 already lists the new decision as a precedent, citing the approval record.
    assert "DEC-017" in list(table(app, "Returned")["Decision"])
    assert "[\\[SRC-023\\]]" in text


def test_kpi_then_close_writes_the_outcome_into_memory(run_app) -> None:
    before = graph_metrics(run_app())
    app = approved(run_app)
    app.number_input(key=kpi_key("revenue", "PRO-001", 1)).set_value(0.22)
    app.button(key=kpi_key("record", "PRO-001", 1)).click()
    app = app.run()
    assert any(s.value == "Week 1 KPI recorded for PRO-001. On plan." for s in app.success)
    assert table(app, "Planned revenue change")["Actual revenue change"][0] == "+$0.220M"
    assert "Rule suggests **success**" in rendered_text(app)

    app.button(key=close_key("submit", "PRO-001")).click()
    app = app.run()
    assert not app.exception and not app.error
    assert any(s.value.startswith("Pilot PRO-001 closed. Outcome OUT-015") for s in app.success)
    states = list(table(app, "Milestone")["State"])
    assert states == ["done", "not reached (closed early)", "not reached (closed early)", "done"]
    assert list(table(app, "Outcome")["Outcome"]) == ["OUT-015 (success)"]
    assert "[\\[SRC-024\\]]" in rendered_text(app)
    # Search and graph read the new outcome on this rerun.
    assert "DEC-017" in list(table(app, "Returned")["Decision"])
    after = graph_metrics(app)
    assert int(after["Decisions"]) == int(before["Decisions"]) + 1
    assert int(after["Outcomes"]) == int(before["Outcomes"]) + 1
    tables = graph_tables(show_all_kinds(as_tables(app)))
    assert "OUT-015" in tables and "RESULTED_IN" in tables and "SRC-024" in tables


def test_rejecting_records_no_pilot(run_app) -> None:
    app = ready_to_approve(run_app)
    app.button(key=REJECT_KEY).click()
    app = app.run()
    assert any(s.value.startswith("Simulated rejection recorded: PRO-001") for s in app.success)
    assert list(table(app, "Outcome")["Status"]) == ["rejected"]
    assert "Rejected: no pilot, decision or tracker was created." in rendered_text(app)
    assert not [f for f in app.dataframe if "Milestone" in f.value.columns]


def test_the_analyst_cannot_approve_or_close_but_can_record_kpis(run_app) -> None:
    app = approved(run_app)
    app.selectbox(key="demo_role").set_value(Role.ANALYST.value)
    app.text_input(key=ACTOR_KEY).set_value("Sam Example")
    app = app.run()
    assert "This question already has an open pilot" in rendered_text(app)
    app.button(key=kpi_key("record", "PRO-001", 1)).click()
    app = app.run()
    assert any(s.value.startswith("Week 1 KPI recorded") for s in app.success)
    assert "The Analyst demo role cannot hold the executive review" in rendered_text(app)
    assert not [b for b in app.button if b.key == close_key("submit", "PRO-001")]


def test_the_analyst_sees_why_there_is_no_approval_form(run_app) -> None:
    app = run_app(role="analyst")
    assert "The Analyst demo role cannot approve or reject" in rendered_text(app)
    assert not [b for b in app.button if b.key == APPROVE_KEY]


def test_nothing_to_approve_without_a_rate(run_app) -> None:
    app = run_app()
    app.text_input(key=QUESTION_KEY).set_value(OFF_TOPIC)
    app = app.run()
    assert "Nothing to approve" in rendered_text(app)
    assert not [b for b in app.button if b.key == APPROVE_KEY]


def test_buttons_wait_for_a_name(run_app) -> None:
    app = run_app()
    app.checkbox(key=EXCLUDE_HVC_KEY).check()
    app = app.run()
    app.multiselect(key=MARKETS_KEY).set_value(["France", "Italy"])
    app = app.run()
    assert app.button(key=APPROVE_KEY).disabled and app.button(key=REJECT_KEY).disabled


def test_a_refused_approval_shows_the_reason_and_stores_nothing(
    run_app, monkeypatch: pytest.MonkeyPatch, seeded_db
) -> None:
    def refuse(*args, **kwargs):
        raise GovernanceError("Refused for this test.")

    monkeypatch.setattr("decision_memory.ui.governance.approve_recommendation", refuse)
    app = ready_to_approve(run_app)
    app.button(key=APPROVE_KEY).click()
    app = app.run()
    assert not app.exception
    assert [e.value for e in app.error] == ["Refused for this test."]
    with Repository.open(seeded_db) as repo:
        assert repo.counts()["proposals"] == 0


@pytest.mark.parametrize("role", ["pricing_director", "analyst"])
def test_a_restricted_pilot_never_reaches_the_screen(run_app, seeded_db, role: str) -> None:
    with Repository.open(seeded_db) as repo:
        record = restricted_approval(repo)
    app = run_app(role=role)
    assert not app.exception
    text = rendered_text(app)
    for secret in (record.proposal.id, record.decision.id, "SRC-023", "Simulated approval record"):
        assert secret not in text
    assert NO_RECORDS in text


def test_the_verdict_selector_follows_the_latest_suggestion(run_app) -> None:
    app = approved(run_app)
    app.button(key=kpi_key("record", "PRO-001", 1)).click()
    app = app.run()
    assert app.selectbox(key=verdict_key("PRO-001", 1)).value == "success"
    app.number_input(key=kpi_key("revenue", "PRO-001", 2)).set_value(0.05)  # below plan
    app.button(key=kpi_key("record", "PRO-001", 2)).click()
    app = app.run()
    assert "Rule suggests **partial**" in rendered_text(app)
    assert app.selectbox(key=verdict_key("PRO-001", 2)).value == "partial"
    app.button(key=close_key("submit", "PRO-001")).click()
    app = app.run()
    assert not app.error  # closing with the suggested verdict needs no note
    assert any(
        s.value.startswith("Pilot PRO-001 closed. Outcome OUT-015 (partial)") for s in app.success
    )
