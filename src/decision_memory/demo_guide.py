"""The guided five-minute demo: six steps, what to do in each, what to say, and live
checks read from the app's current state (docs/PRODUCT_SPEC.md, section 5).

Pure and deterministic: no Streamlit, no database. The UI builds a `DemoState` from
what the tabs computed for the active role and passes it in. Every number in a talk
track comes from that state (the scenario figures on screen), never from this file.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .agents.governance_agent import DEFAULT_RULES as GOVERNANCE_RULES
from .models import Contract, NonEmptyStr, ScenarioResult

# Three artifacts that tell the story at a glance: a success, the policy, a failure.
HOOK_SOURCE_IDS: tuple[str, ...] = ("SRC-006", "SRC-014", "SRC-002")


class GuideStep(Contract):
    """One step of the five-minute demo."""

    key: NonEmptyStr
    title: NonEmptyStr
    time: NonEmptyStr
    tabs: list[str] = Field(default_factory=list)  # agent keys, the first is opened
    actions: list[NonEmptyStr]
    say: NonEmptyStr


STEPS: tuple[GuideStep, ...] = (
    GuideStep(
        key="hook",
        title="Hook",
        time="0:00–0:30",
        actions=[
            (
                "Show the three synthetic artifacts below: a Teams-style transcript, a steering "
                "deck excerpt and a Jira-style ticket."
            ),
            (
                "Point out that they sit in different places and that none of them answers the "
                "question alone."
            ),
        ],
        say=(
            "The company has made this call before. The answer is spread across "
            "transcripts, decks and tickets that nobody can find in time."
        ),
    ),
    GuideStep(
        key="capture",
        title="Capture",
        time="0:30–1:15",
        tabs=["capture"],
        actions=[
            "Keep 'Demo transcript' selected and click Extract decision.",
            (
                "Point at the exact evidence for each field and at the flagged fields; the "
                "side discussion produces no decision."
            ),
            (
                "Check that 'Confirmed by' names the reviewer (it is prefilled), then confirm. "
                "Only a human-confirmed decision enters memory."
            ),
        ],
        say="Agent 1 proposes, a person confirms. Nothing enters memory unreviewed.",
    ),
    GuideStep(
        key="ask",
        title="Ask",
        time="1:15–2:15",
        tabs=["memory"],
        actions=[
            (
                "The demo question is filled in. Walk through the cited precedents and their "
                "score components."
            ),
            (
                "Point at the failed precedent shown as a warning, and at the lesson from "
                "another region."
            ),
            "Scroll to the graph: the precedents are highlighted.",
        ],
        say=(
            "Every claim cites an exact source excerpt. A failed decision is a warning, "
            "never a template."
        ),
    ),
    GuideStep(
        key="analyze",
        title="Analyze",
        time="2:15–3:30",
        tabs=["impact", "scenario"],
        actions=[
            (
                "Tab 3: show the source-supported and assumed labels, then tick 'Exclude "
                "high-volume contract customers'."
            ),
            "Tab 4: compare the options. The formulas are on screen and every input can be edited.",
        ],
        say=(
            "Each impact item is either quoted from a precedent or labelled as an "
            "assumption. The figures come from visible formulas, not from a model."
        ),
    ),
    GuideStep(
        key="recommend",
        title="Recommend",
        time="3:30–4:30",
        tabs=["recommendation"],
        actions=[
            (
                "Read the card: the recommended option, the option the guardrail excludes, the "
                "key risks and the cited reasons."
            ),
            "Point out that it is advisory and names the human approval it needs.",
        ],
        say="The recommendation follows written rules, cites its reasons and abstains without evidence.",
    ),
    GuideStep(
        key="close",
        title="Close the loop",
        time="4:30–5:00",
        tabs=["governance", "memory"],
        actions=[
            (
                f"Tab 6: enter your name, choose {GOVERNANCE_RULES.pilot_markets} pilot markets and "
                "approve (simulated)."
            ),
            "Record week 1 (prefilled with the plan), then close the pilot.",
            (
                "Tab 2: the pilot decision now shows with its outcome; the failed precedent is "
                "still shown as a warning."
            ),
        ],
        say=(
            "A human approves, the pilot is tracked, and its outcome becomes institutional "
            "memory for the next decision."
        ),
    ),
)


class PilotSummary(Contract):
    proposal_id: NonEmptyStr
    status: NonEmptyStr
    outcome_id: str | None = None


class DemoState(Contract):
    """What the tabs computed on this run, for the active role. Nothing hidden from the
    role is in here, because every field comes from role-filtered results."""

    captured_decision_ids: list[str] = Field(default_factory=list)
    asked: bool = False
    precedent_ids: list[str] = Field(default_factory=list)
    failed_precedent_ids: list[str] = Field(default_factory=list)
    lesson_ids: list[str] = Field(default_factory=list)
    exclusion_ticked: bool | None = None  # None: no draft proposal yet
    scenario: ScenarioResult | None = None  # the option the talk track explains
    recommendation: str | None = None  # headline, e.g. "Phased pilot at +5%"
    names_a_rate: bool = False
    key_risks: Annotated[int, Field(ge=0)] = 0
    pilot: PilotSummary | None = None


class StepCheck(Contract):
    """One live check: done or not, and what the app currently shows."""

    done: bool
    text: NonEmptyStr


def _pct(fraction: float) -> str:
    return f"{round(fraction * 100, 3):g}%"


def _musd(value: float) -> str:
    sign = "+" if value >= 0 else "−"
    return f"{sign}${abs(value):,.2f}M"


def revenue_talk_track(scenario: ScenarioResult) -> str:
    """'Where does the figure come from?', with the numbers currently on screen."""
    a = scenario.assumptions
    base = f"${a.baseline_revenue_musd:,.2f}M"
    return (
        f"Where does {_musd(scenario.revenue_change_musd)} come from? {base} × "
        f"{_pct(scenario.surcharge_rate)} × {_pct(1 - a.exempt_share)} not exempt = "
        f"{_musd(scenario.gross_uplift_musd)}, minus {base} × {_pct(a.churn_loss_fraction)} "
        f"churn loss = ${scenario.churn_loss_musd:,.2f}M, gives "
        f"{_musd(scenario.revenue_change_musd)} revenue change a quarter, before margin."
    )


def checks(step: GuideStep, state: DemoState) -> list[StepCheck]:
    """Live checks for a step, from the current state. Pure."""
    if step.key == "hook":
        return []
    if step.key == "capture":
        captured = state.captured_decision_ids
        return [
            StepCheck(
                done=bool(captured),
                text=(
                    f"Captured in the app and confirmed: {', '.join(captured)}."
                    if captured
                    else "No decision captured in the app yet."
                ),
            )
        ]
    if step.key == "ask":
        if not state.asked:
            return [StepCheck(done=False, text="No question asked yet.")]
        found = [
            StepCheck(
                done=bool(state.precedent_ids),
                text=(
                    f"Precedents returned: {', '.join(state.precedent_ids)}."
                    if state.precedent_ids
                    else "Search abstained: insufficient verifiable evidence."
                ),
            )
        ]
        if state.failed_precedent_ids:
            found.append(
                StepCheck(
                    done=True,
                    text=f"Shown as warnings (failed): {', '.join(state.failed_precedent_ids)}.",
                )
            )
        if state.lesson_ids:
            found.append(
                StepCheck(
                    done=True, text=f"Lessons from other regions: {', '.join(state.lesson_ids)}."
                )
            )
        return found
    if step.key == "analyze":
        if state.exclusion_ticked is None:
            return [StepCheck(done=False, text="Ask a question first: no draft proposal yet.")]
        return [
            StepCheck(
                done=state.exclusion_ticked,
                text=(
                    "High-volume contract customers are excluded in the draft proposal."
                    if state.exclusion_ticked
                    else "High-volume contract customers are not excluded yet (tab 3)."
                ),
            )
        ]
    if step.key == "recommend":
        if state.recommendation is None:
            return [StepCheck(done=False, text="No recommendation yet.")]
        risks = f"; {state.key_risks} key risk(s) cited" if state.key_risks else ""
        return [
            StepCheck(
                done=state.names_a_rate,
                text=f"Recommendation on screen: {state.recommendation}{risks}.",
            )
        ]
    if step.key == "close":
        pilot = state.pilot
        if pilot is None:
            return [StepCheck(done=False, text="No pilot approved for this question yet.")]
        approved = StepCheck(
            done=True, text=f"Pilot {pilot.proposal_id} is {pilot.status.replace('_', ' ')}."
        )
        outcome = StepCheck(
            done=pilot.outcome_id is not None,
            text=(
                f"Outcome {pilot.outcome_id} is in memory (tab 2)."
                if pilot.outcome_id
                else "No outcome yet: record week 1, then close the pilot."
            ),
        )
        return [approved, outcome]
    raise ValueError(f"Unknown step {step.key!r}")


def talk_track(step: GuideStep, state: DemoState) -> list[str]:
    """What to say, with any figure taken from the current state."""
    lines = [step.say]
    if step.key == "analyze" and state.scenario is not None:
        lines.append(revenue_talk_track(state.scenario))
    return lines
