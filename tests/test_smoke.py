"""Stage 01 smoke tests: imports, settings, agent stubs and the Streamlit shell.

Everything here runs offline with no API key.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from decision_memory import SYNTHETIC_BANNER
from decision_memory.agents import AGENTS
from decision_memory.config import REPO_ROOT, SettingsError, load_settings
from decision_memory.deps import missing_dependencies, missing_dependencies_message

AGENT_MODULES = (
    "capture_agent",
    "memory_agent",
    "impact_agent",
    "scenario_agent",
    "recommendation_agent",
    "governance_agent",
)


# --- dependencies -----------------------------------------------------------


def test_required_packages_are_installed() -> None:
    assert missing_dependencies() == []


def test_missing_package_is_reported_with_install_hint() -> None:
    missing = missing_dependencies(("pydantic", "definitely_not_a_real_package_xyz"))
    assert missing == ["definitely_not_a_real_package_xyz"]
    message = missing_dependencies_message(missing)
    assert "definitely_not_a_real_package_xyz" in message
    assert "pip install -r requirements.txt" in message


# --- settings ---------------------------------------------------------------


def test_defaults_are_offline_with_no_environment() -> None:
    settings = load_settings(env={}, env_file=None)
    assert settings.llm_mode == "offline"
    assert settings.effective_llm_mode == "offline"
    assert settings.anthropic_api_key is None
    assert settings.llm_fallback_reason is None
    assert settings.db_path == REPO_ROOT / "data" / "demo.db"


def test_anthropic_mode_without_key_falls_back_to_offline() -> None:
    settings = load_settings(env={"LLM_MODE": "anthropic"}, env_file=None)
    assert settings.effective_llm_mode == "offline"
    assert settings.llm_fallback_reason is not None


def test_anthropic_mode_needs_flag_and_key() -> None:
    key_only = load_settings(env={"ANTHROPIC_API_KEY": "placeholder-not-a-key"}, env_file=None)
    assert key_only.effective_llm_mode == "offline"
    both = load_settings(
        env={"LLM_MODE": "Anthropic", "ANTHROPIC_API_KEY": "placeholder-not-a-key"},
        env_file=None,
    )
    assert both.effective_llm_mode == "anthropic"


def test_api_key_never_appears_in_repr() -> None:
    settings = load_settings(env={"ANTHROPIC_API_KEY": "placeholder-not-a-key"}, env_file=None)
    assert "placeholder-not-a-key" not in repr(settings)
    assert "placeholder-not-a-key" not in str(settings)


@pytest.mark.parametrize(
    "env",
    [{"LLM_MODE": "openai"}, {"DM_LOG_LEVEL": "LOUD"}],
)
def test_invalid_values_raise_readable_error(env: dict[str, str]) -> None:
    with pytest.raises(SettingsError, match="Invalid settings"):
        load_settings(env=env, env_file=None)


def test_env_file_is_read_and_real_environment_wins(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nDM_LOG_LEVEL=debug\nDM_DB_PATH=data/other.db\n")
    from_file = load_settings(env={}, env_file=env_file)
    assert from_file.log_level == "DEBUG"
    assert from_file.db_path == REPO_ROOT / "data" / "other.db"
    overridden = load_settings(env={"DM_LOG_LEVEL": "ERROR"}, env_file=env_file)
    assert overridden.log_level == "ERROR"


# --- agent stubs ------------------------------------------------------------


def test_six_agents_in_demo_order() -> None:
    assert [agent.number for agent in AGENTS] == [1, 2, 3, 4, 5, 6]
    assert len({agent.key for agent in AGENTS}) == 6


@pytest.mark.parametrize("module_name", AGENT_MODULES)
def test_agent_module_imports_and_exposes_info(module_name: str) -> None:
    module = importlib.import_module(f"decision_memory.agents.{module_name}")
    assert module.INFO in AGENTS


def test_stub_entry_point_says_which_stage_builds_it() -> None:
    from decision_memory.agents import capture_agent

    with pytest.raises(NotImplementedError, match="Stage 04"):
        capture_agent.extract_decision_draft("any transcript")


# --- Streamlit shell --------------------------------------------------------


@pytest.fixture
def app(run_app):
    """app.py run headlessly against a seeded temporary database, with no API key."""
    return run_app()


def test_app_renders_without_exception(app) -> None:
    assert not app.exception
    assert not app.error


def test_app_shows_synthetic_banner(app) -> None:
    assert any(SYNTHETIC_BANNER in warning.value for warning in app.warning)


def test_app_has_six_labelled_stage_tabs(app) -> None:
    assert [tab.label for tab in app.tabs] == [agent.label for agent in AGENTS]


def test_demo_reset_is_a_disabled_placeholder(app) -> None:
    reset = app.button(key="demo_reset")
    assert reset.label == "Demo reset"
    assert reset.disabled
