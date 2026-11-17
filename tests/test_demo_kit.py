"""The demo kit (Stage 14): the script and checklist say what the app says and shows."""

from __future__ import annotations

import re

import pytest

from decision_memory import DEMO_QUESTION, SYNTHETIC_BANNER
from decision_memory.agents import AGENTS
from decision_memory.agents.capture_agent import DuplicateCaptureError
from decision_memory.agents.governance_agent import DEFAULT_RULES as GOVERNANCE_RULES
from decision_memory.authz import ROLE_LABELS
from decision_memory.demo_guide import HOOK_SOURCE_IDS, STEPS, revenue_talk_track
from decision_memory.models import Decision, Outcome, Proposal
from decision_memory.provenance import INSUFFICIENT_EVIDENCE_TEXT
from decision_memory.repository import Repository
from decision_memory.ui.governance import ACTOR_KEY, APPROVE_KEY, MARKETS_KEY, close_key, kpi_key
from decision_memory.ui.impact import EXCLUDE_HVC_KEY
from decision_memory.ui.reset import CONFIRM_KEY, CONTINUE_KEY, RESET_KEY

from conftest import REPO_ROOT
from test_capture_agent import DEMO, confirm
from test_demo_guide import scenario
from test_ui_guide import checks, next_step
from test_ui_scenarios import DEFAULT_MARGIN, DEFAULT_REVENUE

DOCS = REPO_ROOT / "docs"
SCRIPT = (DOCS / "DEMO_SCRIPT.md").read_text(encoding="utf-8")
CHECKLIST = (DOCS / "DEMO_CHECKLIST.md").read_text(encoding="utf-8")
LINK = re.compile(r"\]\(([^)#]+)(?:#[^)]*)?\)")


@pytest.mark.parametrize("text", [SCRIPT, CHECKLIST], ids=["script", "checklist"])
def test_the_kit_carries_the_synthetic_label_and_working_links(text: str) -> None:
    assert SYNTHETIC_BANNER in text
    for target in LINK.findall(text):
        if not target.startswith(("http://", "https://")):
            assert (DOCS / target).is_file(), target


def test_the_script_follows_the_guides_six_steps() -> None:
    assert DEMO_QUESTION in SCRIPT
    for number, step in enumerate(STEPS, start=1):
        assert f"## {number}. {step.title} ({step.time})" in SCRIPT
        assert f"| {number}. {step.title} | {step.time} |" in SCRIPT
        assert f"> {step.say}" in SCRIPT  # the line the guide panel shows
    for source_id in HOOK_SOURCE_IDS:
        assert source_id in SCRIPT


def test_the_scripts_figures_are_the_ones_the_app_computes() -> None:
    # The app computes these from its default inputs; the script only quotes them.
    for rate, revenue, margin in zip(("+3%", "+5%", "+7%"), DEFAULT_REVENUE, DEFAULT_MARGIN):
        assert f"| {rate} | {revenue} | {margin} |" in SCRIPT
    assert f"> {revenue_talk_track(scenario())}" in SCRIPT


# What the script tells the presenter to click, tick or fill in, as the app labels it.
SCRIPT_CONTROLS = (
    "Read in full below",
    "Next step ▶",
    "Demo transcript",
    "Extract decision",
    "Confirm and save to memory",
    "Exclude high-volume contract customers",
    f"Open {AGENTS[3].label}",
    "Restore default inputs",
    "Approve (simulated)",
    "Record week 1 KPI",
    "Close the pilot and write the outcome to memory",
    f"Open {AGENTS[1].label}",
    "Open a source",
    "Demo role",
    "Guided demo",
    "Simple view",
    "Demo reset",
)
SCRIPT_FIELDS = ("Confirmed by", "Your name")  # the script shortens these two labels
SCRIPT_HEADINGS = (
    "Advisory recommendation: Phased pilot at +5%",
    "How each option was judged",
    "Evidence explorer",
)


def controls(app) -> set[str]:
    """Labels of everything on the page a presenter can click, tick, choose or fill in."""
    kinds = (app.button, app.checkbox, app.text_input, app.selectbox, app.toggle, app.multiselect)
    found = {element.label for kind in kinds for element in kind}
    found |= {option for radio in app.radio for option in radio.options}
    return found


def live_checks(app) -> list[str]:
    return [line.removeprefix("- ✅ ") for line in checks(app).splitlines() if "✅" in line]


def test_the_script_names_what_the_app_shows(run_app, seeded_db) -> None:
    # Walk the whole demo and collect what the app shows on the way.
    app = run_app()
    seen, green, headings = controls(app), [], set()

    def note(page, *, step_done: bool = True) -> None:
        seen.update(controls(page))
        headings.update(m.value.strip("#* ") for m in page.markdown)
        headings.update(h.value for h in page.subheader)
        if step_done:  # the script quotes the checks as they stand when a step is finished
            green.extend(line for line in live_checks(page) if line not in green)

    app = next_step(app)  # 2 Capture
    app = app.button(key="capture_extract").click().run()
    note(app, step_done=False)
    (confirm_button,) = [b for b in app.button if b.label == "Confirm and save to memory"]
    app = confirm_button.click().run()
    note(app)
    app = next_step(app)  # 3 Ask
    note(app)
    app = next_step(app)  # 4 Analyze
    app.checkbox(key=EXCLUDE_HVC_KEY).check()
    app = app.run()
    note(app)
    app = next_step(app)  # 5 Recommend
    note(app)
    app = next_step(app)  # 6 Close the loop
    app.text_input(key=ACTOR_KEY).set_value("Dana Example")
    app = app.run()
    app.multiselect(key=MARKETS_KEY).set_value(["France", "Italy"])
    app = app.run()
    note(app, step_done=False)
    app = app.button(key=APPROVE_KEY).click().run()
    note(app, step_done=False)
    app = app.button(key=kpi_key("record", "PRO-001", 1)).click().run()
    note(app, step_done=False)
    app = app.button(key=close_key("submit", "PRO-001")).click().run()
    note(app)

    for label in SCRIPT_CONTROLS:
        assert label in seen, label
        assert f"**{label}**" in SCRIPT, label
    for short in SCRIPT_FIELDS:
        assert any(label.startswith(short) for label in seen), short
        assert f"**{short}**" in SCRIPT, short
    for heading in SCRIPT_HEADINGS:
        assert heading in headings, heading
        assert f"**{heading}**" in SCRIPT, heading
    for agent in AGENTS:  # every tab is named as the app labels it
        assert agent.label in SCRIPT, agent.label
    for label in ROLE_LABELS.values():
        assert f"**{label}**" in SCRIPT
    assert len(green) == 8  # one for capture, three for ask, then one, one and two
    for line in green:  # each green live check is quoted in the script
        assert line.rstrip(".") in SCRIPT, line

    # The records the demo creates carry the IDs the script names.
    with Repository.open(seeded_db) as repo:
        captured, pilot = sorted(repo.find(Decision), key=lambda d: d.id)[-2:]
        (proposal,) = repo.find(Proposal)
        outcome = max(repo.find(Outcome), key=lambda o: o.id)
    assert outcome.decision_id == pilot.id
    for record_id in (
        captured.id,
        captured.source_refs[0].source_id,
        pilot.id,
        proposal.id,
        outcome.id,
    ):
        assert f"`{record_id}`" in SCRIPT or f"{record_id} " in SCRIPT, record_id


def test_the_script_quotes_the_apps_own_messages(seeded_db) -> None:
    assert INSUFFICIENT_EVIDENCE_TEXT.rstrip(".").lower() in SCRIPT.lower()
    assert f"choose {GOVERNANCE_RULES.pilot_markets} pilot markets" in "\n".join(STEPS[-1].actions)
    assert "choose two pilot markets" in SCRIPT and GOVERNANCE_RULES.pilot_markets == 2
    with Repository.open(seeded_db) as repo:
        confirm(repo, DEMO)
        with pytest.raises(DuplicateCaptureError) as refused:  # the same transcript again
            confirm(repo, DEMO)
    assert f'"{refused.value}"' in SCRIPT


def test_the_kit_describes_the_reset_the_app_has(run_app) -> None:
    # Demo reset -> Yes, reset -> Start the demo from step 1, as both documents say.
    app = run_app()
    path = [app.button(key=RESET_KEY).label]
    app = app.button(key=RESET_KEY).click().run()
    path.append(app.button(key=CONFIRM_KEY).label)
    app = app.button(key=CONFIRM_KEY).click().run()
    path.append(app.button(key=CONTINUE_KEY).label)
    said = " → ".join(f"**{label}**" for label in path)
    assert said in SCRIPT and said in CHECKLIST
    for command in ("python scripts/reset_demo.py", "streamlit run app.py"):
        assert command in SCRIPT and command in CHECKLIST
    assert "python -m pytest -q" in CHECKLIST
    assert "No decision captured in the app yet." in CHECKLIST  # step 2's check at baseline


def test_the_backup_storyboard_lists_every_frame_in_order() -> None:
    backup = DOCS / "demo_backup"
    index = (backup / "README.md").read_text(encoding="utf-8")
    assert SYNTHETIC_BANNER in index
    listed = re.findall(r"\]\(([\w-]+\.jpg)\)", index)
    on_disk = sorted(path.name for path in backup.glob("*.jpg"))
    assert listed == on_disk and len(listed) == 10  # every frame indexed, in step order
    titles = [step.title for step in STEPS]
    steps = re.findall(r"\| (\d)\. ([^|]+?) \|", index)
    assert [titles[int(n) - 1] for n, _ in steps] == [title.strip() for _, title in steps]
    assert {int(n) for n, _ in steps} == set(range(1, len(STEPS) + 1))  # every step shown
    for name in on_disk:  # real images, not placeholders
        assert (backup / name).read_bytes()[:3] == b"\xff\xd8\xff", name


def test_the_backup_slides_show_every_frame_under_the_label_and_load_nothing_remote() -> None:
    backup = DOCS / "demo_backup"
    slides = (backup / "slides.html").read_text(encoding="utf-8")
    # The label sits outside the frame, so it is on screen for every slide.
    label = re.search(r'<div class="label"[^>]*>(.*?)</div>', slides, re.DOTALL)
    assert label and SYNTHETIC_BANNER in label.group(1)
    frames = re.findall(r'\["([\w-]+\.jpg)", "(\d)\. ([^"]+)"', slides)
    assert [name for name, _, _ in frames] == sorted(p.name for p in backup.glob("*.jpg"))
    assert all(STEPS[int(n) - 1].title == title for _, n, title in frames)
    # Offline: no script, style, font or image from anywhere but this folder.
    assert not re.search(r"""(?:src|href)\s*=\s*["']?(?:https?:)?//""", slides)
    assert "@import" not in slides and "url(" not in slides
    # Every pointer to the backup goes to the slides, which carry the label.
    for doc in (SCRIPT, CHECKLIST):
        assert "demo_backup/slides.html" in doc
    assert "(slides.html)" in (backup / "README.md").read_text(encoding="utf-8")
