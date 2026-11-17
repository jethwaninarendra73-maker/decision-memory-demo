"""Streamlit entry point. UI wiring only; business logic lives in src/decision_memory.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
import uuid
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from decision_memory.deps import (  # noqa: E402
    missing_dependencies,
    missing_dependencies_message,
)

_missing = missing_dependencies()
if "streamlit" in _missing:
    raise SystemExit(missing_dependencies_message(_missing))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Decision Memory (synthetic demo)", page_icon="🧭", layout="wide")

if _missing:
    st.error(missing_dependencies_message(_missing))
    st.stop()

from decision_memory.config import (  # noqa: E402
    HOSTED_SETTING_NAMES,
    SettingsError,
    load_settings,
)
from decision_memory.db import SchemaError  # noqa: E402
from decision_memory.logging_setup import configure_logging  # noqa: E402
from decision_memory.repository import Repository  # noqa: E402
from decision_memory.seed import FixtureError  # noqa: E402
from decision_memory.session_db import ensure_seeded, session_db  # noqa: E402
from decision_memory.ui.access import GRANTED_KEY, require_access  # noqa: E402
from decision_memory.ui.evidence import EVIDENCE_PARAM  # noqa: E402
from decision_memory.ui.reset import (  # noqa: E402
    apply_pending_reset,
    render_reset_done,
    render_reset_error,
)
from decision_memory.ui.shell import ROLE_KEY, render_app  # noqa: E402

SESSION_ID_KEY = "demo_session_id"  # names this browser session's own demo database


def hosted_secrets() -> dict[str, str]:
    """The few hosted-demo settings, if the host keeps them in Streamlit's secrets (as
    Streamlit Community Cloud does). Locally there are none and this is empty."""
    try:
        return {name: str(st.secrets[name]) for name in HOSTED_SETTING_NAMES if name in st.secrets}
    except Exception:  # noqa: BLE001 - no secrets file is the normal local case
        return {}


try:
    settings = load_settings(env={**os.environ, **hosted_secrets()})
except SettingsError as exc:
    st.error(str(exc))
    st.stop()

logger = configure_logging(settings.log_level)
logger.debug("Rendering shell in %s mode", settings.effective_llm_mode)

if not require_access(settings.access_password):
    st.stop()

try:
    if settings.per_session_db:
        # A hosted demo: this browser session's own fresh copy of the baseline.
        session_id = st.session_state.setdefault(SESSION_ID_KEY, uuid.uuid4().hex)
        settings = settings.model_copy(
            update={"db_path": session_db(settings.session_dir, session_id, settings.fixtures_dir)}
        )
    else:
        ensure_seeded(settings.db_path, settings.fixtures_dir)  # first run: create the data
except (FixtureError, OSError, sqlite3.Error) as exc:
    logger.warning("Could not create the demo data: %s", type(exc).__name__)
    st.error("The demo data could not be created. Run `python scripts/reset_demo.py`.")
    st.stop()

# A confirmed demo reset rebuilds the database file, so it runs before the file is opened.
# It keeps the role, the access granted and (hosted) this session's own database.
kept = (ROLE_KEY, GRANTED_KEY, SESSION_ID_KEY)
if apply_pending_reset(settings, keep=kept) and EVIDENCE_PARAM in st.query_params:
    del st.query_params[EVIDENCE_PARAM]  # the source that was open may be gone
if render_reset_done():
    st.stop()

try:
    repo = Repository.open(settings.db_path)
except SchemaError as exc:
    render_reset_error()
    st.error(f"{exc} Run `python scripts/reset_demo.py` to rebuild the demo database.")
    st.stop()
except sqlite3.OperationalError as exc:
    # The file exists but cannot be used right now: locked, read-only or not a file.
    render_reset_error()
    st.error(
        f"The demo database {settings.db_path} could not be opened ({exc}). If another "
        "program or a reset is using it, wait a moment and reload. If the file is "
        "read-only, make it writable. Nothing was changed."
    )
    st.stop()
except sqlite3.DatabaseError:
    # Not a SQLite database at all (some other file at DM_DB_PATH). Leave it alone.
    render_reset_error()
    st.error(
        f"{settings.db_path} is not a database this app can open, so it was left untouched. "
        "Point `DM_DB_PATH` at the demo database, or unset it to use `data/demo.db`."
    )
    st.stop()

try:
    render_app(settings, repo)
finally:
    repo.close()
