"""Streamlit entry point. UI wiring only; business logic lives in src/decision_memory.

Run with:  streamlit run app.py
"""

from __future__ import annotations

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
from decision_memory.logging_setup import configure_logging  # noqa: E402
from decision_memory.ui.shell import render_app  # noqa: E402

try:
    settings = load_settings()
except SettingsError as exc:
    st.error(str(exc))
    st.stop()

logger = configure_logging(settings.log_level)
logger.debug("Rendering shell in %s mode", settings.effective_llm_mode)

render_app(settings)
