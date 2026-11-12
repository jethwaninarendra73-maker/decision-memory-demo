"""Dependency check using the standard library only.

Imported before any third-party package so a missing install produces a clear
message instead of a stack trace.
"""

from __future__ import annotations

from importlib.util import find_spec

REQUIRED_PACKAGES: tuple[str, ...] = ("streamlit", "pydantic", "networkx", "plotly")
INSTALL_HINT = (
    "Activate the virtual environment and install the requirements:\n"
    "  source .venv/bin/activate\n"
    "  pip install -r requirements.txt"
)


def missing_dependencies(packages: tuple[str, ...] = REQUIRED_PACKAGES) -> list[str]:
    """Return the import names in `packages` that are not installed."""
    return [name for name in packages if find_spec(name) is None]


def missing_dependencies_message(missing: list[str]) -> str:
    """Human-readable error for a non-empty list of missing packages."""
    return f"Missing Python packages: {', '.join(missing)}.\n{INSTALL_HINT}"
