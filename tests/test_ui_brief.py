"""The optional brief in the running app. Offline nothing changes; in Anthropic mode the
model is called only on request, once per card, and every failure falls back visibly.
The gateway is faked: no test reaches the network."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from decision_memory.llm_gateway import MISSING_PACKAGE_NOTICE, AnthropicGateway, OfflineGateway
from decision_memory.ui.recommendation import BRIEF_BUTTON_KEY

from conftest import rendered_text
from test_brief import GOOD

SECRET = "sk-test-NOT-A-REAL-KEY-7f3a"


class FakeMessages:
    def __init__(self, answer=None, error: Exception | None = None) -> None:
        self.answer, self.error, self.calls = answer, error, 0
        self.served_by = "claude-opus-5-5"

    def create(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            stop_reason="end_turn",
            model=self.served_by,
            content=[SimpleNamespace(type="text", text=json.dumps(self.answer))],
        )


@pytest.fixture
def anthropic_mode(monkeypatch: pytest.MonkeyPatch):
    """Run the app in Anthropic mode with a fake client; returns the fake."""

    def install(answer=GOOD, error=None) -> FakeMessages:
        messages = FakeMessages(answer, error)
        client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
        gateway = AnthropicGateway(SecretStr(SECRET), client=client)
        monkeypatch.setattr(
            "decision_memory.ui.shell.make_gateway", lambda settings: (gateway, None)
        )
        return messages

    return install


def briefs(app) -> list[str]:
    return [m.value for m in app.markdown if m.value.startswith("**Phased pilot at")]


def test_offline_mode_shows_no_brief_and_no_button(run_app) -> None:
    app = run_app()
    assert not app.exception
    assert "Plain-language brief" not in rendered_text(app)
    assert not [b for b in app.button if b.key == BRIEF_BUTTON_KEY]
    assert any(m.value == "**Mode:** `offline` (deterministic baseline)" for m in app.markdown)


def test_anthropic_mode_calls_the_model_only_on_request_and_once(run_app, anthropic_mode) -> None:
    messages = anthropic_mode()
    app = run_app()
    assert not app.exception
    assert messages.calls == 0  # nothing is sent until the button is clicked
    assert "Written by the offline template from the card's cited reasons" in rendered_text(app)
    assert any("`anthropic` (`claude-opus-5-5`" in m.value for m in app.markdown)

    app = app.button(key=BRIEF_BUTTON_KEY).click().run()
    assert messages.calls == 1
    (brief,) = briefs(app)
    assert "France's 5% surcharge worked, so a \\+5% pilot is backed\\." in brief
    assert "\n    - [\\[SRC-006\\]](?role=pricing_director&evidence=SRC-006)" in brief
    assert any(c.value.startswith("Reworded by claude-opus-5-5") for c in app.caption)

    app = app.run()  # another rerun reuses the stored brief
    assert messages.calls == 1 and briefs(app) == [brief]


def test_a_failed_call_falls_back_visibly_without_the_key(run_app, anthropic_mode) -> None:
    anthropic_mode(error=RuntimeError(f"connection reset {SECRET}"))
    app = run_app().button(key=BRIEF_BUTTON_KEY).click().run()
    assert not app.exception
    assert any(
        w.value == "The Anthropic request failed. Showing the offline brief instead."
        for w in app.warning
    )
    assert SECRET not in rendered_text(app)
    assert briefs(app)  # the offline brief is still shown


def test_a_brief_that_strays_is_replaced_by_the_offline_one(run_app, anthropic_mode) -> None:
    anthropic_mode(answer={"sentences": [{"text": "It adds $99M.", "source_ids": ["SRC-006"]}]})
    app = run_app().button(key=BRIEF_BUTTON_KEY).click().run()
    assert any(
        w.value.startswith(
            "The model's brief was not used because sentence 1 has a number that is not in "
            "the reasons it cites"
        )
        for w in app.warning
    )
    assert "99" not in "".join(briefs(app))


def test_a_stored_brief_is_not_shown_to_another_role(run_app, anthropic_mode) -> None:
    anthropic_mode()
    app = run_app().button(key=BRIEF_BUTTON_KEY).click().run()
    assert any(c.value.startswith("Reworded by") for c in app.caption)
    app.selectbox(key="demo_role").set_value("analyst")
    app = app.run()
    assert not any(c.value.startswith("Reworded by") for c in app.caption)


def test_a_missing_package_is_reported_in_the_sidebar(run_app, monkeypatch) -> None:
    monkeypatch.setattr(
        "decision_memory.ui.shell.make_gateway",
        lambda settings: (OfflineGateway(), MISSING_PACKAGE_NOTICE),
    )
    app = run_app()
    assert f"{MISSING_PACKAGE_NOTICE} Running offline." in [i.value for i in app.sidebar.info]
    assert "Plain-language brief" not in rendered_text(app)


def test_the_caption_names_a_fallback_model(run_app, anthropic_mode) -> None:
    messages = anthropic_mode()
    messages.served_by = "claude-opus-4-8"
    app = run_app().button(key=BRIEF_BUTTON_KEY).click().run()
    assert any(c.value.startswith("Reworded by claude-opus-4-8") for c in app.caption)
    assert any("do not catch a changed meaning" in c.value for c in app.caption)
