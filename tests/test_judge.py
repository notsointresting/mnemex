from __future__ import annotations

import json
import sys
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from mnemex.config import MnemexConfig
from mnemex.judge import (
    OpenAIResponsesJudge,
    SemanticJudgment,
    Verdict,
    create_semantic_judge,
)


class FakeResponses:
    def __init__(self, response: object | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeClient:
    def __init__(self, response: object | Exception) -> None:
        self.responses = FakeResponses(response)


def _config(**overrides: object) -> MnemexConfig:
    values: dict[str, object] = {
        "semantic_judge_enabled": True,
        "openai_api_key": "test-key",
        "openai_timeout_seconds": 7.0,
        "max_evidence_tokens": 3,
    }
    values.update(overrides)
    return MnemexConfig(**values)


def _response(**payload: object) -> object:
    base: dict[str, object] = {
        "verdict": "contradiction",
        "confidence": 0.95,
        "explanation": "The new behavior conflicts with the recorded decision.",
        "evidence_ids": ["memory-1"],
    }
    base.update(payload)
    return SimpleNamespace(output_text=json.dumps(base))


def test_local_mode_creates_no_provider_or_openai_import() -> None:
    sys.modules.pop("openai", None)

    judge = create_semantic_judge(MnemexConfig())

    assert judge is None
    assert "openai" not in sys.modules


def test_request_uses_strict_schema_and_bounded_evidence() -> None:
    client = FakeClient(_response())
    judge = OpenAIResponsesJudge(_config(), client=client)

    result = judge.evaluate("one two three four")

    assert result.verdict is Verdict.CONTRADICTION
    assert result.blocks_change is True
    assert result.payload_tokens == 3
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.6"
    assert call["timeout"] == 7.0
    assert call["text"] == {
        "format": {
            "type": "json_schema",
            "name": "mnemex_semantic_judgment",
            "strict": True,
            "schema": call["text"]["format"]["schema"],
        }
    }
    user_message = call["input"][1]
    assert user_message["content"][0]["text"] == "one two three"


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(output_text="not json"),
        _response(verdict="block"),
        _response(confidence=1.5),
        _response(extra="unexpected"),
    ],
)
def test_malformed_model_response_is_uncertain(response: object) -> None:
    result = OpenAIResponsesJudge(_config(), client=FakeClient(response)).evaluate("safe")

    assert result.verdict is Verdict.UNCERTAIN
    assert result.blocks_change is False


def test_provider_failure_and_missing_credentials_are_unavailable() -> None:
    failed = OpenAIResponsesJudge(_config(), client=FakeClient(TimeoutError())).evaluate("safe")
    no_credentials = OpenAIResponsesJudge(
        _config(openai_api_key=None), client=None
    ).evaluate("safe")

    assert failed.verdict is Verdict.UNAVAILABLE
    assert no_credentials.verdict is Verdict.UNAVAILABLE
    assert not failed.blocks_change
    assert not no_credentials.blocks_change


def test_missing_credentials_override_an_injected_client() -> None:
    client = FakeClient(_response())

    result = OpenAIResponsesJudge(
        _config(openai_api_key=None), client=client
    ).evaluate("safe")

    assert result.verdict is Verdict.UNAVAILABLE
    assert client.responses.calls == []


def test_empty_or_refused_output_is_unavailable() -> None:
    result = OpenAIResponsesJudge(
        _config(), client=FakeClient(SimpleNamespace(output_text=None))
    ).evaluate("safe")

    assert result.verdict is Verdict.UNAVAILABLE


def test_semantic_judgment_is_immutable() -> None:
    judgment = SemanticJudgment(Verdict.COMPATIBLE, 1.0, "Compatible")

    with pytest.raises(FrozenInstanceError):
        judgment.verdict = Verdict.UNCERTAIN  # type: ignore[misc]
