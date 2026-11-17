"""The sidebar demo reset (Stage 14): it asks first, restores the baseline byte for byte,
puts the page back at its defaults, and says nothing a role may not know."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from decision_memory.models import Proposal
from decision_memory.repository import Repository
from decision_memory.seed import FixtureError, ResetRefusedError
from decision_memory.ui import reset as reset_ui
from decision_memory.ui.evidence import SELECT_KEY as EVIDENCE_KEY
from decision_memory.ui.evidence import UNAVAILABLE_MESSAGE
from decision_memory.ui.guide import STEP_KEY, TAB_KEY
from decision_memory.ui.reset import (
    CANCEL_KEY,
    CONFIRM_KEY,
    CONFIRM_WARNING,
    CONTINUE_KEY,
    FIXTURES_FAILED,
    REBUILD_FAILED,
    RESET_BUSY,
    RESET_DONE,
    RESET_KEY,
    RESET_REFUSED,
)
from decision_memory.ui.scenarios import BASELINE_KEY
from decision_memory.ui.search import QUESTION_KEY
from decision_memory.ui.shell import ROLE_KEY

from conftest import HIDDEN_FROM_ANALYST, dump_database, rendered_text
from test_capture_agent import DEMO, confirm
from test_governance_agent import OFF_TOPIC, approve, close, kpi, restricted_approval
from test_ui_guide import LABELS, go_to


def has(app, key: str) -> bool:
    return any(button.key == key for button in app.button)


def use_the_app(db: Path) -> None:
    """A captured decision, a closed pilot and a restricted pilot: what a demo leaves."""
    with Repository.open(db) as repo:
        confirm(repo, DEMO)
        pilot = approve(repo).proposal.id
        kpi(repo, pilot)
        close(repo, pilot)
        restricted_approval(repo)


def reset(app):
    app = app.button(key=RESET_KEY).click().run()
    return app.button(key=CONFIRM_KEY).click().run()


def test_the_reset_button_asks_before_it_does_anything(run_app, seeded_db: Path) -> None:
    use_the_app(seeded_db)
    used = dump_database(seeded_db)
    app = run_app()
    button = app.button(key=RESET_KEY)
    assert button.label == "Demo reset" and not button.disabled
    assert not has(app, CONFIRM_KEY)

    app = button.click().run()
    assert CONFIRM_WARNING in [w.value for w in app.sidebar.warning]
    assert has(app, CONFIRM_KEY) and not has(app, RESET_KEY)
    assert dump_database(seeded_db) == used  # the first click changed nothing

    app = app.button(key=CANCEL_KEY).click().run()
    assert has(app, RESET_KEY) and not has(app, CONFIRM_KEY)
    assert dump_database(seeded_db) == used


def test_a_confirmed_reset_restores_the_baseline_byte_for_byte(run_app, seeded_db: Path) -> None:
    baseline = dump_database(seeded_db)
    use_the_app(seeded_db)
    assert dump_database(seeded_db) != baseline
    app = reset(run_app())
    assert not app.exception and not app.error
    assert dump_database(seeded_db) == baseline
    with Repository.open(seeded_db) as repo:
        assert repo.count(Proposal) == 0


def test_after_a_reset_the_page_starts_again_from_its_defaults(run_app, seeded_db: Path) -> None:
    app = run_app()
    default_question = app.text_input(key=QUESTION_KEY).value
    default_baseline = app.number_input(key=BASELINE_KEY).value
    app.text_input(key=QUESTION_KEY).set_value(OFF_TOPIC)
    app.number_input(key=BASELINE_KEY).set_value(default_baseline + 100)
    app = go_to(app.run(), 4)
    assert app.session_state[TAB_KEY] == LABELS[4]

    # The "reset done" page draws none of the app's inputs, so a browser drops their text.
    app = reset(app)
    assert RESET_DONE in [s.value for s in app.success]
    assert not app.text_input and not app.number_input and not app.tabs and not app.radio
    assert [b.key for b in app.button] == [CONTINUE_KEY]

    app = app.button(key=CONTINUE_KEY).click().run()
    assert not app.exception and not app.error
    assert app.text_input(key=QUESTION_KEY).value == default_question
    assert app.number_input(key=BASELINE_KEY).value == default_baseline
    assert app.radio(key=STEP_KEY).value == 0
    assert has(app, RESET_KEY) and not app.success


def test_the_demo_role_survives_a_reset(run_app, seeded_db: Path) -> None:
    app = reset(run_app(role="analyst"))
    app = app.button(key=CONTINUE_KEY).click().run()
    assert app.session_state[ROLE_KEY] == "analyst"
    assert app.query_params["role"] == "analyst"


def test_a_reset_tells_a_role_nothing_about_hidden_records(run_app, seeded_db: Path) -> None:
    # The analyst resets a database that holds the executive's restricted pilot. No page
    # on the way shows a count, an ID or a word from a record the analyst cannot see.
    use_the_app(seeded_db)
    secrets = (*HIDDEN_FROM_ANALYST, "PRO-002")  # PRO-002 is the restricted pilot

    def shown(page) -> str:
        notices = [*page.warning, *page.success, *page.error, *page.caption]
        return rendered_text(page) + "\n".join(n.value for n in notices)

    app = run_app(role="analyst")
    app = app.button(key=RESET_KEY).click().run()  # the confirmation
    for hidden in secrets:
        assert hidden not in shown(app), hidden
    app = app.button(key=CONFIRM_KEY).click().run()  # the "reset done" page
    done = shown(app)
    assert RESET_DONE in done
    assert not any(char.isdigit() for char in done), done  # no counts, no IDs
    app = app.button(key=CONTINUE_KEY).click().run()  # the app again
    for hidden in secrets:
        assert hidden not in shown(app), hidden


def test_an_evidence_link_is_dropped_by_a_reset(run_app, seeded_db: Path) -> None:
    # The open source was captured in the app, so the reset removes it. Kept in the
    # address, its link would be honoured again and answered with "not available".
    with Repository.open(seeded_db) as repo:
        captured = confirm(repo, DEMO).source.id
    app = run_app(evidence=captured)
    assert app.session_state[EVIDENCE_KEY] == captured
    app = reset(app)
    assert "evidence" not in app.query_params
    app = app.button(key=CONTINUE_KEY).click().run()
    assert "evidence" not in app.query_params
    assert UNAVAILABLE_MESSAGE not in [i.value for i in app.info]


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (ResetRefusedError("notes.db was not created by this app."), None),
        (FixtureError("OUT-900 refers to unknown decision DEC-015"), FIXTURES_FAILED),
        (OSError("locked: DEC-015"), REBUILD_FAILED),
    ],
)
def test_a_failed_reset_keeps_the_page_and_says_why_without_details(
    run_app, seeded_db: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, message
) -> None:
    use_the_app(seeded_db)
    used = dump_database(seeded_db)

    def refuse(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(reset_ui, "reset_database", refuse)
    app = run_app()
    app.text_input(key=QUESTION_KEY).set_value(OFF_TOPIC)
    app = reset(app.run())
    assert not app.exception
    (shown,) = [e.value for e in app.sidebar.error]
    # Only a refusal is quoted; other errors can name records, so the page stays neutral.
    assert shown == (message or RESET_REFUSED.format(reason=error))
    assert (message is None) or ("DEC-015" not in shown)
    assert dump_database(seeded_db) == used
    assert app.text_input(key=QUESTION_KEY).value == OFF_TOPIC  # the page is kept too
    assert has(app, RESET_KEY) and not has(app, CONTINUE_KEY)
    app = app.run()
    assert not app.sidebar.error  # shown once


def test_a_reset_never_deletes_a_file_the_app_did_not_create(run_app, seeded_db: Path) -> None:
    # Some other file takes the database's place between the two clicks of a reset.
    notes = b"my own notes, not a database" * 20
    app = run_app().button(key=RESET_KEY).click().run()
    seeded_db.write_bytes(notes)
    app = app.button(key=CONFIRM_KEY).click().run()
    assert seeded_db.read_bytes() == notes  # refused: the file is left exactly as it was
    assert not app.exception
    refused, cannot_open = [e.value for e in app.error]
    assert refused.startswith("Demo reset refused, nothing was changed:")
    assert "was not created by this app" in refused
    assert "is not a database this app can open, so it was left untouched" in cannot_open
    assert not has(app, CONTINUE_KEY) and not app.tabs


# --- review regressions ---------------------------------------------------------------


def test_a_file_that_is_not_a_database_gets_a_message_not_a_traceback(
    run_app, tmp_path: Path
) -> None:
    other = tmp_path / "notes.db"
    other.write_bytes(b"not a database made by this app" * 10)
    app = run_app(db=other)
    assert not app.exception
    (shown,) = [e.value for e in app.error]
    assert shown.startswith(f"{other} is not a database this app can open")
    assert not app.tabs and not has(app, RESET_KEY)
    assert other.read_bytes() == b"not a database made by this app" * 10


@pytest.mark.parametrize("problem", ["locked", "read-only", "a directory"])
def test_a_demo_database_that_cannot_be_opened_is_not_called_foreign(
    run_app, seeded_db: Path, tmp_path: Path, problem: str
) -> None:
    # The file is the demo database (or the path is wrong in another way); the message
    # gives SQLite's reason and does not say it belongs to another app.
    target, holder = seeded_db, None
    if problem == "locked":
        holder = sqlite3.connect(seeded_db, timeout=0)
        holder.execute("BEGIN EXCLUSIVE")
    elif problem == "read-only":
        os.chmod(seeded_db, 0o444)
    else:
        target = tmp_path / "a_folder"
        target.mkdir()
    try:
        app = run_app(db=target)
    finally:
        if holder is not None:
            holder.rollback()
            holder.close()
        os.chmod(seeded_db, 0o644)
    assert not app.exception and not app.tabs
    (shown,) = [e.value for e in app.error]
    assert shown.startswith(f"The demo database {target} could not be opened (")
    assert "not a database this app can open" not in shown


def test_a_reset_of_a_locked_database_says_so_and_changes_nothing(run_app, seeded_db: Path) -> None:
    use_the_app(seeded_db)
    used = dump_database(seeded_db)
    app = run_app()
    app.text_input(key=QUESTION_KEY).set_value(OFF_TOPIC)
    app = app.run().button(key=RESET_KEY).click().run()
    holder = sqlite3.connect(seeded_db, timeout=0)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        app = app.button(key=CONFIRM_KEY).click().run()
    finally:
        holder.rollback()
        holder.close()
    assert RESET_BUSY in [e.value for e in app.error]
    assert not any("not created by this app" in e.value for e in app.error)
    assert dump_database(seeded_db) == used
    app = app.run()  # the lock is gone: the page is back as it was
    assert not app.exception and not app.error
    assert app.text_input(key=QUESTION_KEY).value == OFF_TOPIC
