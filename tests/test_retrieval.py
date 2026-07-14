"""Phase 2 retrieval tests.

Uses a small deterministic synthetic embedder (no ML dependency): tokens are
hashed into a fixed 384-dim space so memories that share words with the query
land near it, which is enough to exercise the vector path deterministically.
"""

from __future__ import annotations

import hashlib

import pytest

from mnemex.anchors import remember
from mnemex.retrieval import (
    RecallResult,
    bm25_candidates,
    ensure_embeddings,
    estimate_tokens,
    recall,
    rrf_fuse,
    vector_candidates,
)
from mnemex.storage import Memory, Storage

_DIM = 384

with Storage() as _probe:
    VEC_AVAILABLE = _probe.vec_available
_needs_vec = pytest.mark.skipif(
    not VEC_AVAILABLE, reason="sqlite-vec extension unavailable (no-ML mode)"
)


def synthetic_embedder(text: str) -> list[float]:
    """Deterministic, ML-free bag-of-hashed-words embedding."""

    vector = [0.0] * _DIM
    for token in text.lower().split():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % _DIM
        vector[index] += 1.0
    return vector


def wrong_dim_embedder(text: str) -> list[float]:
    return [0.0] * 10


def add(
    storage: Storage,
    memory_id: str,
    content: str,
    *,
    scope: str = "project-shared",
    rationale: str = "",
    tags: str = "",
) -> Memory:
    return remember(
        storage,
        content,
        memory_id=memory_id,
        scope=scope,
        rationale=rationale,
        tags=tags,
    )


def make_memory(memory_id: str, content: str) -> Memory:
    return Memory(
        id=memory_id,
        type="decision",
        content=content,
        rationale="",
        anchor_node_id=None,
        anchor_hash=None,
        scope="project-shared",
        source="test",
        confidence=1.0,
        importance=1.0,
        created_at="2026-07-14T08:00:00Z",
        last_accessed="2026-07-14T08:00:00Z",
        last_verified="2026-07-14T08:00:00Z",
        tags="",
    )


def _cost(memory: Memory) -> int:
    combined = memory.content if not memory.rationale else (
        f"{memory.content}\n{memory.rationale}"
    )
    return estimate_tokens(combined)


# --------------------------------------------------------------------------- #
# estimate_tokens
# --------------------------------------------------------------------------- #


def test_estimate_tokens_is_deterministic_monotonic_and_positive() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") >= 1
    assert estimate_tokens("abc") == estimate_tokens("abc")

    previous = 0
    for length in range(0, 200):
        current = estimate_tokens("x" * length)
        assert current >= previous  # monotonic non-decreasing in length
        if length >= 1:
            assert current >= 1
        previous = current


# --------------------------------------------------------------------------- #
# BM25 / no-ML mode
# --------------------------------------------------------------------------- #


def test_no_ml_recall_returns_sane_bm25_order() -> None:
    with Storage() as storage:
        strong = add(
            storage,
            "strong",
            "authentication cookies authentication cookies session",
        )
        weak = add(storage, "weak", "authentication guide")
        add(storage, "unrelated", "database schema migration")

        result = recall(storage, "authentication cookies")

        assert result.mode == "bm25-only"
        assert [sm.memory.id for sm in result.included] == [strong.id, weak.id]
        assert all(sm.signals == ("bm25",) for sm in result.included)
        assert result.dropped == ()
        assert result.budget_tokens is None


def test_bm25_candidates_are_scope_filtered_and_ranked() -> None:
    with Storage() as storage:
        add(storage, "pub", "authentication cookies session")
        add(storage, "priv", "authentication cookies session",
            scope="agent-private")

        shared = bm25_candidates(
            storage, "authentication", scopes=("project-shared",), limit=10
        )
        assert [memory.id for memory, _ in shared] == ["pub"]
        assert [rank for _, rank in shared] == [1]

        private = bm25_candidates(
            storage, "authentication", scopes=("agent-private",), limit=10
        )
        assert [memory.id for memory, _ in private] == ["priv"]


@_needs_vec
def test_determinism_same_query_twice_identical_order() -> None:
    with Storage() as storage:
        add(storage, "a", "alpha beta gamma alpha")
        add(storage, "b", "alpha beta")
        add(storage, "c", "gamma delta alpha")

        def signature(result: RecallResult) -> list[tuple[str, float, int, tuple[str, ...]]]:
            return [
                (sm.memory.id, sm.score, sm.rank, sm.signals)
                for sm in result.included
            ]

        first = recall(storage, "alpha gamma")
        second = recall(storage, "alpha gamma")
        assert signature(first) == signature(second)

        # Hybrid mode is deterministic too (idempotent embeddings + fixed embedder).
        hybrid_first = recall(storage, "alpha gamma", embedder=synthetic_embedder)
        hybrid_second = recall(storage, "alpha gamma", embedder=synthetic_embedder)
        assert signature(hybrid_first) == signature(hybrid_second)
        assert hybrid_first.mode == "hybrid"


# --------------------------------------------------------------------------- #
# Token governor
# --------------------------------------------------------------------------- #


def test_token_cap_never_exceeded_including_oversized_single_memory() -> None:
    with Storage() as storage:
        add(storage, "s1", "widget aa")          # short, contains query term
        add(storage, "s2", "widget bb")
        add(storage, "s3", "widget cc")
        oversized_content = "widget " + ("z" * 400)
        big = add(storage, "big", oversized_content)  # cost > any small cap

        assert _cost(big) > 5

        for cap in (0, 1, 4, 5, 100, 10_000):
            result = recall(storage, "widget", max_tokens=cap)

            included_cost = sum(_cost(sm.memory) for sm in result.included)
            assert included_cost <= cap
            assert result.used_tokens == included_cost
            assert result.used_tokens <= cap
            assert result.budget_tokens == cap
            # No memory that individually exceeds the cap is ever included.
            assert all(_cost(sm.memory) <= cap for sm in result.included)

            included_ids = {sm.memory.id for sm in result.included}
            dropped_ids = {sm.memory.id for sm in result.dropped}
            assert included_ids.isdisjoint(dropped_ids)

            if cap < _cost(big):
                assert big.id in dropped_ids
                assert big.id not in included_ids

        # The oversized memory is dropped intact, never truncated.
        capped = recall(storage, "widget", max_tokens=5)
        dropped_big = next(
            sm for sm in capped.dropped if sm.memory.id == big.id
        )
        assert dropped_big.memory.content == oversized_content


def test_no_cap_includes_everything_and_reports_total() -> None:
    with Storage() as storage:
        add(storage, "a", "widget alpha")
        add(storage, "b", "widget beta")

        result = recall(storage, "widget", max_tokens=None)
        assert result.dropped == ()
        assert result.used_tokens == sum(_cost(sm.memory) for sm in result.included)


# --------------------------------------------------------------------------- #
# Scope isolation (BM25 + vector)
# --------------------------------------------------------------------------- #


@_needs_vec
def test_scope_isolation_holds_across_bm25_and_vector_paths() -> None:
    with Storage() as storage:
        pub = add(storage, "pub", "vector alpha beta gamma")
        priv = add(storage, "priv", "vector alpha beta gamma",
                   scope="agent-private")

        # Populate embeddings for BOTH scopes so the private memory really has a
        # vector row -- proving the rowid filter (not a missing embedding) is
        # what keeps it out of project-shared results.
        ensure_embeddings(
            storage,
            synthetic_embedder,
            scopes=("agent-private", "project-shared", "user-global"),
        )

        result = recall(
            storage,
            "vector alpha",
            scopes=("project-shared",),
            embedder=synthetic_embedder,
        )
        surfaced = {
            sm.memory.id for sm in (*result.included, *result.dropped)
        }
        assert priv.id not in surfaced
        assert pub.id in surfaced
        assert "vector" in next(
            sm.signals for sm in result.included if sm.memory.id == pub.id
        )

        query_vec = synthetic_embedder("vector alpha")
        shared_vec = vector_candidates(
            storage, query_vec, scopes=("project-shared",), limit=10
        )
        assert [memory.id for memory, _ in shared_vec] == ["pub"]

        # The filter works both ways: the private scope can see its own row.
        private_vec = vector_candidates(
            storage, query_vec, scopes=("agent-private",), limit=10
        )
        assert [memory.id for memory, _ in private_vec] == ["priv"]


# --------------------------------------------------------------------------- #
# ensure_embeddings
# --------------------------------------------------------------------------- #


@_needs_vec
def test_ensure_embeddings_is_idempotent_and_scope_bounded() -> None:
    with Storage() as storage:
        add(storage, "p1", "alpha one")
        add(storage, "p2", "alpha two")
        priv = add(storage, "priv", "alpha private", scope="agent-private")

        # No embeddings exist yet: the vector path is empty, not an error.
        assert vector_candidates(
            storage,
            synthetic_embedder("alpha"),
            scopes=("project-shared",),
            limit=10,
        ) == []

        inserted = ensure_embeddings(
            storage, synthetic_embedder, scopes=("project-shared",)
        )
        assert inserted == 2
        assert _vec_count(storage) == 2

        # Second call is a no-op: no duplicates, no growth.
        assert ensure_embeddings(
            storage, synthetic_embedder, scopes=("project-shared",)
        ) == 0
        assert _vec_count(storage) == 2

        # The private memory was out of scope and got no vector row.
        priv_rowid = _rowid(storage, priv.id)
        assert priv_rowid not in _vec_rowids(storage)

        # Widening scope populates the remaining row, and stays idempotent.
        assert ensure_embeddings(
            storage, synthetic_embedder, scopes=("agent-private",)
        ) == 1
        assert _vec_count(storage) == 3
        assert ensure_embeddings(
            storage, synthetic_embedder, scopes=("agent-private",)
        ) == 0


# --------------------------------------------------------------------------- #
# Embedding dimension validation
# --------------------------------------------------------------------------- #


@_needs_vec
def test_wrong_embedding_dimension_raises_everywhere() -> None:
    with Storage() as storage:
        add(storage, "m", "alpha beta")

        with pytest.raises(ValueError, match="384 dimensions"):
            ensure_embeddings(
                storage, wrong_dim_embedder, scopes=("project-shared",)
            )

        with pytest.raises(ValueError, match="384 dimensions"):
            vector_candidates(
                storage, [0.0] * 10, scopes=("project-shared",), limit=5
            )

        with pytest.raises(ValueError, match="384 dimensions"):
            recall(storage, "alpha", embedder=wrong_dim_embedder)

        # A failed ensure_embeddings leaves no partial vector rows behind.
        assert _vec_count(storage) == 0


# --------------------------------------------------------------------------- #
# Empty / garbage query
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("query", ["", "   \t ", "!!! ??? ---", "@#$ ^^^ &&&"])
def test_empty_or_garbage_query_returns_empty_without_crashing(
    query: str,
) -> None:
    with Storage() as storage:
        add(storage, "m", "authentication cookies")

        bm25_only = recall(storage, query)
        assert bm25_only.included == ()
        assert bm25_only.dropped == ()
        assert bm25_only.used_tokens == 0
        assert bm25_only.mode == "bm25-only"

        hybrid = recall(storage, query, embedder=synthetic_embedder)
        assert hybrid.included == ()
        assert hybrid.mode == ("hybrid" if VEC_AVAILABLE else "bm25-only")

        assert bm25_candidates(
            storage, query, scopes=("project-shared",), limit=10
        ) == []


# --------------------------------------------------------------------------- #
# Scope validation is reused / fails closed
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad_scopes, message",
    [
        ([], "non-empty collection"),
        ("project-shared", "non-empty collection"),
        (["bogus"], "Invalid memory scope"),
    ],
)
def test_invalid_scopes_are_rejected(bad_scopes: object, message: str) -> None:
    with Storage() as storage:
        add(storage, "m", "alpha")
        with pytest.raises(ValueError, match=message):
            bm25_candidates(storage, "alpha", scopes=bad_scopes, limit=10)
        with pytest.raises(ValueError, match=message):
            vector_candidates(
                storage, [0.0] * _DIM, scopes=bad_scopes, limit=10
            )
        with pytest.raises(ValueError, match=message):
            ensure_embeddings(storage, synthetic_embedder, scopes=bad_scopes)
        with pytest.raises(ValueError, match=message):
            recall(storage, "alpha", scopes=bad_scopes)


# --------------------------------------------------------------------------- #
# RRF fusion
# --------------------------------------------------------------------------- #


def test_rrf_fusion_scores_signals_and_ordering() -> None:
    ma = make_memory("a", "alpha")
    mb = make_memory("b", "beta")
    mc = make_memory("c", "gamma")

    fused = rrf_fuse(
        {
            "bm25": [(ma, 1), (mc, 2)],
            "vector": [(ma, 1), (mb, 2)],
        },
        k=60,
    )

    ordering = [(memory.id, signals) for memory, _score, signals in fused]
    # ma is rank 1 in both lists -> highest score, first.
    assert ordering[0] == ("a", ("bm25", "vector"))
    # mb and mc tie on score (1/62) -> tiebreak by id ascending: b before c.
    assert ordering[1] == ("b", ("vector",))
    assert ordering[2] == ("c", ("bm25",))

    scores = {memory.id: score for memory, score, _ in fused}
    assert scores["a"] == pytest.approx(2.0 / 61.0)
    assert scores["b"] == pytest.approx(1.0 / 62.0)
    assert scores["c"] == pytest.approx(1.0 / 62.0)


def test_rrf_rejects_negative_k() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        rrf_fuse({"bm25": []}, k=-1)


# --------------------------------------------------------------------------- #
# small local helpers
# --------------------------------------------------------------------------- #


def _vec_count(storage: Storage) -> int:
    return storage.connection.execute(
        "SELECT count(*) FROM memories_vec"
    ).fetchone()[0]


def _vec_rowids(storage: Storage) -> set[int]:
    return {
        row[0]
        for row in storage.connection.execute(
            "SELECT rowid FROM memories_vec"
        ).fetchall()
    }


def _rowid(storage: Storage, memory_id: str) -> int:
    return storage.connection.execute(
        "SELECT rowid FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()[0]
