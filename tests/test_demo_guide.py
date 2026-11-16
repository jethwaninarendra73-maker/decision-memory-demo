"""The guided demo's steps, live checks and talk tracks (pure, no app)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from decision_memory.agents import AGENTS
from decision_memory.agents.governance_agent import DEFAULT_RULES as GOVERNANCE_RULES
from decision_memory.agents.scenario_agent import DEFAULT_INPUTS, simulate_scenarios
from decision_memory.demo_guide import (
    HOOK_SOURCE_IDS,
    STEPS,
    DemoState,
    PilotSummary,
    checks,
    revenue_talk_track,
    talk_track,
)

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
STEP = {step.key: step for step in STEPS}


def scenario(rate: float = 0.05, **inputs):
    edited = DEFAULT_INPUTS.model_copy(update=inputs)
    return next(r for r in simulate_scenarios(edited, "PRO-DRAFT", NOW) if r.surcharge_rate == rate)


def test_six_steps_follow_the_five_minute_outline() -> None:
    assert [(s.title, s.time) for s in STEPS] == [
        ("Hook", "0:00–0:30"),
        ("Capture", "0:30–1:15"),
        ("Ask", "1:15–2:15"),
        ("Analyze", "2:15–3:30"),
        ("Recommend", "3:30–4:30"),
        ("Close the loop", "4:30–5:00"),
    ]
    keys = {agent.key for agent in AGENTS}
    assert all(set(step.tabs) <= keys for step in STEPS)
    assert [step.tabs[0] if step.tabs else None for step in STEPS] == [
        None, "capture", "memory", "impact", "recommendation", "governance",
    ]  # fmt: skip
    assert HOOK_SOURCE_IDS == ("SRC-006", "SRC-014", "SRC-002")


def test_the_revenue_talk_track_uses_the_figures_on_screen() -> None:
    assert revenue_talk_track(scenario()) == (
        "Where does +$14.00M come from? $400.00M × 5% × 90% not exempt = +$18.00M, minus "
        "$400.00M × 1% churn loss = $4.00M, gives +$14.00M revenue change a quarter, before "
        "margin."
    )
    # Nothing is hard-coded: edit an input and the explanation follows.
    edited = revenue_talk_track(scenario(baseline_revenue_musd=500.0))
    assert edited.startswith("Where does +$17.50M come from? $500.00M × 5%")
    assert revenue_talk_track(scenario(0.03)).startswith("Where does +$8.40M come from?")


def test_only_the_analyze_step_adds_figures() -> None:
    state = DemoState(scenario=scenario())
    for step in STEPS:
        lines = talk_track(step, state)
        assert lines[0] == step.say
        assert ("$" in " ".join(lines)) is (step.key == "analyze")
    assert talk_track(STEP["analyze"], DemoState()) == [STEP["analyze"].say]


@pytest.mark.parametrize(
    ("key", "state", "expected"),
    [
        ("hook", DemoState(), []),
        ("capture", DemoState(), [(False, "No decision captured in the app yet.")]),
        (
            "capture",
            DemoState(captured_decision_ids=["DEC-017"]),
            [(True, "Captured in the app and confirmed: DEC-017.")],
        ),
        ("ask", DemoState(), [(False, "No question asked yet.")]),
        (
            "ask",
            DemoState(asked=True),
            [(False, "Search abstained: insufficient verifiable evidence.")],
        ),
        (
            "ask",
            DemoState(
                asked=True,
                precedent_ids=["DEC-009", "DEC-001"],
                failed_precedent_ids=["DEC-001"],
                lesson_ids=["DEC-014"],
            ),
            [
                (True, "Precedents returned: DEC-009, DEC-001."),
                (True, "Shown as warnings (failed): DEC-001."),
                (True, "Lessons from other regions: DEC-014."),
            ],
        ),
        ("analyze", DemoState(), [(False, "Ask a question first: no draft proposal yet.")]),
        (
            "analyze",
            DemoState(exclusion_ticked=False),
            [(False, "High-volume contract customers are not excluded yet (tab 3).")],
        ),
        (
            "analyze",
            DemoState(exclusion_ticked=True),
            [(True, "High-volume contract customers are excluded in the draft proposal.")],
        ),
        ("recommend", DemoState(), [(False, "No recommendation yet.")]),
        (
            "recommend",
            DemoState(recommendation="Phased pilot at +5%", names_a_rate=True, key_risks=2),
            [(True, "Recommendation on screen: Phased pilot at +5%; 2 key risk(s) cited.")],
        ),
        (
            "recommend",
            DemoState(recommendation="Insufficient evidence to recommend"),
            [(False, "Recommendation on screen: Insufficient evidence to recommend.")],
        ),
        ("close", DemoState(), [(False, "No pilot approved for this question yet.")]),
        (
            "close",
            DemoState(pilot=PilotSummary(proposal_id="PRO-001", status="in_progress")),
            [
                (True, "Pilot PRO-001 is in progress."),
                (False, "No outcome yet: record week 1, then close the pilot."),
            ],
        ),
        (
            "close",
            DemoState(
                pilot=PilotSummary(proposal_id="PRO-001", status="completed", outcome_id="OUT-015")
            ),
            [
                (True, "Pilot PRO-001 is completed."),
                (True, "Outcome OUT-015 is in memory (tab 2)."),
            ],
        ),
    ],
)
def test_live_checks(key: str, state: DemoState, expected) -> None:
    assert [(c.done, c.text) for c in checks(STEP[key], state)] == expected


def test_step_texts_match_the_app() -> None:
    # The capture review prefills "Confirmed by"; the market count is a governance setting.
    assert any("(it is prefilled)" in action for action in STEP["capture"].actions)
    assert not any("Fill in 'Confirmed by'" in action for action in STEP["capture"].actions)
    assert STEP["close"].actions[0] == (
        f"Tab 6: enter your name, choose {GOVERNANCE_RULES.pilot_markets} pilot markets and "
        "approve (simulated)."
    )
