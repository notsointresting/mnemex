"""Independent Phase 2 gate tests: RRF ranking quality, token-cap fuzz, and
determinism.

These are authored independently of ``retrieval.py`` and drive the module only
through its public API. The synthetic embedder here is *concept-based* (a fixed
synonym map), not bag-of-words, so semantic proximity exists WITHOUT exact
lexical overlap — that is what lets the vector signal win queries BM25 misses,
and vice versa, so the fused (RRF) ranking can genuinely beat either alone.

No ML dependency; all data and the embedder are fixed/deterministic.
"""

from __future__ import annotations

import math
import random
import re

from mnemex.anchors import remember
from mnemex.retrieval import (
    bm25_candidates,
    ensure_embeddings,
    estimate_tokens,
    recall,
    rrf_fuse,
    vector_candidates,
)
from mnemex.storage import Memory, Storage

_DIM = 384

# Disjoint concept -> synonym map. Each concept occupies one fixed dimension,
# so two texts are "near" iff they share a concept, regardless of exact words.
_CONCEPTS: dict[str, list[str]] = {
    "auth": ["login", "signin", "authenticate", "credentials", "oauth", "session"],
    "payment": ["payment", "billing", "invoice", "charge", "refund", "checkout"],
    "cache": ["cache", "caching", "redis", "memoize", "invalidation", "ttl"],
    "database": ["database", "schema", "migration", "sql", "index"],
}
_CONCEPT_DIM = {name: i for i, name in enumerate(sorted(_CONCEPTS))}
_TOKEN_TO_CONCEPT = {
    token: concept
    for concept, tokens in _CONCEPTS.items()
    for token in tokens
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def concept_embedder(text: str) -> list[float]:
    """Deterministic, ML-free, concept-based unit embedding.

    Concept words contribute to their concept's dimension; text with no concept
    word yields the zero vector (so purely lexical/rare-keyword text is
    indistinguishable to the vector signal — an honest vector "miss").
    """

    vector = [0.0] * _DIM
    for token in _TOKEN_RE.findall(text.lower()):
        concept = _TOKEN_TO_CONCEPT.get(token)
        if concept is not None:
            vector[_CONCEPT_DIM[concept]] += 1.0
    norm = math.sqrt(sum(component * component for component in vector))
    if norm > 0.0:
        vector = [component / norm for component in vector]
    return vector


def _add(storage: Storage, memory_id: str, content: str) -> None:
    remember(storage, content, memory_id=memory_id, scope="project-shared")


def _reciprocal_rank(ordered_ids: list[str], target_id: str) -> float:
    """1 / rank of target in an ordered id list; 0.0 if absent."""

    for rank, memory_id in enumerate(ordered_ids, start=1):
        if memory_id == target_id:
            return 1.0 / rank
    return 0.0


# Labeled golden set. Insertion order matters: the zero-vector distractors are
# inserted BEFORE the rare-keyword targets so that, under a zero-vector query,
# the target is not the lowest-rowid tie — i.e. the vector signal genuinely
# fails to rank it first.
_ZERO_VECTOR_DISTRACTORS = [
    ("z1", "handshake tuning notes"),
    ("z2", "resolver batching notes"),
]
_KEYWORD_TARGETS = [
    ("kerberos", "k_target", "kerberos handshake tuning"),
    ("graphql", "g_target", "graphql resolver batching"),
]
_SYNONYM_TARGETS = [
    # (query, target_id, target_content) — query shares the CONCEPT but no
    # exact word with the target, and no memory contains the query words, so
    # BM25 returns nothing while the vector signal finds the concept match.
    ("login signin", "auth_target", "authenticate credentials oauth"),
    ("billing invoice", "pay_target", "charge refund checkout"),
    ("caching redis", "cache_target", "memoize invalidation ttl"),
]


def _build_golden_db(storage: Storage) -> None:
    for memory_id, content in _ZERO_VECTOR_DISTRACTORS:
        _add(storage, memory_id, content)
    for _query, target_id, content in _KEYWORD_TARGETS:
        _add(storage, target_id, content)
    for _query, target_id, content in _SYNONYM_TARGETS:
        _add(storage, target_id, content)
    ensure_embeddings(storage, concept_embedder, scopes=("project-shared",))


def _ranked_ids_bm25(storage: Storage, query: str) -> list[str]:
    return [
        memory.id
        for memory, _rank in bm25_candidates(
            storage, query, scopes=("project-shared",), limit=20
        )
    ]


def _ranked_ids_vector(storage: Storage, query: str) -> list[str]:
    return [
        memory.id
        for memory, _rank in vector_candidates(
            storage,
            concept_embedder(query),
            scopes=("project-shared",),
            limit=20,
        )
    ]


def _ranked_ids_fused(storage: Storage, query: str) -> list[str]:
    fused = rrf_fuse(
        {
            "bm25": bm25_candidates(
                storage, query, scopes=("project-shared",), limit=20
            ),
            "vector": vector_candidates(
                storage,
                concept_embedder(query),
                scopes=("project-shared",),
                limit=20,
            ),
        }
    )
    return [memory.id for memory, _score, _signals in fused]


def test_rrf_beats_either_signal_alone_on_labeled_set() -> None:
    with Storage() as storage:
        _build_golden_db(storage)

        queries = [
            (query, target_id)
            for query, target_id, _content in (
                *_SYNONYM_TARGETS,
                *((q, t, c) for q, t, c in _KEYWORD_TARGETS),
            )
        ]

        bm25_rrs: list[float] = []
        vector_rrs: list[float] = []
        fused_rrs: list[float] = []
        bm25_wins = vector_wins = 0

        for query, target_id in queries:
            bm25_rr = _reciprocal_rank(_ranked_ids_bm25(storage, query), target_id)
            vector_rr = _reciprocal_rank(
                _ranked_ids_vector(storage, query), target_id
            )
            fused_rr = _reciprocal_rank(
                _ranked_ids_fused(storage, query), target_id
            )
            bm25_rrs.append(bm25_rr)
            vector_rrs.append(vector_rr)
            fused_rrs.append(fused_rr)
            if bm25_rr > vector_rr:
                bm25_wins += 1
            if vector_rr > bm25_rr:
                vector_wins += 1

        bm25_mrr = sum(bm25_rrs) / len(bm25_rrs)
        vector_mrr = sum(vector_rrs) / len(vector_rrs)
        fused_mrr = sum(fused_rrs) / len(fused_rrs)

        detail = (
            f"bm25={bm25_mrr:.4f} vector={vector_mrr:.4f} fused={fused_mrr:.4f} "
            f"bm25_wins={bm25_wins} vector_wins={vector_wins}"
        )

        # The gate: fused strictly beats BOTH single signals.
        assert fused_mrr > bm25_mrr, detail
        assert fused_mrr > vector_mrr, detail
        # Non-degenerate: each signal is the sole winner on at least one query,
        # so the fused win is real, not an artifact of one dominant signal.
        assert bm25_wins >= 1, detail
        assert vector_wins >= 1, detail
        # Fused should top-rank every target here.
        assert fused_mrr == 1.0, detail


def test_token_cap_never_exceeded_under_fuzz() -> None:
    rng = random.Random(0xC0FFEE)
    filler = ["alpha", "widget", "beta", "gamma", "delta", "omega"]

    for _iteration in range(250):
        with Storage() as storage:
            count = rng.randint(1, 12)
            for i in range(count):
                length = rng.choice([0, 1, 3, 20, 200, 1200])
                # Always include the query term so the memory is recall-able.
                body = "widget " + " ".join(
                    rng.choice(filler) for _ in range(length)
                )
                remember(storage, body, memory_id=f"m{i}", scope="project-shared")

            cap = rng.choice([0, 1, 2, 5, 25, 300, 5000])
            result = recall(storage, "widget", limit=50, max_tokens=cap)

            included_cost = sum(
                estimate_tokens(
                    sm.memory.content
                    if not sm.memory.rationale
                    else f"{sm.memory.content}\n{sm.memory.rationale}"
                )
                for sm in result.included
            )
            assert included_cost <= cap
            assert result.used_tokens == included_cost
            assert result.used_tokens <= cap

            included_ids = [sm.memory.id for sm in result.included]
            dropped_ids = [sm.memory.id for sm in result.dropped]
            # Included and dropped are disjoint and every included memory
            # individually fits under the cap.
            assert set(included_ids).isdisjoint(dropped_ids)
            for sm in result.included:
                one_cost = estimate_tokens(
                    sm.memory.content
                    if not sm.memory.rationale
                    else f"{sm.memory.content}\n{sm.memory.rationale}"
                )
                assert one_cost <= cap


def test_recall_is_deterministic_in_both_modes() -> None:
    def signature(result_included: object) -> list[tuple[str, float, int, tuple[str, ...]]]:
        return [
            (sm.memory.id, sm.score, sm.rank, sm.signals)
            for sm in result_included  # type: ignore[attr-defined]
        ]

    with Storage() as storage:
        _build_golden_db(storage)

        # BM25-only mode.
        a = recall(storage, "authenticate refund", limit=20)
        b = recall(storage, "authenticate refund", limit=20)
        assert signature(a.included) == signature(b.included)
        assert a.mode == "bm25-only"

        # Hybrid mode, including a redundant ensure_embeddings call that must
        # not change ordering (idempotent embeddings).
        c = recall(storage, "authenticate refund", limit=20, embedder=concept_embedder)
        ensure_embeddings(storage, concept_embedder, scopes=("project-shared",))
        d = recall(storage, "authenticate refund", limit=20, embedder=concept_embedder)
        assert signature(c.included) == signature(d.included)
        assert c.mode == "hybrid"

        # Ties are broken by memory id: equal scores must be ordered id asc.
        for first, second in zip(c.included, c.included[1:]):
            if first.score == second.score:
                assert first.memory.id < second.memory.id


def _make_detached_memory(memory_id: str, content: str) -> Memory:
    """Guard: the golden helpers rely on remember() persisting content; this
    keeps a direct Memory construction path exercised for parity."""

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
        created_at="2026-07-14T00:00:00Z",
        last_accessed="2026-07-14T00:00:00Z",
        last_verified="2026-07-14T00:00:00Z",
        tags="",
    )


def test_golden_helper_memory_shape_matches_storage() -> None:
    with Storage() as storage:
        remember(storage, "kerberos handshake tuning", memory_id="k", scope="project-shared")
        stored = storage.get_memory("k")
        expected = _make_detached_memory("k", "kerberos handshake tuning")
        # Same content/scope/anchor shape (timestamps/source differ by design).
        assert stored is not None
        assert stored.content == expected.content
        assert stored.scope == expected.scope
        assert stored.anchor_node_id is None
