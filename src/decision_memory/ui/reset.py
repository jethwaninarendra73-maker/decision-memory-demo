"""Demo reset: the sidebar control, and the reset it asks for.

The control never touches the database. Its buttons only set flags: the first click asks
for confirmation, the second raises a request. `apply_pending_reset` carries the request
out at the start of the next run, before the app opens the database, because a reset
deletes and rebuilds the file.

After a reset the app shows a short "reset done" page before it draws the tabs again.
That page has none of the app's inputs on it, so the browser drops what was typed into
them; clearing the session alone would leave the old text on screen.

The messages are the same for every demo role and carry no counts, so a reset says
nothing about records the role cannot see. The rebuilt database is the baseline, byte for
byte, so the reset leaves no audit event of its own; it is logged instead.
"""

from __future__ import annotations

import logging
import sqlite3

import streamlit as st

from decision_memory.config import Settings
from decision_memory.db import DatabaseUnavailableError
from decision_memory.seed import FixtureError, ResetRefusedError, reset_database

logger = logging.getLogger(__name__)

RESET_KEY = "demo_reset"  # the sidebar button
CONFIRM_KEY = "demo_reset_confirm"
CANCEL_KEY = "demo_reset_cancel"
ARMED_KEY = "demo_reset_armed"  # the first click was made; show the confirmation
REQUEST_KEY = "demo_reset_requested"  # confirmed; applied at the start of the next run
DONE_KEY = "demo_reset_done"  # the reset ran; show the "reset done" page
CONTINUE_KEY = "demo_reset_continue"
ERROR_KEY = "demo_reset_error"  # why a reset failed, shown once

RESET_HELP = "Restores the synthetic baseline for the next rehearsal. Asks for confirmation first."
CONFIRM_WARNING = (
    "This deletes everything created in the app (captured decisions, approvals, pilot "
    "records and outcomes), for every demo role, and restores the synthetic baseline. It "
    "also clears what you typed in this browser tab. It cannot be undone."
)
RESET_DONE = "Demo data reset to the synthetic baseline. Everything created in the app was removed."
CONTINUE_NOTE = (
    "The guided demo, the question, the scenario inputs and every other field are back at "
    "their defaults. Fixture files were not changed."
)
USE_THE_SCRIPT = "Run `python scripts/reset_demo.py` in a terminal to see why."
RESET_REFUSED = "Demo reset refused, nothing was changed: {reason}"
RESET_BUSY = (
    "Demo reset not done, nothing was changed: the demo database is in use or cannot be "
    "read. Wait a moment and try again; if it keeps happening, close other programs using "
    "it or run `python scripts/reset_demo.py`."
)
FIXTURES_FAILED = (
    "Demo reset failed, nothing was changed: the fixture files could not be loaded. "
    + USE_THE_SCRIPT
)
REBUILD_FAILED = (
    "Demo reset did not finish: the database file could not be rebuilt, so the demo data "
    "may be incomplete. " + USE_THE_SCRIPT
)


def _arm() -> None:
    st.session_state[ARMED_KEY] = True


def _disarm() -> None:
    st.session_state.pop(ARMED_KEY, None)


def _request() -> None:
    st.session_state.pop(ARMED_KEY, None)
    st.session_state[REQUEST_KEY] = True


def apply_pending_reset(settings: Settings, keep: tuple[str, ...] = ()) -> bool:
    """Carry out a confirmed reset. Call before the database is opened.

    On success every session value except those named in `keep` is dropped, so the page
    starts again from its defaults. On failure the session is kept and the reason is
    shown once in the sidebar. Returns True if a reset was done.
    """
    if not st.session_state.pop(REQUEST_KEY, False):
        return False
    try:
        reset_database(settings.db_path, settings.fixtures_dir)
    except (
        ResetRefusedError,
        DatabaseUnavailableError,
        FixtureError,
        OSError,
        sqlite3.Error,
    ) as exc:
        # Only a refusal is shown as raised (it names the file). The other messages can
        # name fixture records, so they go to the log and the page stays neutral.
        logger.warning("Demo reset from the app failed: %s: %s", type(exc).__name__, exc)
        if isinstance(exc, ResetRefusedError):
            st.session_state[ERROR_KEY] = RESET_REFUSED.format(reason=exc)
        elif isinstance(exc, DatabaseUnavailableError):
            st.session_state[ERROR_KEY] = RESET_BUSY
        elif isinstance(exc, FixtureError):
            st.session_state[ERROR_KEY] = FIXTURES_FAILED
        else:
            st.session_state[ERROR_KEY] = REBUILD_FAILED
        return False
    kept = {key: st.session_state[key] for key in keep if key in st.session_state}
    st.session_state.clear()
    st.session_state.update(kept)
    st.session_state[DONE_KEY] = True
    logger.info("Demo reset from the app: the synthetic baseline was restored")
    return True


def _continue() -> None:
    st.session_state.pop(DONE_KEY, None)


def render_reset_done() -> bool:
    """The page shown right after a reset. True if it was drawn: the caller then stops,
    so that none of the app's inputs is drawn on this run."""
    if not st.session_state.get(DONE_KEY):
        return False
    st.success(RESET_DONE)
    st.caption(CONTINUE_NOTE)
    st.button("Start the demo from step 1", key=CONTINUE_KEY, type="primary", on_click=_continue)
    return True


def render_reset_error() -> None:
    """Why the last reset failed, shown once."""
    error = st.session_state.pop(ERROR_KEY, None)
    if error is not None:
        st.error(error)


def render_reset_control() -> None:
    """The sidebar control: a button, then a confirmation. Buttons only set flags."""
    render_reset_error()
    if not st.session_state.get(ARMED_KEY):
        st.button("Demo reset", key=RESET_KEY, help=RESET_HELP, on_click=_arm)
        return
    st.warning(CONFIRM_WARNING, icon="⚠️")
    confirm, cancel = st.columns(2)
    with confirm:
        st.button("Yes, reset", key=CONFIRM_KEY, type="primary", on_click=_request)
    with cancel:
        st.button("Cancel", key=CANCEL_KEY, on_click=_disarm)
