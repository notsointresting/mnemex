"""Local, configurable embedding providers and deferred update support.

This module deliberately has no model downloads or cloud providers.  The
built-in ``hash`` provider is deterministic and useful for the optional vector
retrieval path; selecting ``none`` keeps retrieval in its BM25-only mode.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from mnemex.retrieval import ensure_embeddings
from mnemex.storage import Storage

__all__ = [
    "DEFAULT_PROVIDER_CONFIG",
    "EmbeddingProvider",
    "Tokenizer",
    "Char4Tokenizer",
    "HashEmbeddingProvider",
    "EmbeddingProviderRegistry",
    "create_embedding_provider",
    "EmbeddingFlushResult",
    "MemoryEmbeddingUpdateQueue",
]

DEFAULT_PROVIDER_CONFIG = "none"
_DEFAULT_DIMENSIONS = 384
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


@runtime_checkable
class EmbeddingProvider(Protocol):
    """A local text-to-vector provider usable by :mod:`mnemex.retrieval`."""

    @property
    def dimensions(self) -> int:
        """The number of floats returned by :meth:`embed`."""

    def embed(self, text: str) -> Sequence[float]:
        """Return a deterministic embedding for ``text``."""


@runtime_checkable
class Tokenizer(Protocol):
    """Minimal model-specific token counting interface."""

    def count(self, text: str) -> int:
        """Return the token count for ``text``."""


@dataclass(frozen=True, slots=True)
class Char4Tokenizer:
    """Conservative dependency-free token counter (four characters per token)."""

    characters_per_token: int = 4

    def __post_init__(self) -> None:
        if self.characters_per_token <= 0:
            raise ValueError("characters_per_token must be greater than zero")

    def count(self, text: str) -> int:
        return (len(text) + self.characters_per_token - 1) // self.characters_per_token


@dataclass(frozen=True, slots=True)
class HashEmbeddingProvider:
    """Deterministic, local feature-hashing embedding provider.

    The provider hashes normalized word tokens with SHA-256, so its output is
    stable across Python processes and operating systems.  ``dimensions`` is
    configurable, although mnemex's current sqlite-vec schema accepts 384
    dimensions when this provider is passed to retrieval.
    """

    dimensions: int = _DEFAULT_DIMENSIONS

    def __post_init__(self) -> None:
        if not isinstance(self.dimensions, int) or isinstance(self.dimensions, bool):
            raise TypeError("dimensions must be an integer")
        if self.dimensions <= 0:
            raise ValueError("dimensions must be greater than zero")

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimensions
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[index] += sign

        magnitude = math.sqrt(sum(component * component for component in vector))
        if magnitude:
            return [component / magnitude for component in vector]
        return vector

    def __call__(self, text: str) -> Sequence[float]:
        return self.embed(text)


ProviderFactory = Callable[[dict[str, str]], EmbeddingProvider]


class EmbeddingProviderRegistry:
    """Registry for explicitly configured local embedding providers."""

    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}

    def register(self, name: str, factory: ProviderFactory, *, replace: bool = False) -> None:
        normalized = _normalize_name(name)
        if not callable(factory):
            raise TypeError("factory must be callable")
        if normalized in self._factories and not replace:
            raise ValueError(f"embedding provider already registered: {normalized}")
        self._factories[normalized] = factory

    def create(self, config: str) -> EmbeddingProvider | None:
        name, options = _parse_provider_config(config)
        if name in {"none", "off", "disabled"}:
            if options:
                raise ValueError("the none embedding provider accepts no options")
            return None
        try:
            factory = self._factories[name]
        except KeyError as exc:
            available = ", ".join(sorted((*self._factories, "none")))
            raise ValueError(
                f"unknown embedding provider {name!r}; available: {available}"
            ) from exc
        return factory(options)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


def _hash_provider_factory(options: dict[str, str]) -> EmbeddingProvider:
    unknown = set(options) - {"dimensions"}
    if unknown:
        raise ValueError(f"unsupported hash provider options: {', '.join(sorted(unknown))}")
    dimensions = options.get("dimensions", str(_DEFAULT_DIMENSIONS))
    try:
        return HashEmbeddingProvider(dimensions=int(dimensions))
    except ValueError as exc:
        raise ValueError("hash dimensions must be an integer") from exc


def _default_registry() -> EmbeddingProviderRegistry:
    registry = EmbeddingProviderRegistry()
    registry.register("hash", _hash_provider_factory)
    return registry


def create_embedding_provider(
    config: str = DEFAULT_PROVIDER_CONFIG,
    *,
    registry: EmbeddingProviderRegistry | None = None,
) -> EmbeddingProvider | None:
    """Create a provider from ``none``, ``hash``, or ``hash:384`` syntax.

    Query-style options are also accepted (for example ``hash?dimensions=384``)
    so custom providers can grow their configuration without a config-module
    dependency.  No config value implicitly selects a networked provider.
    """

    return (registry or _DEFAULT_REGISTRY).create(config)


def _normalize_name(name: str) -> str:
    if not isinstance(name, str):
        raise TypeError("provider name must be a string")
    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("provider name must be non-empty")
    return normalized


def _parse_provider_config(config: str) -> tuple[str, dict[str, str]]:
    if not isinstance(config, str):
        raise TypeError("embedding provider config must be a string")
    raw = config.strip()
    if not raw:
        raise ValueError("embedding provider config must be non-empty")

    name_part, separator, option_part = raw.partition("?")
    if not separator and ":" in raw:
        name_part, option_part = raw.split(":", 1)
        option_part = f"dimensions={option_part}"
    name = _normalize_name(name_part)
    options: dict[str, str] = {}
    if option_part:
        for pair in option_part.split("&"):
            key, equals, value = pair.partition("=")
            if not equals or not key.strip() or not value.strip():
                raise ValueError("provider options must use key=value syntax")
            normalized_key = key.strip().lower()
            if normalized_key in options:
                raise ValueError(f"duplicate provider option: {normalized_key}")
            options[normalized_key] = value.strip()
    return name, options


_DEFAULT_REGISTRY = _default_registry()


@dataclass(frozen=True, slots=True)
class EmbeddingFlushResult:
    """The observable result of flushing deferred memory embedding updates."""

    embedded_count: int
    processed_memory_ids: tuple[str, ...]
    missing_memory_ids: tuple[str, ...]
    deferred_memory_ids: tuple[str, ...]


class MemoryEmbeddingUpdateQueue:
    """Coalesce memory IDs and populate vectors after the write path completes.

    The queue does not duplicate retrieval's storage logic.  A flush groups
    queued memories by scope and invokes :func:`ensure_embeddings` for every
    selected scope.  IDs outside the requested scopes remain queued for a
    later flush; IDs deleted before flush are reported and discarded.
    """

    def __init__(self, provider: EmbeddingProvider) -> None:
        if not isinstance(provider, EmbeddingProvider):
            raise TypeError("provider must implement EmbeddingProvider")
        self._provider = provider
        self._pending: dict[str, None] = {}

    @property
    def pending_memory_ids(self) -> tuple[str, ...]:
        return tuple(self._pending)

    def enqueue(self, memory_id: str) -> None:
        if not isinstance(memory_id, str) or not memory_id.strip():
            raise ValueError("memory_id must be a non-empty string")
        self._pending.setdefault(memory_id, None)

    def flush(
        self,
        storage: Storage,
        *,
        scopes: Collection[str] = ("project-shared",),
    ) -> EmbeddingFlushResult:
        """Flush queued IDs visible in ``scopes`` using retrieval's API."""

        scope_values = _normalize_scopes(scopes)
        selected: list[str] = []
        missing: list[str] = []
        deferred: list[str] = []
        selected_scopes: set[str] = set()
        for memory_id in self._pending:
            memory = storage.get_memory(memory_id)
            if memory is None:
                missing.append(memory_id)
            elif memory.scope in scope_values:
                selected.append(memory_id)
                selected_scopes.add(memory.scope)
            else:
                deferred.append(memory_id)

        embedded_count = 0
        for scope in sorted(selected_scopes):
            embedded_count += ensure_embeddings(
                storage, self._provider.embed, scopes=(scope,)
            )

        for memory_id in (*selected, *missing):
            self._pending.pop(memory_id, None)
        return EmbeddingFlushResult(
            embedded_count=embedded_count,
            processed_memory_ids=tuple(selected),
            missing_memory_ids=tuple(missing),
            deferred_memory_ids=tuple(deferred),
        )


def _normalize_scopes(scopes: Collection[str]) -> tuple[str, ...]:
    if isinstance(scopes, (str, bytes)):
        raise ValueError("scopes must be a non-empty collection")
    values = tuple(scopes)
    if not values:
        raise ValueError("scopes must be a non-empty collection")
    for scope in values:
        Storage._validate_scope(scope)
    return tuple(sorted(set(values)))
