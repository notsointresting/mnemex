# DECISIONS

Running decision log for the mnemex build. Each entry is a decision anchored to
the code it concerns, mirroring the product's own `remember()` model. Newest
first.

---

## Phase 2 — Retrieval + token governor — GATE: PASS (local)

**Anchors:** `src/mnemex/retrieval.py`
**Verified:** 2026-07-14, Windows host, Python 3.14.0

### Decisions
- **Hybrid recall = BM25 (FTS5) + optional vector (sqlite-vec) fused by RRF**
  (`score = Σ 1/(k+rank)`, k=60), sorted by score desc then `memory.id` asc for
  a deterministic total order.
- **No-ML is the default.** `embedder=None` → mode `bm25-only`: the vector path,
  `ensure_embeddings`, and query embedding are all skipped and no ML library is
  imported. An embedder is *injected* only when the caller wants the vector
  signal (mode `hybrid`). `sqlite-vec` is a vector index, not an embedding
  model, so it does not violate no-ML.
- **The token governor guarantees the cap.** Each candidate is included only
  while the running `estimate_tokens` sum stays `<= max_tokens`; a memory that
  would exceed the cap is dropped intact (never truncated), so the guarantee
  holds even for a single oversized memory and for `max_tokens=0`.
  `estimate_tokens` is a documented ~4-chars/token heuristic with a tokenizer
  upgrade path left in a `ponytail:` comment.
- **Vectors are lazily populated, not a schema change.** `ensure_embeddings`
  idempotently writes missing `memories_vec` rows by shared `rowid`; the Phase 1
  schema is untouched.
- **Scope isolation is fail-closed in the vector path.** KNN is constrained by
  `rowid IN (<in-scope rowids>)` with `k = total embedded rows` (sqlite-vec
  0.1.9 applies the rowid filter as a post-filter, so a small k could starve
  in-scope hits); results map back only through the in-scope `rowid -> id` map,
  and scope validation reuses `Storage._validate_scope`.

### Gate evidence
- Plan's three Phase 2 criteria certified by an **independent verifier spawned
  separately from the implementer and the golden-test author**:
  - Governor never exceeds the cap → `test_token_cap_never_exceeded_under_fuzz`
    (250 seeded cases) + oversized-single-memory drop test.
  - No-ML mode sane with zero model → `test_no_ml_recall_returns_sane_bm25_order`
    + a SQLite trace proving `recall(embedder=None)` issues zero `memories_vec`
    statements and never calls the embedder + grep: no ML imports.
  - RRF beats either signal alone → `test_rrf_beats_either_signal_alone_on_labeled_set`.
    Independently recomputed MRR: **bm25=0.4000, vector=0.7167, fused=1.0000**;
    fused strictly greater than both, non-degenerate (bm25 sole-wins 2 queries,
    vector sole-wins 3).
- Verifier reran from a clean install (exit 0/0/0, 62 passed) and added 5 fresh
  adversarial probes (5/5 pass), including: an `agent-private` memory pinned as
  the exact nearest vector is excluded from `project-shared` results yet visible
  to its own scope; FTS5 operator/injection strings never raise or inject; RRF
  tie-break is a deterministic id-ascending total order. Confirmed the committed
  tree unchanged, no schema/behavior change to storage/anchors (all 40 Phase 0/1
  tests still green), and no Phase 3+ feature introduced.

### Residual risk (non-blocking)
- Certified only on the local Windows host (Python 3.14.0, outside the declared
  3.10–3.13 window); CI matrix configured but not executed here.
- `vector_candidates` is O(embedded rows) per query on sqlite-vec 0.1.9
  (post-filter workaround); fine at local-first scale, upgrade path noted.
- Ranking quality measured on a synthetic concept embedder, not a real model —
  correct for a Phase 2 mechanism proof; real-model validation belongs to a
  later phase.

---

## Phase 1 — Anchor mechanism + storage — GATE: PASS (local)

**Anchors:** `src/mnemex/anchors.py` · `src/mnemex/storage.py`
**Verified:** 2026-07-14, Windows host, Python 3.14.0

### Decisions
- **The freshness verdict depends only on anchor existence and exact
  `content_hash` equality**, never on node name/file/line/type. This keeps
  staleness deterministic and immune to cosmetic churn. Enforced by
  `tests/test_anchor_adversarial.py::test_freshness_is_deterministic_and_depends_only_on_hash_equality`.
- **Four explicit freshness states** — `fresh`, `stale`, `orphaned`,
  `unanchored`. A missing stored hash on an existing node is `stale` (not
  silently fresh); a deleted node is `orphaned` and the memory + its anchor are
  preserved for reconciliation rather than crashing or auto-decaying.
- **`remember()` stamps the structural backend's `content_hash`**; it never
  hashes the memory text. Anchor resolution is exact: a direct node id, or an
  exact `(file, symbol)` pair. Zero matches → `AnchorNotFoundError`; multiple →
  `AmbiguousAnchorError`. No fuzzy, case-insensitive, or path-normalized
  fallback (proved with SQL metacharacters, `LIKE` wildcards, Windows
  backslash paths, and unicode look-alikes).
- **`check_freshness()` reads candidates through `Storage.list_memories(scopes)`
  first**, so scope isolation is structural: an `agent-private` memory can never
  leak into a `project-shared` query, even when its id is requested explicitly.
  It performs no writes (verified via a SQLite statement trace).
- **Scopes are validated before any SQL** and re-enforced by a `CHECK`
  constraint; a `vec0(384)` table and an external-content FTS5 table with
  insert/delete triggers are created but only Phase 1 CRUD/sync behavior is
  exercised — no retrieval/ranking yet.
- **Public API fails closed on bad input types:** `resolve_anchor()` /
  `remember()` raise `TypeError` for a non-`Anchor`/non-`str` anchor rather than
  leaking an `AttributeError` (hardening from the verifier's review;
  `tests/test_anchor_typing.py`).

### Gate evidence
- Plan Phase 1 criteria mapped to passing tests by an **independent verifier
  (separate spawn from both implementers)**:
  - stale-on-change / fresh-on-no-change → `test_freshness_matrix_is_read_only_ordered_and_repeatable`, `test_state_transitions_never_mutate_the_stamped_memory_row`.
  - orphaned detected, not crashed → same matrix test + `test_deleting_node_preserves_memory_anchor_for_orphan_detection`.
  - scope isolation → `test_project_freshness_excludes_agent_private_even_by_memory_id`, `test_list_memories_filters_scopes_without_private_leakage`, `test_scope_isolation_over_every_non_empty_subset` (exhaustive over all 7 subsets).
- Independent verifier reran from a clean install: `pip install -e ".[dev]"`
  exit 0, `ruff check .` exit 0, `pytest` exit 0. It added 5 fresh adversarial
  probes (unicode/whitespace look-alikes, 100k-char symbol, cross-connection
  hash change, write-free trace, bad-type rejection) — 5/5 passed — and
  confirmed the committed tree was unchanged and no Phase 2+ feature was
  smuggled in.
- Post-hardening orchestrator rerun: `ruff check .` → "All checks passed!";
  `pytest -q` → **40 passed** (storage 8, anchors 17 incl. parametrized,
  adversarial 6, typing 8, smoke 1).

### Residual risk (non-blocking)
- Certified only on the local Windows host, which runs Python **3.14.0** —
  outside the declared 3.10–3.13 support window. The 3-OS × 3.10–3.13 CI matrix
  is configured in `.github/workflows/ci.yml` but has **not** been executed
  here.
- Phase 1 hashes are caller-supplied on synthetic nodes; real cross-version hash
  stability is correctly deferred to the Phase 3 structural indexer.

---

## Phase 0 — Scaffold & decisions — GATE: PASS (local)

**Anchors:** `pyproject.toml` · `src/mnemex/__init__.py` · `tests/test_smoke.py`
· `.github/workflows/ci.yml`

### Decisions
- **Path A** confirmed: mnemex is the memory + anchor brain over a *pluggable*
  structural backend; no parser is built here.
- **One SQLite file via `sqlite-vec` + FTS5**, `src/` layout, setuptools build
  backend, exact-pinned runtime deps (`fastmcp==3.4.4`, `sqlite-vec==0.1.9`) and
  dev deps (`pytest==9.1.1`, `ruff==0.15.21`). No ML/cloud dependency.
- **CI matrix** = {ubuntu, macos, windows} × Python {3.10, 3.11, 3.12, 3.13},
  each job running install + Ruff + pytest.

### Gate evidence
- Local: `pip install -e ".[dev]"` exit 0 (built editable wheel), `ruff check .`
  exit 0, `pytest` exit 0 (smoke test loads sqlite-vec, runs `vec_version()`,
  creates an FTS5 table and asserts a `MATCH` hit, then disables extension
  loading).
- An independent read-only verifier reran all three commands (exit 0) and
  audited the exact pins, all 12 configured CI combinations, genuine
  vec/FTS5 behavior, and the absence of Phase 1 code.
