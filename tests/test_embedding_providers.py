"""Focused tests for local embedding providers and deferred update flushing."""

from __future__ import annotations

import pytest

from mnemex.anchors import remember
from mnemex.embedding_providers import (
    Char4Tokenizer,
    EmbeddingProviderRegistry,
    HashEmbeddingProvider,
    MemoryEmbeddingUpdateQueue,
    create_embedding_provider,
)
from mnemex.storage import Storage


with Storage() as _probe:
    VEC_AVAILABLE = _probe.vec_available
_needs_vec = pytest.mark.skipif(
    not VEC_AVAILABLE, reason="sqlite-vec extension unavailable (no-ML mode)"
)


def test_hash_provider_is_deterministic_and_configurable() -> None:
    provider = HashEmbeddingProvider(dimensions=8)

    assert provider.embed("alpha beta alpha") == provider.embed("alpha beta alpha")
    assert len(provider.embed("alpha")) == 8
    assert provider.embed("   ") == [0.0] * 8
    assert provider("alpha") == provider.embed("alpha")


@pytest.mark.parametrize("dimensions", [0, -1])
def test_hash_provider_rejects_non_positive_dimensions(dimensions: int) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        HashEmbeddingProvider(dimensions=dimensions)


def test_provider_factory_defaults_to_no_ml_and_parses_hash_dimensions() -> None:
    assert create_embedding_provider() is None
    assert create_embedding_provider("none") is None

    colon_provider = create_embedding_provider("hash:16")
    query_provider = create_embedding_provider("hash?dimensions=16")
    assert isinstance(colon_provider, HashEmbeddingProvider)
    assert colon_provider.dimensions == 16
    assert query_provider == colon_provider

    with pytest.raises(ValueError, match="unknown embedding provider"):
        create_embedding_provider("cloud")
    with pytest.raises(ValueError, match="unsupported hash"):
        create_embedding_provider("hash?model=local")


def test_registry_allows_injected_local_provider_factories() -> None:
    registry = EmbeddingProviderRegistry()
    registry.register(
        "tiny", lambda options: HashEmbeddingProvider(int(options["d"]))
    )

    provider = create_embedding_provider("tiny?d=3", registry=registry)
    assert isinstance(provider, HashEmbeddingProvider)
    assert provider.dimensions == 3


def test_char4_tokenizer_is_default_style_and_injectable_contract() -> None:
    tokenizer = Char4Tokenizer()
    assert tokenizer.count("") == 0
    assert tokenizer.count("abcde") == 2
    assert Char4Tokenizer(characters_per_token=2).count("abcde") == 3


@_needs_vec
def test_update_queue_coalesces_ids_and_flushes_only_selected_scopes() -> None:
    with Storage() as storage:
        shared = remember(storage, "local retrieval setting", memory_id="shared")
        private = remember(
            storage,
            "private local retrieval setting",
            memory_id="private",
            scope="agent-private",
        )
        queue = MemoryEmbeddingUpdateQueue(HashEmbeddingProvider())
        queue.enqueue(shared.id)
        queue.enqueue(shared.id)
        queue.enqueue(private.id)

        first = queue.flush(storage, scopes=("project-shared",))
        assert first.processed_memory_ids == (shared.id,)
        assert first.deferred_memory_ids == (private.id,)
        assert first.missing_memory_ids == ()
        assert first.embedded_count == 1
        assert queue.pending_memory_ids == (private.id,)

        second = queue.flush(storage, scopes=("agent-private",))
        assert second.processed_memory_ids == (private.id,)
        assert second.embedded_count == 1
        assert queue.pending_memory_ids == ()


@_needs_vec
def test_update_queue_reports_deleted_memory_and_keeps_queue_empty() -> None:
    with Storage() as storage:
        queue = MemoryEmbeddingUpdateQueue(HashEmbeddingProvider())
        queue.enqueue("gone")

        result = queue.flush(storage)
        assert result.missing_memory_ids == ("gone",)
        assert result.processed_memory_ids == ()
        assert queue.pending_memory_ids == ()
