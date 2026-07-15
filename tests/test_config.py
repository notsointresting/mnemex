from __future__ import annotations

import pytest

from mnemex.config import MnemexConfig


def test_defaults_keep_semantic_judge_disabled() -> None:
    config = MnemexConfig()

    assert config.semantic_judge_enabled is False
    assert config.openai_model == "gpt-5.6"


def test_environment_key_does_not_enable_semantic_judge() -> None:
    config = MnemexConfig.from_env({"OPENAI_API_KEY": "key-present"})

    assert config.semantic_judge_enabled is False
    assert config.openai_api_key == "key-present"


def test_blank_environment_key_is_treated_as_absent() -> None:
    config = MnemexConfig.from_env({"OPENAI_API_KEY": "   "})

    assert config.openai_api_key is None


def test_from_env_loads_explicit_openai_settings() -> None:
    config = MnemexConfig.from_env({
        "MNEMEX_SEMANTIC_JUDGE_ENABLED": "true",
        "MNEMEX_OPENAI_API_KEY": "configured-key",
        "MNEMEX_OPENAI_MODEL": "test-model",
        "MNEMEX_OPENAI_TIMEOUT_SECONDS": "4.5",
        "MNEMEX_MAX_EVIDENCE_TOKENS": "321",
    })

    assert config.semantic_judge_enabled is True
    assert config.openai_api_key == "configured-key"
    assert config.openai_model == "test-model"
    assert config.openai_timeout_seconds == 4.5
    assert config.max_evidence_tokens == 321


@pytest.mark.parametrize("value", ["", "maybe", "enabled"])
def test_from_env_rejects_invalid_enablement(value: str) -> None:
    with pytest.raises(ValueError, match="MNEMEX_SEMANTIC_JUDGE_ENABLED"):
        MnemexConfig.from_env({"MNEMEX_SEMANTIC_JUDGE_ENABLED": value})


def test_config_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="max_evidence_tokens"):
        MnemexConfig(max_evidence_tokens=0)

    with pytest.raises(ValueError, match="openai_timeout_seconds"):
        MnemexConfig(openai_timeout_seconds=0)
