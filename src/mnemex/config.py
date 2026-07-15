"""Configuration for optional mnemex integrations.

The semantic judge is deliberately opt-in.  Possessing an OpenAI API key alone
never enables it, so a normal local mnemex process neither imports the SDK nor
makes a network request.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["MnemexConfig"]


@dataclass(frozen=True, slots=True)
class MnemexConfig:
    """Runtime settings shared by optional integrations.

    ``semantic_judge_enabled`` must be set explicitly through a constructor,
    CLI adapter, or ``MNEMEX_SEMANTIC_JUDGE_ENABLED=true``.  This prevents an
    ambient ``OPENAI_API_KEY`` from changing local-first behavior.
    """

    semantic_judge_enabled: bool = False
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.6"
    openai_timeout_seconds: float = 15.0
    max_evidence_tokens: int = 1_200
    embedding_provider: str = "none"

    def __post_init__(self) -> None:
        if not isinstance(self.semantic_judge_enabled, bool):
            raise TypeError("semantic_judge_enabled must be a bool")
        if self.openai_api_key is not None and not self.openai_api_key.strip():
            raise ValueError("openai_api_key must be non-empty when provided")
        if not isinstance(self.openai_model, str) or not self.openai_model.strip():
            raise ValueError("openai_model must be a non-empty string")
        if self.openai_timeout_seconds <= 0:
            raise ValueError("openai_timeout_seconds must be greater than zero")
        if self.max_evidence_tokens <= 0:
            raise ValueError("max_evidence_tokens must be greater than zero")
        if not isinstance(self.embedding_provider, str) or not self.embedding_provider.strip():
            raise ValueError("embedding_provider must be a non-empty string")

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "MnemexConfig":
        """Load environment/CLI-compatible fields without enabling by default.

        CLI frontends may populate the same ``MNEMEX_*`` variables or pass the
        corresponding dataclass fields directly.  ``OPENAI_API_KEY`` is used
        only as a credential fallback after explicit enablement.
        """
        values = os.environ if environ is None else environ
        enabled = _parse_bool(values.get("MNEMEX_SEMANTIC_JUDGE_ENABLED"), False)
        api_key = values.get("MNEMEX_OPENAI_API_KEY") or values.get("OPENAI_API_KEY")
        api_key = api_key.strip() or None if api_key else None
        model = values.get("MNEMEX_OPENAI_MODEL", "gpt-5.6")
        timeout = _parse_positive_float(
            values.get("MNEMEX_OPENAI_TIMEOUT_SECONDS"), 15.0,
            "MNEMEX_OPENAI_TIMEOUT_SECONDS",
        )
        evidence_tokens = _parse_positive_int(
            values.get("MNEMEX_MAX_EVIDENCE_TOKENS"), 1_200,
            "MNEMEX_MAX_EVIDENCE_TOKENS",
        )
        embedding_provider = values.get("MNEMEX_EMBEDDING_PROVIDER", "none")
        return cls(
            semantic_judge_enabled=enabled,
            openai_api_key=api_key,
            openai_model=model,
            openai_timeout_seconds=timeout,
            max_evidence_tokens=evidence_tokens,
            embedding_provider=embedding_provider,
        )


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("MNEMEX_SEMANTIC_JUDGE_ENABLED must be a boolean")


def _parse_positive_float(value: str | None, default: float, name: str) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return parsed


def _parse_positive_int(value: str | None, default: int, name: str) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return parsed
