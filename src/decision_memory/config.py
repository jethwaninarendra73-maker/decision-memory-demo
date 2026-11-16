"""Typed application settings.

Values come from environment variables, then from an optional git-ignored `.env`
file at the repo root, then from defaults. Nothing here needs network access.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, SecretStr, StringConstraints, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"

LlmMode = Literal["offline", "anthropic"]
# Model for the optional Anthropic mode (Stage 12); DM_LLM_MODEL overrides it.
DEFAULT_LLM_MODEL = "claude-opus-5-5"
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class SettingsError(RuntimeError):
    """Raised when a setting has an invalid value. Never contains secret values."""


class Settings(BaseModel):
    """Validated, immutable runtime settings."""

    model_config = ConfigDict(frozen=True)

    app_name: str = "Enterprise Decision Intelligence & Institutional Memory Agent"
    db_path: Path = REPO_ROOT / "data" / "demo.db"
    fixtures_dir: Path = REPO_ROOT / "data" / "fixtures"
    llm_mode: LlmMode = "offline"
    anthropic_api_key: SecretStr | None = None
    llm_model: Annotated[str, StringConstraints(min_length=1, pattern=r"^[a-z0-9][a-z0-9.-]*$")] = (
        DEFAULT_LLM_MODEL
    )
    log_level: LogLevel = "INFO"

    @property
    def effective_llm_mode(self) -> LlmMode:
        """The mode actually used: `anthropic` needs both the flag and a key."""
        if self.llm_mode == "anthropic" and self.anthropic_api_key is not None:
            return "anthropic"
        return "offline"

    @property
    def llm_fallback_reason(self) -> str | None:
        """Why the app fell back to offline mode, or None if no fallback happened."""
        if self.llm_mode == "anthropic" and self.anthropic_api_key is None:
            return "LLM_MODE=anthropic was requested but ANTHROPIC_API_KEY is not set."
        return None


def read_env_file(path: Path) -> dict[str, str]:
    """Parse simple KEY=VALUE lines. Missing file yields an empty dict."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_settings(
    env: Mapping[str, str] | None = None,
    env_file: Path | None = DEFAULT_ENV_FILE,
) -> Settings:
    """Build `Settings`. Real environment variables win over the `.env` file."""
    merged: dict[str, str] = {}
    if env_file is not None:
        merged.update(read_env_file(env_file))
    merged.update(os.environ if env is None else env)

    def get(name: str) -> str | None:
        value = merged.get(name, "").strip()
        return value or None

    fields: dict[str, object] = {}
    if (mode := get("LLM_MODE")) is not None:
        fields["llm_mode"] = mode.lower()
    if (key := get("ANTHROPIC_API_KEY")) is not None:
        fields["anthropic_api_key"] = key
    if (model := get("DM_LLM_MODEL")) is not None:
        fields["llm_model"] = model
    if (level := get("DM_LOG_LEVEL")) is not None:
        fields["log_level"] = level.upper()
    if (db := get("DM_DB_PATH")) is not None:
        db_path = Path(db).expanduser()
        fields["db_path"] = db_path if db_path.is_absolute() else REPO_ROOT / db_path

    try:
        return Settings(**fields)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise SettingsError(f"Invalid settings ({problems}). See .env.example.") from None
