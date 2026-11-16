"""The guided demo in the running app: banners on every tab, step navigation that opens
the right tab, live checks, and a scripted run through all six steps."""

from __future__ import annotations

import pytest

from decision_memory import SYNTHETIC_BANNER
from decision_memory.agents import AGENTS
from decision_memory.ui.evidence import SELECT_KEY as EVIDENCE_KEY
from decision_memory.ui.governance import ACTOR_KEY, APPROVE_KEY, MARKETS_KEY, close_key, kpi_key
from decision_memory.ui.guide import GUIDE_ON_KEY, STEP_KEY, TAB_KEY
from decision_memory.ui.impact import EXCLUDE_HVC_KEY
from decision_memory.ui.scenarios import BASELINE_KEY, churn_key
from decision_memory.ui.search import QUESTION_KEY

from conftest import HIDDEN_FROM_ANALYST, HIDDEN_FROM_DIRECTOR, rendered_text
from test_ui_governance import approved

LABELS = [agent.label for agent in AGENTS]


def guide_text(app) -> str:
    return "\n".join(m.value for m in app.markdown) + "\n".join(i.value for i in app.info)


def checks(app) -> str:
    (block,) = [m.value for m in app.markdown if m.value.startswith("**Live checks**")]
    return block.replace("\\", "")


def next_step(app):
    return app.button(key="guide_next").click().run()


def go_to(app, index: int):
    app.radio(key=STEP_KEY).set_value(index)
    return app.run()


# --- banners -------------------------------------------------------------------------


def test_every_tab_starts_with_the_synthetic_data_banner(run_app) -> None:
    app = run_app()
    assert SYNTHETIC_BANNER == "SYNTHETIC DATA — NOT A PREDICTION OR AUTHORIZED BUSINESS DECISION"
    assert any(SYNTHETIC_BANNER in w.value for w in app.warning)  # above everything
    assert len(app.tabs) == 6
    for tab in app.tabs:
        assert tab.caption[0].value == f"⚠️ **{SYNTHETIC_BANNER}**", tab.label


# --- the panel -------------------------------------------------------------------------


def test_the_guide_opens_on_the_hook_with_three_quoted_artifacts(run_app) -> None:
    app = run_app()
    assert not app.exception
    assert app.toggle(key=GUIDE_ON_KEY).value
    assert app.radio(key=STEP_KEY).value == 0
    text = guide_text(app)
    assert "**Step 1 of 6 · Hook** (0:00–0:30, on this panel)" in text
    for source_id in ("SRC-006", "SRC-014", "SRC-002"):
        assert f"**{source_id}** · " in text
    # Each artifact quotes the line memory cites from it, shown literally.
    quoted = [t.value for t in app.text]
    assert "Apply a 5% freight surcharge in France from 1 February 2024" in "".join(quoted)
    assert any(q.startswith("Adopt a churn guardrail for all European surcharges") for q in quoted)
    assert any(q.startswith("Eleven high-volume contract customers have disputed") for q in quoted)
    app.button(key="guide_read_SRC-014").click()
    app = app.run()
    assert app.selectbox(key=EVIDENCE_KEY).value == "SRC-014"  # open in the explorer below


def test_next_and_back_open_each_steps_tab(run_app) -> None:
    app = run_app()
    expected = [None, LABELS[0], LABELS[1], LABELS[2], LABELS[4], LABELS[5]]
    for index in range(1, 6):
        app = next_step(app)
        assert app.radio(key=STEP_KEY).value == index
        assert app.session_state[TAB_KEY] == expected[index]
    assert app.button(key="guide_next").disabled
    app = app.button(key="guide_back").click().run()
    assert (app.radio(key=STEP_KEY).value, app.session_state[TAB_KEY]) == (4, LABELS[4])


def test_picking_a_step_opens_its_tab_and_extra_tabs_have_buttons(run_app) -> None:
    app = go_to(run_app(), 3)  # Analyze: tab 3, then tab 4
    assert app.session_state[TAB_KEY] == LABELS[2]
    assert "(2:15–3:30, in tab 3 · Impact Analysis then tab 4 · Scenario Simulation)" in (
        guide_text(app).replace("\\", "")
    )
    app = app.button(key="guide_open_scenario").click().run()
    assert app.session_state[TAB_KEY] == LABELS[3]


def test_the_guide_can_be_turned_off(run_app) -> None:
    app = run_app()
    app.toggle(key=GUIDE_ON_KEY).set_value(False)
    app = app.run()
    assert not app.exception
    assert "Guided demo · six steps" not in guide_text(app)
    assert not [b for b in app.button if b.key == "guide_next"]


def test_live_checks_and_talk_track_follow_the_app(run_app) -> None:
    app = go_to(run_app(), 2)
    found = checks(app)
    assert "✅ Precedents returned: DEC-009, DEC-007, DEC-004, DEC-011, DEC-001." in found
    assert "✅ Shown as warnings (failed): DEC-001." in found
    assert "✅ Lessons from other regions: DEC-014." in found

    app = go_to(app, 3)
    assert "⬜ High-volume contract customers are not excluded yet (tab 3)." in checks(app)
    talk = " ".join(i.value for i in app.info).replace("\\", "")
    assert "Where does +$14.00M come from? $400.00M × 5% × 90% not exempt = +$18.00M" in talk
    app.checkbox(key=EXCLUDE_HVC_KEY).check()
    app.number_input(key=BASELINE_KEY).set_value(500.0)
    app = app.run()
    assert "✅ High-volume contract customers are excluded" in checks(app)
    talk = " ".join(i.value for i in app.info).replace("\\", "")
    assert "Where does +$17.50M come from? $500.00M × 5%" in talk  # computed, not typed in

    app = go_to(app, 4)
    assert "✅ Recommendation on screen: Phased pilot at +5%; 2 key risk(s) cited." in checks(app)


@pytest.mark.parametrize(
    ("role", "hidden"),
    [("pricing_director", HIDDEN_FROM_DIRECTOR), ("analyst", HIDDEN_FROM_ANALYST)],
)
def test_the_guide_shows_nothing_hidden(run_app, role: str, hidden) -> None:
    app = run_app(role=role)
    for index in range(6):
        app = go_to(app, index)
        on_screen = rendered_text(app)
        for secret in hidden:
            assert secret not in on_screen


# --- the whole demo, scripted ----------------------------------------------------------


def test_a_scripted_run_through_all_six_steps(run_app) -> None:
    app = run_app()
    # 1 Hook: three artifacts.  2 Capture: extract and confirm the demo transcript.
    app = next_step(app)
    app = app.button(key="capture_extract").click().run()
    (confirm,) = [b for b in app.button if b.label == "Confirm and save to memory"]
    app = confirm.click().run()
    assert "✅ Captured in the app and confirmed: DEC-017." in checks(app)
    # 3 Ask: the question is filled in and returns cited precedents.
    app = next_step(app)
    assert "✅ Precedents returned" in checks(app)
    # 4 Analyze: exclude high-volume contract customers.
    app = next_step(app)
    app.checkbox(key=EXCLUDE_HVC_KEY).check()
    app = app.run()
    assert "✅ High-volume contract customers are excluded" in checks(app)
    # 5 Recommend.
    app = next_step(app)
    assert "✅ Recommendation on screen: Phased pilot at +5%" in checks(app)
    # 6 Close the loop: approve, record week 1, close.
    app = next_step(app)
    assert app.session_state[TAB_KEY] == LABELS[5]
    app.text_input(key=ACTOR_KEY).set_value("Dana Example")
    app = app.run()
    app.multiselect(key=MARKETS_KEY).set_value(["France", "Italy"])
    app = app.run()
    app = app.button(key=APPROVE_KEY).click().run()
    pilot = "PRO-001"
    assert "✅ Pilot PRO-001 is approved." in checks(app)
    app = app.button(key=kpi_key("record", pilot, 1)).click().run()
    app = app.button(key=close_key("submit", pilot)).click().run()
    assert not app.exception and not app.error
    found = checks(app)
    assert "✅ Pilot PRO-001 is completed." in found
    assert "✅ Outcome OUT-015 is in memory (tab 2)." in found
    # The captured decision is DEC-017, so the approved pilot became DEC-018.
    assert (
        "DEC-018"
        in next(row for row in app.dataframe if "Decision in memory" in row.value.columns)
        .value["Decision in memory"]
        .tolist()
    )


# --- review regressions ---------------------------------------------------------------

OTHER_QUESTION = "Should we raise the freight surcharge by 3% for German non-contract customers?"


def test_step_6_checks_only_the_pilot_for_the_question_on_screen(run_app) -> None:
    app = go_to(approved(run_app), 5)
    assert "✅ Pilot PRO-001 is approved." in checks(app)
    app.text_input(key=QUESTION_KEY).set_value(OTHER_QUESTION)
    app = app.run()
    assert "⬜ No pilot approved for this question yet." in checks(app)
    assert "PRO-001" not in checks(app)


def test_hiding_the_guide_keeps_its_step(run_app) -> None:
    app = go_to(run_app(), 3)
    app.toggle(key=GUIDE_ON_KEY).set_value(False)
    app = app.run()
    app.toggle(key=GUIDE_ON_KEY).set_value(True)
    app = app.run()
    assert app.radio(key=STEP_KEY).value == 3
    app = next_step(app)
    assert (app.radio(key=STEP_KEY).value, app.session_state[TAB_KEY]) == (4, LABELS[4])


def test_a_card_without_a_rate_counts_no_key_risks(run_app) -> None:
    app = run_app()
    for rate in (0.03, 0.05):
        app.number_input(key=churn_key(rate)).set_value(1.3)  # every backed option fails
    app = go_to(app.run(), 4)
    assert "⬜ Recommendation on screen: Do not proceed." in checks(app)
