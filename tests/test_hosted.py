"""Hosting the demo (Streamlit Community Cloud): a fresh copy of the demo data for each
visitor, an optional password screen, settings from the host's secrets, and a database
that creates itself. All of it is off unless configured, so the laptop setup is unchanged."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from decision_memory.config import load_settings
from decision_memory.models import Decision
from decision_memory.repository import Repository
from decision_memory.session_db import ensure_seeded, prune, session_db
from decision_memory.ui.access import WRONG_PASSWORD
from decision_memory.ui.reset import CONFIRM_KEY, CONTINUE_KEY, RESET_KEY

from conftest import APP_FILE, FIXTURES_DIR, OFFLINE_ENV, dump_database
from test_capture_agent import DEMO, confirm

SESSION = "0123456789abcdef0123456789abcdef"
PASSWORD = "demo-pass-123"


def test_hosted_settings_are_off_unless_set(tmp_path: Path) -> None:
    plain = load_settings(env=OFFLINE_ENV, env_file=None)
    assert not plain.per_session_db and plain.access_password is None
    hosted = load_settings(
        env={
            **OFFLINE_ENV,
            "DM_PER_SESSION_DB": "true",
            "DM_ACCESS_PASSWORD": PASSWORD,
            "DM_SESSION_DIR": str(tmp_path),
        },
        env_file=None,
    )
    assert hosted.per_session_db and hosted.session_dir == tmp_path
    assert hosted.access_password.get_secret_value() == PASSWORD
    assert PASSWORD not in repr(hosted)  # a secret never shows in logs or errors


def test_a_missing_database_creates_itself_and_an_existing_one_is_left_alone(
    tmp_path: Path, seeded_db: Path
) -> None:
    fresh = tmp_path / "new" / "demo.db"
    assert ensure_seeded(fresh, FIXTURES_DIR)
    with Repository.open(fresh) as repo:
        assert repo.count(Decision) == 16
    with Repository.open(seeded_db) as repo:
        confirm(repo, DEMO)
    used = dump_database(seeded_db)
    assert not ensure_seeded(seeded_db, FIXTURES_DIR)
    assert dump_database(seeded_db) == used


def test_each_session_gets_its_own_copy_and_keeps_it(tmp_path: Path) -> None:
    first = session_db(tmp_path, SESSION, FIXTURES_DIR)
    other = session_db(tmp_path, "f" * 32, FIXTURES_DIR)
    assert first != other
    with Repository.open(first) as repo:
        confirm(repo, DEMO)
    assert session_db(tmp_path, SESSION, FIXTURES_DIR) == first  # the same copy again
    with Repository.open(first) as repo, Repository.open(other) as clean:
        assert repo.count(Decision) == 17 and clean.count(Decision) == 16
    with pytest.raises(ValueError):
        session_db(tmp_path, "../../etc/passwd", FIXTURES_DIR)


def test_old_copies_are_removed_and_nothing_else(tmp_path: Path) -> None:
    old = session_db(tmp_path, SESSION, FIXTURES_DIR)
    keep = tmp_path / "notes.txt"
    keep.write_text("not ours")
    hours_ago = time.time() - 7 * 3600
    os.utime(old, (hours_ago, hours_ago))
    assert prune(tmp_path, time.time()) == 1
    assert not old.exists() and keep.exists()


# --- in the running app ----------------------------------------------------------------


@pytest.fixture
def hosted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    sessions = tmp_path / "sessions"
    monkeypatch.setenv("DM_PER_SESSION_DB", "true")
    monkeypatch.setenv("DM_SESSION_DIR", str(sessions))
    return sessions


def test_visitors_do_not_see_each_others_work(run_app, hosted: Path, seeded_db: Path) -> None:
    shared_before = dump_database(seeded_db)
    alice = run_app()
    alice = alice.button(key="capture_extract").click().run()
    (confirm_button,) = [b for b in alice.button if b.label == "Confirm and save to memory"]
    alice = confirm_button.click().run()
    assert not alice.exception
    bob = run_app()  # a second browser session
    files = sorted(hosted.glob("*.db"))
    assert len(files) == 2  # one copy each
    counts = []
    for path in files:
        with Repository.open(path) as repo:
            counts.append(repo.count(Decision))
    assert sorted(counts) == [16, 17]  # Alice's capture is in her copy only
    assert "DEC-017" not in str([m.value for m in bob.markdown])
    assert dump_database(seeded_db) == shared_before  # the shared database is untouched


def test_a_visitors_reset_restores_only_their_copy(run_app, hosted: Path) -> None:
    app = run_app()
    (mine,) = hosted.glob("*.db")
    with Repository.open(mine) as repo:
        confirm(repo, DEMO)
    app = app.button(key=RESET_KEY).click().run().button(key=CONFIRM_KEY).click().run()
    app = app.button(key=CONTINUE_KEY).click().run()
    assert not app.exception
    assert list(hosted.glob("*.db")) == [mine]  # the same copy, rebuilt
    with Repository.open(mine) as repo:
        assert repo.count(Decision) == 16


def test_the_password_screen_shows_nothing_else_until_the_password_is_right(
    run_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DM_ACCESS_PASSWORD", PASSWORD)
    app = run_app()
    assert not app.tabs and not app.metric and not app.sidebar.button
    app = submit(app, "guess")
    assert WRONG_PASSWORD in [e.value for e in app.error] and not app.tabs
    app = submit(app, PASSWORD)
    assert not app.exception and len(app.tabs) == 6
    # A demo reset keeps the visitor in.
    app = app.button(key=RESET_KEY).click().run().button(key=CONFIRM_KEY).click().run()
    app = app.button(key=CONTINUE_KEY).click().run()
    assert len(app.tabs) == 6
    assert PASSWORD not in str([*app.markdown, *app.caption, *app.text])


def submit(app, password: str):
    app.text_input(key="access_password_entry").set_value(password)
    (button,) = [b for b in app.button if b.label == "Open the demo"]
    return button.click().run()


def test_the_host_can_set_the_password_in_streamlit_secrets(
    monkeypatch: pytest.MonkeyPatch, seeded_db: Path
) -> None:
    from streamlit.testing.v1 import AppTest

    for name, value in OFFLINE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DM_DB_PATH", str(seeded_db))
    monkeypatch.delenv("DM_ACCESS_PASSWORD", raising=False)
    app = AppTest.from_file(str(APP_FILE), default_timeout=30)
    app.secrets["DM_ACCESS_PASSWORD"] = PASSWORD  # as Community Cloud's secrets box does
    app = app.run()
    assert not app.tabs and [b for b in app.button if b.label == "Open the demo"]
