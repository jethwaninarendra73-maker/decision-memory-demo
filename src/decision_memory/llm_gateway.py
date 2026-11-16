"""Optional LLM access. Offline by default; Anthropic only when asked for and configured.

`make_gateway(settings)` returns an `OfflineGateway` unless `LLM_MODE=anthropic`, an
`ANTHROPIC_API_KEY` is set, and the optional `anthropic` package is installed. Whatever
the reason for staying offline, it comes back as a notice the UI shows.

The Anthropic gateway makes one structured-output request per call and turns every
failure (missing package, timeout, network, rate limit, rejected key, refusal, cut-off
or malformed output) into an `LlmError` with a short, key-free message. Callers fall
back to their deterministic path on any `LlmError`. Nothing here logs prompts,
responses or keys: only the mode, the outcome and the error class.

The app works fully without this module ever reaching the network, and the tests use
fake clients only.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import time
from typing import Any, Literal, Protocol

from pydantic import SecretStr

from .config import DEFAULT_LLM_MODEL, Settings

logger = logging.getLogger(__name__)

DEFAULT_MODEL = DEFAULT_LLM_MODEL  # DM_LLM_MODEL overrides it
# Server-side fallback: if the model declines, the API retries on its recommended
# fallback model inside the same call (beta).
FALLBACK_BETA = "server-side-fallback-2026-07-01"
REQUEST_TIMEOUT_S = 30.0
MAX_RETRIES = 1
EFFORT = "low"  # a short rewording task

MISSING_PACKAGE_NOTICE = (
    "LLM_MODE=anthropic was requested but the optional `anthropic` package is not "
    "installed (pip install -r requirements-optional.txt)."
)


class LlmError(Exception):
    """The model call failed or its output is unusable. The message is safe to show."""


class Gateway(Protocol):
    mode: Literal["offline", "anthropic"]
    model: str | None

    def complete_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        """Return the model's JSON answer, or raise `LlmError`."""
        ...


class OfflineGateway:
    """The default: no model is ever called."""

    mode: Literal["offline", "anthropic"] = "offline"
    model: str | None = None

    def complete_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        raise LlmError("Offline mode: no language model is called.")


def _error_message(exc: Exception) -> str:
    """A short description by error class. Never the exception text, which could echo
    request details."""
    try:
        import anthropic
    except ImportError:
        return "The Anthropic request failed."

    known: list[tuple[type[Exception], str]] = [
        (anthropic.APITimeoutError, "The Anthropic request timed out."),
        (anthropic.APIConnectionError, "The Anthropic API could not be reached."),
        (anthropic.AuthenticationError, "The Anthropic API rejected the key."),
        (anthropic.RateLimitError, "The Anthropic API is rate limiting requests."),
        (anthropic.BadRequestError, "The Anthropic API rejected the request."),
        (anthropic.APIStatusError, "The Anthropic API returned an error."),
    ]
    for error_class, message in known:
        if isinstance(exc, error_class):
            return message
    return "The Anthropic request failed."


class AnthropicGateway:
    """One structured-output request per call through the official SDK."""

    mode: Literal["offline", "anthropic"] = "anthropic"

    def __init__(
        self,
        api_key: SecretStr,
        model: str = DEFAULT_MODEL,
        *,
        timeout: float = REQUEST_TIMEOUT_S,
        client: Any = None,
    ) -> None:
        self.model = model
        self.last_model: str | None = None  # the model that served the last answer
        self._api_key = api_key
        self._timeout = timeout
        self._client = client  # tests pass a fake; otherwise built on first use

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(
                api_key=self._api_key.get_secret_value(),
                timeout=self._timeout,
                max_retries=MAX_RETRIES,
            )
        return self._client

    def complete_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        started = time.monotonic()
        try:
            response = self._get_client().beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "effort": EFFORT,
                    "format": {"type": "json_schema", "schema": schema},
                },
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        # Any failure must fall back to the offline path (CLAUDE.md), so catch broadly and
        # map the class to a message below.
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM call failed: %s", type(exc).__name__)
            raise LlmError(_error_message(exc)) from None
        elapsed = time.monotonic() - started
        served = getattr(response, "model", None)
        self.last_model = served if isinstance(served, str) and served else self.model
        stop = getattr(response, "stop_reason", None)
        logger.info("LLM call finished: stop_reason=%s seconds=%.1f", stop, elapsed)
        if stop == "refusal":
            raise LlmError("The model declined the request.")
        if stop == "max_tokens":
            raise LlmError("The model's answer was cut off.")
        text = next(
            (block.text for block in response.content if getattr(block, "type", "") == "text"),
            None,
        )
        if not text:
            raise LlmError("The model returned no text.")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise LlmError("The model's answer was not valid JSON.") from None
        if not isinstance(data, dict):
            raise LlmError("The model's answer was not a JSON object.")
        return data


def anthropic_installed() -> bool:
    return importlib.util.find_spec("anthropic") is not None


def make_gateway(settings: Settings) -> tuple[Gateway, str | None]:
    """The gateway to use and, when the app stays offline against a request, why."""
    if settings.effective_llm_mode != "anthropic" or settings.anthropic_api_key is None:
        return OfflineGateway(), settings.llm_fallback_reason
    if not anthropic_installed():
        return OfflineGateway(), MISSING_PACKAGE_NOTICE
    return AnthropicGateway(settings.anthropic_api_key, settings.llm_model), None
