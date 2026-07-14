# Implementation Plan for Opus 4.8 — `mnemex` (Anchored Agent Memory MCP Server)

*Executable plan for building the project with Claude Code driven by Opus 4.8 as the orchestrator, using subagents. Every phase has an owner, explicit deliverables, a verification gate, and tests. Nothing merges without passing its gate. Project name is a placeholder — swap for whatever you register.*

Reference: this builds the architecture from `codebase-mind-improved-plan.md` — **decision anchors + JIT hook injection + one SQLite file (sqlite-vec) + skills/AGENTS.md distribution.** Path A (pluggable structural backend) is assumed; Path B (Rust core) notes are called out where they differ.

---

## 0. How to run Opus 4.8 as the orchestrator

**Model roles.** Run the top-level session on **Opus 4.8** as the *orchestrator/architect*. It plans, decomposes, delegates to subagents, and owns the merge gate. It should write little code directly — its job is decomposition, review, and integration. Use Sonnet for high-volume implementation subagents where speed/cost matters; keep Opus for architecture, the anchor/fusion core, and final verification.

**Golden rules for the orchestrator (put these in `CLAUDE.md`):**
- One subagent = one bounded deliverable with a written contract (inputs, outputs, tests it must pass). Never hand a subagent a vague goal.
- Subagents return *diffs + test results + a self-report*, not prose. The orchestrator reviews the diff, never blind-merges.
- A phase is **not done** until its verification gate passes. Gates are objective (tests green, benchmark met), not "looks good."
- The **verifier subagent is a different instance from the implementer** — never let the author grade their own work on the critical path.
- Keep a running `DECISIONS.md` (dogfood your own `remember()` once it exists).

**Context hygiene.** Each subagent starts cold, so give it: the relevant file paths, the interface contracts, the test command, and the acceptance criteria. Don't rely on it seeing the whole repo.

---

## 1. Subagent roster

| Subagent | Model | Scope | Never does |
|---|---|---|---|
| **Orchestrator** (you, top level) | Opus 4.8 | Decompose, delegate, review diffs, own merge gate, integrate | Bulk implementation |
| **`schema-agent`** | Sonnet | SQLite schema, migrations, `sqlite-vec`/FTS5 setup, DB access layer | Business logic |
| **`anchor-core-agent`** | Opus 4.8 | The moat: anchor resolution (file+symbol→node), hash stamping, staleness diff, fusion join | Anything outside the core |
| **`retrieval-agent`** | Sonnet | Hybrid recall (BM25 + vector), RRF fusion, token-budget governor | Schema changes |
| **`indexer-adapter-agent`** | Sonnet | Wrap structural backend (codebase-memory-mcp output or minimal tree-sitter Py/TS) → nodes/edges | Reinventing a parser (Path A) |
| **`mcp-server-agent`** | Sonnet | FastMCP tool definitions, stdio/HTTP transport, tool schemas | Core algorithms |
| **`hooks-agent`** | Sonnet | SessionStart/PreToolUse/Stop hooks, JIT `context_for` wiring, graceful degradation | Retrieval internals |
| **`distribution-agent`** | Sonnet | `npx skills` packaging, CLI, `pyproject.toml`/wheels, AGENTS.md generator | Core logic |
| **`security-agent`** | Opus 4.8 | Secret stripping, `<private>` handling, redaction audit log, threat review | Feature work |
| **`test-agent`** | Sonnet | Unit + integration + MCP-protocol + golden tests, fixtures, coverage | Implementing features under test |
| **`verifier-agent`** | Opus 4.8 | Independent gate check per phase: reads diff, re-runs tests, adversarial cases, benchmark validation | Writing the code it reviews |
| **`benchmark-agent`** | Sonnet | Token-savings + latency harness on real repos, honest reporting | Tuning to game the numbers |

> In Claude Code these map to `Task`/subagent invocations. The orchestrator spawns them with an explicit contract and receives their final report. `verifier-agent` and the implementer for the same phase must be **separate spawns**.

---

## 2. Phase plan — each with owner, deliverable, gate, tests

### Phase 0 — Scaffold & decisions (Orchestrator + schema-agent)
**Deliverable:** repo skeleton, `pyproject.toml`, CI (GitHub Actions: lint + test on Py 3.10–3.13, macOS/Linux/Windows), `sqlite-vec` + FastMCP installing cleanly, `CLAUDE.md`/`AGENTS.md` bootstrap, empty test suite that runs green.
**Gate:** `pip install -e .` works on all 3 OSes in CI; `pytest` runs (0 tests, 0 errors); linter clean.
**Tests:** a smoke test that imports the package and opens an in-memory SQLite with the vec extension loaded.

### Phase 1 — Anchor mechanism + storage (anchor-core-agent + schema-agent) ⭐ the moat, build first
**Deliverable:** schema from the design doc; `remember(content, anchor?, scope)`, `forget()`, anchor resolution, `anchor_hash` stamping, and `check_freshness()` (hash-mismatch report). Build this **before** any indexer using synthetic nodes.
**Gate (verifier-agent, independent):**
- A memory anchored to a symbol is flagged stale when that symbol's `content_hash` changes, and fresh when it doesn't.
- Orphaned anchor (symbol deleted) is detected and reported, not crashed on.
- `scope` isolation holds: `agent-private` never leaks into `project-shared` queries.
**Tests (test-agent):** unit tests for hash stamping, staleness diff (fresh/stale/orphaned matrix), scope filtering; property test: for any node edit, freshness verdict is deterministic.

### Phase 2 — Retrieval + token governor (retrieval-agent)
**Deliverable:** hybrid `recall(query)` = BM25 (FTS5) + vector (sqlite-vec) fused via RRF; **no-ML mode** (BM25 only) works with zero model download; token-budget governor that ranks + truncates to a hard cap and reports what it dropped.
**Gate:** governor never exceeds the cap (fuzz with oversized memory sets); no-ML mode returns sane results with zero embedding model present; RRF beats either signal alone on a labeled fixture set.
**Tests:** ranking-quality test on a golden Q→expected-memory set (assert top-k contains the target); token-cap fuzz test; determinism test (same query → same order).

### Phase 3 — Structural backend adapter (indexer-adapter-agent)
**Deliverable (Path A):** adapter that ingests `codebase-memory-mcp` (or minimal tree-sitter Py/TS) output into `nodes`/`edges`; anchor resolver maps `file:symbol` → node id. (Path B: Rust indexer via maturin — separate sub-plan, slower.)
**Gate:** on a sample repo, anchors resolve to correct nodes; incremental re-index touches only changed files; `trace_callers()` returns correct reverse edges on a known fixture graph.
**Tests:** golden fixture repo with known call graph; assert node/edge counts and specific caller chains; incremental-index test (edit one file, assert only its nodes change).

### Phase 4 — MCP server + JIT hooks (mcp-server-agent + hooks-agent)
**Deliverable:** FastMCP server exposing the 10 tools; SessionStart brief (≤800 tok), **PreToolUse hook** → `context_for(path)` (≤400 tok) injecting only anchored memories for the file about to be edited; Stop hook for capture; graceful degradation to on-query tools where hooks are unavailable.
**Gate:** MCP protocol conformance (initialize, tools/list, tools/call round-trips); PreToolUse actually fires before an Edit in a live Claude Code session and injects file-scoped context; token caps honored end-to-end.
**Tests:** MCP-protocol test harness (spin the server, assert JSON-RPC handshake + each tool's schema + a call); hook integration test (simulate PreToolUse event → assert `context_for` payload is file-scoped and under budget); end-to-end smoke in Claude Code with a recorded transcript.

### Phase 5 — `why()` fusion + self-updating AGENTS.md + staleness watcher (anchor-core-agent + distribution-agent)
**Deliverable:** `why(symbol_or_file)` returns decision + surrounding call graph in one response; `generate_agents_md()` regenerates only sections whose anchors changed; git-diff staleness watcher.
**Gate:** `why()` on a fixture returns both the anchored decision and correct callers; regenerating AGENTS.md after an unrelated edit changes only the affected section (diff is minimal); watcher flags a changed anchor within one commit.
**Tests:** fusion golden test; AGENTS.md idempotence test (regenerate twice with no change → byte-identical); watcher test on a scripted git history.

### Phase 6 — Security & privacy (security-agent, Opus) — gate is mandatory
**Deliverable:** deterministic secret stripping at write time, `<private>` tag handling, redaction audit log, scope enforcement review.
**Gate (blocking):** a seeded corpus of fake keys/tokens/PII is 100% stripped before persistence; audit log records every redaction; no private-scope leakage under adversarial queries. **This gate blocks release regardless of feature completeness.**
**Tests:** secret-corpus test (known-bad strings must never appear in the DB); audit-log completeness test; adversarial scope-leak test.

### Phase 7 — Distribution & benchmarks (distribution-agent + benchmark-agent)
**Deliverable:** `npx skills add` packaging, CLI, prebuilt wheels + static binary (if Path B), install docs for Claude Code / Codex / Cursor / Gemini CLI / OpenClaw; honest benchmark harness.
**Gate:** clean install on all three OSes from a fresh machine (CI matrix); benchmark reports **real** token savings vs. file-exploration baseline on ≥3 real repos — claim only what the harness shows (target the defensible 10×, not marketing 120×).
**Tests:** fresh-env install test in CI; benchmark reproducibility test (same repo/query → savings within a tolerance band); cross-agent install smoke tests.

---

## 3. Testing strategy (layered)

| Layer | What it covers | Owner | Runs |
|---|---|---|---|
| **Unit** | Anchor hashing, staleness matrix, RRF math, token governor, secret stripping | test-agent | every commit (CI) |
| **Property/fuzz** | Freshness determinism, token cap never exceeded, scope isolation | test-agent | every commit |
| **Golden** | Retrieval quality (Q→expected memory), call-graph fixtures, `why()` fusion, AGENTS.md idempotence | test-agent | every commit |
| **MCP-protocol** | JSON-RPC handshake, tools/list schemas, tools/call round-trips | test-agent | every commit |
| **Integration (hooks)** | PreToolUse fires + injects file-scoped context under budget | hooks-agent + test-agent | pre-merge |
| **End-to-end (live agent)** | Recorded Claude Code session: session brief + JIT injection + capture | orchestrator | per phase 4+ |
| **Security** | Secret corpus 0-leak, audit completeness, scope adversarial | security-agent | blocking gate |
| **Benchmark** | Token savings + latency vs baseline on real repos | benchmark-agent | per release |
| **Cross-platform install** | pip/npx on macOS/Linux/Windows, Py 3.10–3.13 | CI matrix | per release |

**Fixtures to build once (reused everywhere):** a small golden repo with a known call graph; a labeled Q→memory retrieval set; a seeded secret/PII corpus; a scripted git history for staleness tests.

---

## 4. Verification model (how the gates actually work)

1. **Implementer subagent** finishes → returns diff + its own test run.
2. **Orchestrator (Opus)** reads the diff for contract compliance and obvious defects. Rejects if the contract isn't met — does not fix silently.
3. **`verifier-agent` (separate Opus spawn)** independently: re-runs the full suite from a clean checkout, writes 3–5 *adversarial* cases the implementer likely didn't consider, and for perf phases re-runs the benchmark. It reports pass/fail with evidence.
4. **Gate decision:** merge only if suite green **and** verifier's adversarial cases pass **and** the phase's objective metric is met. Otherwise the phase reopens with a specific failing case attached.
5. For Phase 6 (security) and any release, run the built-in `/security-review` and `engineering:code-review` skills as an additional independent pass.

The point: the author never certifies their own critical-path work, and every gate is an objective artifact (green suite, a number, a 0-leak result) rather than a judgment call.

---

## 5. `CLAUDE.md` bootstrap to drive the build

```markdown
# mnemex — Build Context

## Goal
MCP server giving coding agents ANCHORED memory: every decision is bound to a
file+symbol and hash-stamped, delivered JUST-IN-TIME via hooks. One SQLite file
(sqlite-vec + FTS5). Distributed via MCP + skills + AGENTS.md across all agents.

## Orchestration rules
- Opus 4.8 orchestrates; delegate bounded deliverables to subagents with written contracts.
- Subagents return diff + test results + self-report. Never blind-merge.
- A phase is done ONLY when its verification gate passes (tests green + verifier's
  adversarial cases pass + objective metric met).
- verifier-agent must be a SEPARATE spawn from the implementer. Authors don't grade themselves.
- Build the ANCHOR core (Phase 1) before any indexer, using synthetic nodes.

## Hard constraints
- No cloud/external API for core features. Local-first.
- Context injections respect hard token caps (800 session / 400 JIT). Never exceed.
- pip install works on macOS/Linux/Windows, Py 3.10–3.13, no ML model required (no-ML mode).
- Secrets/PII stripped at write time; Phase 6 security gate is blocking.
- Prefer wrapping an existing structural backend over reinventing a parser (Path A).

## Layout
mnemex/storage.py     → sqlite-vec + FTS5, schema, migrations
mnemex/anchors.py     → anchor resolve + hash stamp + staleness (THE MOAT)
mnemex/retrieval.py   → BM25+vector RRF + token governor
mnemex/indexer.py     → structural backend adapter
mnemex/server.py      → FastMCP tools
mnemex/hooks.py       → SessionStart/PreToolUse/Stop, JIT injection
mnemex/agents_md.py   → self-updating AGENTS.md
mnemex/security.py    → secret stripping + audit log
tests/fixtures/       → golden repo, Q→memory set, secret corpus, git history
```

First orchestrator prompt: *"Start Phase 1. Spawn schema-agent to build storage.py from the schema in CLAUDE.md, then anchor-core-agent to implement remember/forget/anchor-resolution/check_freshness against synthetic nodes. Then spawn verifier-agent (separate) to run the fresh/stale/orphaned matrix and scope-isolation adversarial cases. Report the gate result before touching Phase 2."*

---

## 6. Definition of Done (release)

All seven gates green · security gate 0-leak · MCP protocol conformance · cross-platform install verified in CI · benchmark reports honest, reproducible token savings on ≥3 real repos · JIT injection demonstrated live in at least Claude Code and one other agent · `npx skills add` + `pip install` both work from a clean machine · AGENTS.md auto-generation idempotent.

---

## Critical-path summary
Phase 1 (anchor core) is the moat and the highest risk — build and harden it first, independently verified, before investing in the indexer or server. Everything else is assembly around a proven core. Keep Opus on Phases 1, 5, and all verification; delegate the rest to Sonnet subagents under strict contracts.
