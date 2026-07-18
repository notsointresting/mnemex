"""Phase 2 — hybrid recall (BM25 + vector) with RRF fusion + token governor.

This module builds strictly on top of :mod:`mnemex.storage`; it never changes
the schema and performs only reads plus a single additive write path
(``ensure_embeddings`` populating ``memories_vec``). It has **zero ML
dependencies**: an embedding model is never imported here. Callers inject an
``Embedder`` when they want the vector signal; with no embedder the module runs
in deterministic BM25-only ("no-ML") mode.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass

# The optional sqlite-vec accelerator is reached only through
# ``storage.vec_serialize`` (see mnemex.vector_backend); this module never
# imports the native package, keeping core mode import-free of it.
from mnemex.storage import Memory, Storage

__all__ = [
    "Embedder",
    "ScoredMemory",
    "RecallResult",
    "estimate_tokens",
    "ensure_embeddings",
    "bm25_candidates",
    "vector_candidates",
    "rrf_fuse",
    "govern_memories",
    "recall",
]

# An embedder maps text -> a dense vector. It must return exactly this many
# components; the length is validated at every point of use.
Embedder = Callable[[str], Sequence[float]]

_EMBEDDING_DIM = 384

# Canonical ordering for the retrieval signals so ``ScoredMemory.signals`` is
# deterministic regardless of fusion input order. Known signals sort first in
# this order; any other label sorts after them, alphabetically.
_SIGNAL_ORDER = ("bm25", "vector")

_WORD_RE = re.compile(r"\w+")


@dataclass(frozen=True, slots=True)
class ScoredMemory:
    """A single fused, ranked result. ``signals`` is a subset of
    ``('bm25', 'vector')`` naming which retrieval paths surfaced it."""

    memory: Memory
    score: float
    rank: int
    signals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecallResult:
    """Outcome of :func:`recall` after the token governor has run.

    ``budget_tokens`` mirrors the requested ``max_tokens`` (``None`` = no cap).
    ``used_tokens`` is the exact sum of ``estimate_tokens`` over ``included``
    and is guaranteed ``<= budget_tokens`` whenever a cap is set.
    """

    included: tuple[ScoredMemory, ...]
    dropped: tuple[ScoredMemory, ...]
    used_tokens: int
    budget_tokens: int | None
    mode: str


def estimate_tokens(
    text: str, token_counter: Callable[[str], int] | None = None
) -> int:
    """Estimate the token cost of ``text``.

    Deterministic, monotonic non-decreasing in ``len(text)``, ``0`` for the
    empty string and ``>= 1`` for any non-empty string.

    ponytail: this is a ~4-chars-per-token heuristic, NOT a real BPE
    tokenizer. Ceiling: it mis-estimates code, CJK, and whitespace-heavy text
    and is not model-specific, so it can be off by a constant factor. Upgrade
    path: drop in the target model's tokenizer (e.g. ``tiktoken``) behind this
    exact signature without touching callers.
    """

    if token_counter is None:
        return (len(text) + 3) // 4
    count = token_counter(text)
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("token_counter must return a non-negative integer")
    return count


def _combine_text(content: str, rationale: str) -> str:
    """Text used for both embedding and token accounting: content, then
    rationale when present. Deterministic."""

    parts = [part for part in (content, rationale) if part]
    return "\n".join(parts)


def _normalize_scopes(scopes: Collection[str]) -> tuple[str, ...]:
    """Validate + normalize scopes, reusing Storage's exact semantics so
    retrieval can never widen the trust boundary storage enforces.

    Mirrors ``Storage.list_memories`` (reject str/bytes and empty collections)
    and delegates the per-value check to ``Storage._validate_scope`` — the
    single source of truth for the valid scope set. Returns a sorted, de-duped
    tuple suitable for building ``IN (...)`` placeholders.
    """

    if isinstance(scopes, (str, bytes)):
        raise ValueError("scopes must be a non-empty collection")
    values = tuple(scopes)
    if not values:
        raise ValueError("scopes must be a non-empty collection")
    for scope in values:
        Storage._validate_scope(scope)
    return tuple(sorted(set(values)))


def _extract_terms(query: str) -> list[str]:
    """Extract bare word tokens from a free-text query."""

    return _WORD_RE.findall(query)


def _build_match(terms: Sequence[str]) -> str:
    """Build a safe FTS5 MATCH expression.

    Every token is a bare ``\\w+`` run, so wrapping each in double quotes makes
    it a literal FTS5 term — this neutralizes operators/metacharacters (there
    is nothing to escape inside ``\\w+``) and turns keywords like ``OR`` into
    plain terms. Tokens are OR-ed for recall.
    """

    return " OR ".join(f'"{term}"' for term in terms)


def _validate_embedding(vector: Sequence[float]) -> list[float]:
    """Coerce to a list of floats and enforce the required dimension."""

    values = [float(component) for component in vector]
    if len(values) != _EMBEDDING_DIM:
        raise ValueError(
            f"embedding must have {_EMBEDDING_DIM} dimensions, "
            f"got {len(values)}"
        )
    return values


def _signal_sort_key(name: str) -> tuple[int, object]:
    try:
        return (0, _SIGNAL_ORDER.index(name))
    except ValueError:
        return (1, name)


def ensure_embeddings(
    storage: Storage,
    embedder: Embedder,
    *,
    scopes: Collection[str],
) -> int:
    """Idempotently populate ``memories_vec`` for in-scope memories that lack a
    vector row, keyed by the shared ``rowid``.

    Embeds ``content`` (plus ``rationale`` when present). Never duplicates: a
    second call inserts nothing. The inserts run in one transaction, so a bad
    embedding (wrong dimension) rolls back cleanly. Returns the number of rows
    inserted. This is the module's only write path.
    """

    scope_values = _normalize_scopes(scopes)
    if not storage.vec_available:
        return 0
    placeholders = ", ".join("?" for _ in scope_values)
    rows = storage.connection.execute(
        f"""
        SELECT rowid, content, rationale
        FROM memories
        WHERE scope IN ({placeholders})
        ORDER BY rowid
        """,
        scope_values,
    ).fetchall()
    if not rows:
        return 0

    candidate_rowids = [row[0] for row in rows]
    existing_ph = ", ".join("?" for _ in candidate_rowids)
    existing = {
        found[0]
        for found in storage.connection.execute(
            f"SELECT rowid FROM memories_vec WHERE rowid IN ({existing_ph})",
            candidate_rowids,
        ).fetchall()
    }

    pending = [row for row in rows if row[0] not in existing]
    if not pending:
        return 0

    inserted = 0
    with storage.connection:
        for rowid, content, rationale in pending:
            embedding = _validate_embedding(
                embedder(_combine_text(content, rationale))
            )
            storage.connection.execute(
                "INSERT INTO memories_vec(rowid, embedding) VALUES (?, ?)",
                (rowid, storage.vec_serialize(embedding)),
            )
            inserted += 1
    return inserted


def bm25_candidates(
    storage: Storage,
    query: str,
    *,
    scopes: Collection[str],
    limit: int,
) -> list[tuple[Memory, int]]:
    """Scope-filtered BM25 candidates as ``(memory, 1-based-rank)``.

    An empty or non-word query returns ``[]`` and never raises on query
    content. Ordering is best-first: ``bm25(memories_fts) ASC, m.id ASC``
    (FTS5 bm25 returns negative numbers, lower = better).
    """

    scope_values = _normalize_scopes(scopes)
    if limit <= 0:
        return []
    terms = _extract_terms(query)
    if not terms:
        return []
    match = _build_match(terms)
    placeholders = ", ".join("?" for _ in scope_values)
    rows = storage.connection.execute(
        f"""
        SELECT m.id
        FROM memories_fts
        JOIN memories m ON m.rowid = memories_fts.rowid
        WHERE memories_fts MATCH ? AND m.scope IN ({placeholders})
        ORDER BY bm25(memories_fts) ASC, m.id ASC
        LIMIT ?
        """,
        (match, *scope_values, limit),
    ).fetchall()

    results: list[tuple[Memory, int]] = []
    for rank, (memory_id,) in enumerate(rows, start=1):
        memory = storage.get_memory(memory_id)
        if memory is not None:
            results.append((memory, rank))
    return results


def vector_candidates(
    storage: Storage,
    query_embedding: Sequence[float],
    *,
    scopes: Collection[str],
    limit: int,
) -> list[tuple[Memory, int]]:
    """Scope-filtered vector KNN candidates as ``(memory, 1-based-rank)``.

    Uses the sqlite-vec KNN pattern that cannot be wrapped in a JOIN: the scope
    filter is applied via ``rowid IN (...)`` over the in-scope rowids. Returns
    ``[]`` when no in-scope memory has a vector row.
    """

    scope_values = _normalize_scopes(scopes)
    query = _validate_embedding(query_embedding)
    if not storage.vec_available or limit <= 0:
        return []

    scope_ph = ", ".join("?" for _ in scope_values)
    scoped = storage.connection.execute(
        f"SELECT rowid, id FROM memories WHERE scope IN ({scope_ph})",
        scope_values,
    ).fetchall()
    if not scoped:
        return []

    rowid_to_id = {rowid: memory_id for rowid, memory_id in scoped}
    allowed_rowids = list(rowid_to_id)

    # sqlite-vec (0.1.9) applies the ``rowid IN (...)`` constraint as a filter
    # over the k globally-nearest rows, NOT as a pre-filter, so a small k can
    # starve in-scope results whenever out-of-scope vectors are nearer. Ask for
    # k = total embedded rows to stay complete, then take the top ``limit``
    # after a deterministic sort.
    # ponytail: this makes each vector query O(embedded rows). Upgrade path is
    # native sqlite-vec metadata partitioning, which needs a schema-level
    # partition key (deferred -- it would require editing storage.py).
    vec_total = storage.connection.execute(
        "SELECT count(*) FROM memories_vec"
    ).fetchone()[0]
    if not vec_total:
        return []

    rowid_ph = ", ".join("?" for _ in allowed_rowids)
    matches = storage.connection.execute(
        f"""
        SELECT rowid, distance
        FROM memories_vec
        WHERE embedding MATCH ? AND k = ? AND rowid IN ({rowid_ph})
        ORDER BY distance
        """,
        (storage.vec_serialize(query), vec_total, *allowed_rowids),
    ).fetchall()

    # sqlite-vec (0.1.9) permits only a single 'ORDER BY distance' clause on a
    # vec0 KNN query, so break distance ties deterministically by rowid here,
    # then keep the closest ``limit``.
    matches.sort(key=lambda row: (row[1], row[0]))
    matches = matches[:limit]

    results: list[tuple[Memory, int]] = []
    for rank, (rowid, _distance) in enumerate(matches, start=1):
        memory = storage.get_memory(rowid_to_id[rowid])
        if memory is not None:
            results.append((memory, rank))
    return results


def rrf_fuse(
    ranked_lists: Mapping[str, Sequence[tuple[Memory, int]]],
    *,
    k: int = 60,
) -> list[tuple[Memory, float, tuple[str, ...]]]:
    """Reciprocal Rank Fusion across signal-labelled ranked lists.

    ``ranked_lists`` maps a signal name (e.g. ``"bm25"``) to its
    ``(memory, rank)`` list. A memory's score is ``sum(1 / (k + rank))`` over
    every list it appears in. Output is sorted by score descending, then
    ``memory.id`` ascending, and records the contributing signals per memory.
    """

    if k < 0:
        raise ValueError("rrf k must be non-negative")

    scores: dict[str, float] = {}
    signals: dict[str, set[str]] = {}
    memories: dict[str, Memory] = {}
    for signal, ranked in ranked_lists.items():
        for memory, rank in ranked:
            scores[memory.id] = scores.get(memory.id, 0.0) + 1.0 / (k + rank)
            signals.setdefault(memory.id, set()).add(signal)
            memories.setdefault(memory.id, memory)

    fused = [
        (
            memories[memory_id],
            scores[memory_id],
            tuple(sorted(signals[memory_id], key=_signal_sort_key)),
        )
        for memory_id in memories
    ]
    fused.sort(key=lambda item: (-item[1], item[0].id))
    return fused


def govern_memories(
    memories: Sequence[Memory],
    *,
    max_tokens: int | None,
    mode: str,
    token_counter: Callable[[str], int] | None = None,
) -> RecallResult:
    """Apply the standard token governor to an already-selected memory set."""
    if max_tokens is not None and max_tokens < 0:
        raise ValueError("max_tokens must be non-negative or None")

    included: list[ScoredMemory] = []
    dropped: list[ScoredMemory] = []
    used_tokens = 0
    for rank, memory in enumerate(memories, start=1):
        scored = ScoredMemory(
            memory=memory,
            score=1.0 / rank,
            rank=rank,
            signals=("anchor-file",),
        )
        cost = estimate_tokens(
            _combine_text(memory.content, memory.rationale), token_counter
        )
        if max_tokens is None or used_tokens + cost <= max_tokens:
            included.append(scored)
            used_tokens += cost
        else:
            dropped.append(scored)

    return RecallResult(
        included=tuple(included),
        dropped=tuple(dropped),
        used_tokens=used_tokens,
        budget_tokens=max_tokens,
        mode=mode,
    )


def recall(
    storage: Storage,
    query: str,
    *,
    scopes: Collection[str] = ("project-shared",),
    embedder: Embedder | None = None,
    limit: int = 10,
    max_tokens: int | None = None,
    rrf_k: int = 60,
    token_counter: Callable[[str], int] | None = None,
) -> RecallResult:
    """Hybrid recall with a hard token governor.

    * ``embedder is None`` -> mode ``"bm25-only"``: the vector path is skipped
      entirely and no embedder is invoked.
    * ``embedder`` present -> mode ``"hybrid"``: ``ensure_embeddings`` runs, the
      query is embedded, and BM25 + vector candidates are fused via RRF.

    ``limit`` is applied to the fused list, then the token governor walks it in
    rank order: a memory is included while the running ``estimate_tokens`` sum
    stays ``<= max_tokens``; one that would exceed the cap is moved to
    ``dropped`` and iteration CONTINUES (later, smaller memories may still fit).
    This guarantees ``used_tokens <= max_tokens`` always — including when a
    single memory alone exceeds the cap (it is dropped, never truncated).
    ``max_tokens=None`` means no cap.
    """

    scope_values = _normalize_scopes(scopes)
    if max_tokens is not None and max_tokens < 0:
        raise ValueError("max_tokens must be non-negative or None")
    mode = (
        "hybrid" if (embedder is not None and storage.vec_available)
        else "bm25-only"
    )

    terms = _extract_terms(query)
    if not terms or limit <= 0:
        # Empty/garbage query (or no room): return empty without invoking the
        # embedder. This keeps "garbage in -> nothing out" true in both modes.
        return RecallResult(
            included=(),
            dropped=(),
            used_tokens=0,
            budget_tokens=max_tokens,
            mode=mode,
        )

    ranked_lists: dict[str, list[tuple[Memory, int]]] = {
        "bm25": bm25_candidates(
            storage, query, scopes=scope_values, limit=limit
        )
    }
    if embedder is not None and storage.vec_available:
        # Idempotent, additive population of missing vector rows for these
        # scopes. ponytail: recomputes the missing set on each hybrid recall;
        # upgrade path is event-driven population when a write API lands.
        ensure_embeddings(storage, embedder, scopes=scope_values)
        query_embedding = _validate_embedding(embedder(query))
        ranked_lists["vector"] = vector_candidates(
            storage, query_embedding, scopes=scope_values, limit=limit
        )

    fused = rrf_fuse(ranked_lists, k=rrf_k)[:limit]

    included: list[ScoredMemory] = []
    dropped: list[ScoredMemory] = []
    used_tokens = 0
    for rank, (memory, score, signals) in enumerate(fused, start=1):
        scored = ScoredMemory(
            memory=memory, score=score, rank=rank, signals=signals
        )
        cost = estimate_tokens(
            _combine_text(memory.content, memory.rationale), token_counter
        )
        if max_tokens is None or used_tokens + cost <= max_tokens:
            included.append(scored)
            used_tokens += cost
        else:
            dropped.append(scored)

    return RecallResult(
        included=tuple(included),
        dropped=tuple(dropped),
        used_tokens=used_tokens,
        budget_tokens=max_tokens,
        mode=mode,
    )
