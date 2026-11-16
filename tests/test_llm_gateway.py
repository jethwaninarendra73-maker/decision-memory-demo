"""The optional LLM gateway: mode selection, the request it sends, and every failure
turning into a key-free `LlmError`. Fake clients only: no test calls a paid API."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from decision_memory.config import DEFAULT_LLM_MODEL, SettingsError, load_settings
from decision_memory.llm_gateway import (
    FALLBACK_BETA,
    MISSING_PACKAGE_NOTICE,
    AnthropicGateway,
    LlmError,
    OfflineGateway,
    make_gateway,
)

SECRET = "sk-test-NOT-A-REAL-KEY-7f3a"
SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


def settings(**env):
    return load_settings(env=env, env_file=None)


def text_response(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)]
    )


class FakeMessages:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response, self.error, self.calls = response, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def fake_gateway(response=None, error=None) -> tuple[AnthropicGateway, FakeMessages]:
    messages = FakeMessages(response, error)
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return AnthropicGateway(SecretStr(SECRET), client=client), messages


def call(gateway):
    return gateway.complete_json(system="sys", prompt="prompt", schema=SCHEMA, max_tokens=4000)


# --- choosing the mode -------------------------------------------------------------


def test_offline_is_the_default_and_never_calls_anything() -> None:
    gateway, notice = make_gateway(settings())
    assert isinstance(gateway, OfflineGateway) and gateway.mode == "offline"
    assert notice is None
    with pytest.raises(LlmError, match="Offline mode"):
        call(gateway)


def test_a_key_alone_does_not_switch_the_mode_on() -> None:
    gateway, notice = make_gateway(settings(ANTHROPIC_API_KEY=SECRET))
    assert gateway.mode == "offline" and notice is None


def test_anthropic_mode_without_a_key_stays_offline_with_a_notice() -> None:
    gateway, notice = make_gateway(settings(LLM_MODE="anthropic"))
    assert gateway.mode == "offline"
    assert notice == "LLM_MODE=anthropic was requested but ANTHROPIC_API_KEY is not set."


def test_anthropic_mode_without_the_package_stays_offline_with_a_notice(monkeypatch) -> None:
    monkeypatch.setattr("decision_memory.llm_gateway.anthropic_installed", lambda: False)
    gateway, notice = make_gateway(settings(LLM_MODE="anthropic", ANTHROPIC_API_KEY=SECRET))
    assert gateway.mode == "offline" and notice == MISSING_PACKAGE_NOTICE


def test_anthropic_mode_with_key_and_package(monkeypatch) -> None:
    monkeypatch.setattr("decision_memory.llm_gateway.anthropic_installed", lambda: True)
    gateway, notice = make_gateway(settings(LLM_MODE="anthropic", ANTHROPIC_API_KEY=SECRET))
    assert isinstance(gateway, AnthropicGateway) and notice is None
    assert gateway.model == DEFAULT_LLM_MODEL == "claude-opus-5-5"
    gateway, _ = make_gateway(
        settings(LLM_MODE="anthropic", ANTHROPIC_API_KEY=SECRET, DM_LLM_MODEL="claude-sonnet-5-5")
    )
    assert gateway.model == "claude-sonnet-5-5"


def test_a_malformed_model_setting_is_refused_without_echoing_the_key() -> None:
    with pytest.raises(SettingsError) as refused:
        settings(DM_LLM_MODEL="Claude Opus!", ANTHROPIC_API_KEY=SECRET)
    assert SECRET not in str(refused.value)


# --- the request -------------------------------------------------------------------


def test_the_request_is_one_structured_output_call_with_server_side_fallback() -> None:
    gateway, messages = fake_gateway(text_response('{"ok": true}'))
    assert call(gateway) == {"ok": True}
    (kwargs,) = messages.calls
    assert kwargs == {
        "model": "claude-opus-5-5",
        "max_tokens": 4000,
        "system": "sys",
        "messages": [{"role": "user", "content": "prompt"}],
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }


# --- failures ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (text_response("{}", stop_reason="refusal"), "The model declined the request."),
        (text_response('{"a": ', stop_reason="max_tokens"), "The model's answer was cut off."),
        (text_response("not json"), "The model's answer was not valid JSON."),
        (text_response("[1, 2]"), "The model's answer was not a JSON object."),
        (SimpleNamespace(stop_reason="end_turn", content=[]), "The model returned no text."),
    ],
)
def test_unusable_answers_raise(response, message: str) -> None:
    gateway, _ = fake_gateway(response)
    with pytest.raises(LlmError) as raised:
        call(gateway)
    assert str(raised.value) == message


def test_a_failing_client_never_leaks_the_key_or_the_prompt(caplog) -> None:
    gateway, _ = fake_gateway(error=RuntimeError(f"boom {SECRET} prompt-text"))
    with caplog.at_level(logging.DEBUG), pytest.raises(LlmError) as raised:
        gateway.complete_json(system="sys", prompt="prompt-text", schema=SCHEMA, max_tokens=10)
    assert str(raised.value) == "The Anthropic request failed."
    logged = caplog.text
    assert SECRET not in logged and "prompt-text" not in logged
    assert "RuntimeError" in logged


def test_sdk_error_classes_map_to_plain_messages() -> None:
    anthropic = pytest.importorskip("anthropic")  # skipped when the optional package is absent
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    for error, message in [
        (anthropic.APITimeoutError(request=request), "The Anthropic request timed out."),
        (anthropic.APIConnectionError(request=request), "The Anthropic API could not be reached."),
    ]:
        gateway, _ = fake_gateway(error=error)
        with pytest.raises(LlmError) as raised:
            call(gateway)
        assert str(raised.value) == message


def test_the_key_is_kept_secret_in_settings() -> None:
    loaded = settings(LLM_MODE="anthropic", ANTHROPIC_API_KEY=SECRET)
    assert SECRET not in repr(loaded) and SECRET not in str(loaded.model_dump())
    assert json.dumps(loaded.model_dump(mode="json")).count(SECRET) == 0


def test_the_gateway_records_the_model_that_served_the_answer() -> None:
    served = text_response('{"ok": true}')
    served.model = "claude-opus-4-8"
    gateway, _ = fake_gateway(served)
    assert gateway.last_model is None
    call(gateway)
    assert gateway.last_model == "claude-opus-4-8"
    gateway, _ = fake_gateway(text_response("{}"))  # no model on the response
    call(gateway)
    assert gateway.last_model == "claude-opus-5-5"
