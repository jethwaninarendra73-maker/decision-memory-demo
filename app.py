"""Streamlit entry point. UI wiring only; business logic lives in src/decision_memory.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import sqlite3
import sys
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

from decision_memory.config import SettingsError, load_settings  # noqa: E402
from decision_memory.db import SchemaError  # noqa: E402
from decision_memory.logging_setup import configure_logging  # noqa: E402
from decision_memory.repository import Repository  # noqa: E402
from decision_memory.ui.evidence import EVIDENCE_PARAM  # noqa: E402
from decision_memory.ui.reset import (  # noqa: E402
    apply_pending_reset,
    render_reset_done,
    render_reset_error,
)
from decision_memory.ui.shell import ROLE_KEY, render_app  # noqa: E402

try:
    settings = load_settings()
except SettingsError as exc:
    st.error(str(exc))
    st.stop()

logger = configure_logging(settings.log_level)
logger.debug("Rendering shell in %s mode", settings.effective_llm_mode)

# A confirmed demo reset rebuilds the database file, so it runs before the file is opened.
if apply_pending_reset(settings, keep=(ROLE_KEY,)) and EVIDENCE_PARAM in st.query_params:
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
