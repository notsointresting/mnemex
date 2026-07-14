# Fact-Check + Improved Build Plan: Codebase-Aware Agent Memory MCP Server

*Reviewed against live sources, July 2026. The Perplexity research is mostly directionally correct but has inflated numbers, one factually wrong "unbuilt gap" claim, and a strategy that will lose on the metrics it promises. This document fixes those, then upgrades the plan into something that can actually win across every agent.*

---

## Part 1 — What the Perplexity research got RIGHT vs. WRONG

### Verdict at a glance

| Claim in the research | Status | Reality |
|---|---|---|
| The "re-explain tax" is a real, top pain | ✅ True | Widely discussed across r/mcp, r/ClaudeCode, and multiple 2025–2026 essays. |
| Two types of forgetting: *fact* vs *shape* | ✅ True (framing real) | The getunblocked post ("Why Claude Code Forgets Your Codebase") and Joe Njenga's essay both make this exact distinction. Solid foundation. |
| `rohitg00/agentmemory` exists, is mature | ✅ True | Real and active. BUT architecture is misstated (see below). |
| `MemPalace/mempalace` ~43k stars | ⚠️ Half-true | Stars are real but **bought** — an independent audit found ~42k purchased stars, and it's "ChromaDB with a celebrity name." Do NOT cite its star count as a demand signal. |
| `codebase-memory-mcp` (DeusData) exists, tree-sitter graph | ✅ True | Real. **158 languages** (not 155), single static **C** binary, sub-ms queries. arXiv paper 2603.27277 is real. |
| `Gentleman-Programming/engram` (Go, SQLite+FTS5) | ✅ True | Real and accurately described. |
| Benchmarks: "83% answer quality, 10× fewer tokens" | ✅ True-ish | Real from the paper, across 31 repos. The repo's marketing now claims up to **120× fewer / 99% fewer tokens** — treat the higher numbers as marketing, the 10× as the defensible figure. |
| "This combination is explicitly unbuilt" | ❌ **FALSE** | `yuga-hashimoto/codebase-memory` already does almost exactly the proposed project: MCP server, persistent memory of architecture decisions, patterns, conventions across sessions, works with Claude/Cursor/any MCP. `EtienneBBeaulac/memory-mcp` is a third. The gap is **narrower and more crowded** than claimed. |
| agentmemory = "four-tier pipeline, BM25+vector+graph RRF, 17,800 stars" | ❌ Mostly fabricated | The real agentmemory is built on the `iii-engine` (Worker/Function/Trigger), ships **15 skills + 6 lifecycle hooks**, and proxies **53 tools**. The "four-tier RRF" description and the exact star count appear to be Perplexity hallucinations. Its real strength is **distribution** (auto-installs across 50+ agents via the `skills` CLI), which the research completely missed. |
| Star counts generally (7,335 / 3,163 / 2,389 etc.) | ⚠️ Untrustworthy | Treat every precise star number in that report as unverified. The repos exist; the counts are likely invented. |
| AGENTS.md is a cross-tool standard | ✅ True — and bigger than stated | Now stewarded by the Linux Foundation, adopted by 60k+ projects, read natively by Codex, Cursor, Copilot, Windsurf, Amp, Devin, Aider, Zed, Jules, VS Code, Junie — and Claude Code now reads it too. This is a real moat opportunity. |

### The two claims that change the strategy

1. **The "unbuilt gap" is already partly built.** At minimum three repos (`codebase-memory`, `memory-mcp`, plus the structural `codebase-memory-mcp`) overlap the proposed feature set. You cannot win by being "the one that combines structural + episodic." Someone already glued those together. You win on *how the two are fused* and *how it's delivered*.

2. **The real winner's advantage is distribution, not features.** agentmemory's edge isn't its pipeline — it's that `npx skills add` drops it into 50+ agents automatically, and engram's edge is a single zero-dependency binary. Perplexity's plan optimizes for a feature checklist and ignores the thing that actually drives adoption.

---

## Part 2 — Three fatal flaws in the proposed plan (and the fixes)

### Flaw 1: Python will lose the benchmark war it's picking

The plan promises sub-ms queries and a tiny token footprint, then chooses **pure Python + tree-sitter + ChromaDB**. The incumbent it's benchmarking against (`codebase-memory-mcp`) is **hand-written C with a static binary**. Python tree-sitter indexing of a 49K-node repo will be seconds-to-minutes, not the ~6s / 1.2s incremental the paper reports, and ChromaDB adds a heavy dependency tree that breaks the "zero deps" promise.

**Fix — don't re-fight the structural battle in a slower language.** Two viable paths:

- **Path A (recommended): Don't rebuild the graph at all.** Treat `codebase-memory-mcp` (or tree-sitter-graph, or an LSP) as a *pluggable structural backend*. Your project becomes the **memory + fusion + retrieval brain** on top. This is faster to build, avoids a losing benchmark, and is genuinely unbuilt.
- **Path B: If you must own the indexer, put the hot path in Rust** and ship it as a pip wheel via `maturin`. Still `pip install`, still cross-platform, but competitive speed. Python stays for orchestration only.

### Flaw 2: ChromaDB breaks the "single file / zero deps" story

ChromaDB pulls in a large dependency chain and has known read/write concurrency bottlenecks. Every source comparing it to `sqlite-vec` for an *embedded, local-first, single-file* tool favors `sqlite-vec`.

**Fix — use `sqlite-vec` (or FTS5-only mode).** One SQLite file holds *both* the structural graph and the episodic memory *and* the vectors. That gives engram's "one binary, one file" simplicity while keeping semantic search. Offer a **no-ML mode** (FTS5 keyword + BM25 only) so `pip install` works with zero model download for users who don't want a 80MB embedding model.

### Flaw 3: The plan bundles 10 tools but has no unique mechanism

"Structural + episodic + AGENTS.md generator" is a feature list, not an invention. `codebase-memory` already stores decisions/conventions. The AGENTS.md generator is nice but trivially copyable in a weekend. There's no defensible core.

**Fix — build one genuinely novel mechanism (below) and make everything else serve it.**

---

## Part 3 — The sharper wedge: **Fused, anchored, just-in-time memory**

Everyone else does *either* structural graph *or* fact memory, and injects at session start. The unbuilt, defensible idea is the **fusion mechanism** between them:

> **Decision anchors** — every episodic memory (decision, convention, correction, pattern) is *anchored to a structural location* (a file + symbol, i.e. a node in the graph) and stamped with that node's `content_hash`. Memory and code are the same object seen from two sides.

This single mechanism unlocks four things nobody ships together:

1. **Automatic staleness.** When `git diff` shows the anchored symbol changed, the memory's hash mismatches → it's auto-flagged "possibly stale, code changed 2h ago" instead of being confidently served as current. This directly kills agentmemory's known weakness.
2. **Just-in-time (JIT) retrieval, not session-start dumping.** Via a **PreToolUse hook**, the moment the agent is about to `Edit apps/web/lib/auth.ts`, the server injects *only* the decisions/patterns/callers anchored to that file — the exact "shape forgetting" fix the getunblocked analysis calls for, delivered at the moment of decision. No 2–5K preload tax.
3. **Shape + fact answered in one query.** "Why is auth done this way?" returns the decision *and* the call graph around it, because they're linked. No other tool joins these.
4. **Self-maintaining AGENTS.md.** Because decisions are anchored and hash-tracked, the generated AGENTS.md can regenerate only the sections whose anchors changed — it never goes stale.

That's the moat: **not the graph, not the store, but the join between them plus JIT delivery.**

---

## Part 4 — Additional innovations layered on the wedge

- **Token-budget governor.** Every injection path takes a hard `max_tokens` budget (default 800 for session brief, 400 for JIT). The server *ranks and truncates* to fit, and reports what it dropped. Makes the "< 800 tokens" promise a guarantee, not a hope.
- **Provenance + confidence on every returned fact.** Each memory carries `source` (who/what wrote it), `last_verified`, `anchor_status` (fresh/stale/orphaned), and a confidence score. The agent can reason about trust instead of blindly accepting.
- **Hybrid retrieval by default (RRF).** BM25 (FTS5) + vector (sqlite-vec) fused with Reciprocal Rank Fusion. Works in no-ML mode (BM25 only) and upgrades automatically when embeddings are present. This is the retrieval quality agentmemory is *claimed* to have — actually build it.
- **Multi-agent shared brain with scopes.** One SQLite brain, three scopes: `agent-private`, `project-shared`, `user-global`. Claude Code, Codex, and OpenClaw writing to the same project share decisions; private scratch stays private. This is the real "works for all agents" story.
- **Decay + reinforcement done honestly.** Importance rises on access, decays on age, drops to zero on `forget()` — but a *stale* memory (anchor hash changed) is never silently decayed away; it's surfaced for human/agent reconciliation.
- **Deterministic secret stripping at write time**, plus a `<private>` tag convention, plus a redaction audit log so users can verify nothing leaked.

---

## Part 5 — Revised architecture

```
                    ┌──────────────────────────────────────────────┐
                    │                 mnemex (core)                 │
                    │                                               │
   ┌────────────┐   │   ┌───────────────────────────────────────┐  │
   │ Structural │   │   │           ONE SQLite file             │  │
   │  backend   │──▶│   │  ┌────────────┐   ┌────────────────┐  │  │
   │ (pluggable)│   │   │  │  graph     │◀─▶│  memories      │  │  │
   │ • cbm-mcp  │   │   │  │  nodes/    │ANCHOR  decisions/   │  │  │
   │ • treesit. │   │   │  │  edges     │  │  conventions +   │  │  │
   │ • LSP      │   │   │  │  (hash)    │  │  vec + fts5      │  │  │
   └────────────┘   │   │  └────────────┘   └────────────────┘  │  │
                    │   └───────────────────────────────────────┘  │
                    │        │Fusion engine (RRF + anchor join)     │
                    │        │Token governor  │  Staleness watcher  │
                    │   ┌────▼────────────────────────────────────┐ │
                    │   │       MCP server (FastMCP)  + HOOKS      │ │
                    │   │  SessionStart · PreToolUse · Stop        │ │
                    │   └─────────────────────────────────────────┘ │
                    └───────────────────┬──────────────────────────┘
             MCP (stdio/HTTP) + Skills (AGENTS.md) + CLI + hooks
        ┌───────────────┬───────────────┼───────────────┬───────────────┐
   Claude Code       Codex CLI       OpenClaw        Gemini CLI      Cursor/Windsurf…
```

**Key differences from the Perplexity design:** structural backend is *pluggable* (don't reinvent C-speed indexing); *one* SQLite file not two DBs; the anchor join and token governor are first-class; hooks (not just tools) drive JIT injection; distribution is MCP **+ skills + CLI + hooks**, not MCP-only.

### Storage (one file, `sqlite-vec`)

```sql
-- structural
CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, name TEXT, file TEXT,
                   line_start INT, content_hash TEXT, language TEXT);
CREATE TABLE edges(from_id TEXT, to_id TEXT, type TEXT, confidence REAL);

-- episodic, ANCHORED to a node
CREATE TABLE memories(
  id TEXT PRIMARY KEY, type TEXT, content TEXT, rationale TEXT,
  anchor_node_id TEXT,          -- ← the fusion link (nullable = global fact)
  anchor_hash TEXT,             -- ← hash at write time, for staleness
  scope TEXT,                   -- agent-private | project-shared | user-global
  source TEXT, confidence REAL, importance REAL,
  created_at TEXT, last_accessed TEXT, last_verified TEXT, tags TEXT
);
CREATE VIRTUAL TABLE memories_vec USING vec0(embedding FLOAT[384]);
CREATE VIRTUAL TABLE memories_fts USING fts5(content, rationale, tags);
```

### The tools (trimmed and JIT-aware)

Session-start `get_context_brief()` (≤800 tok) · JIT `context_for(path)` (≤400 tok, hook-driven) · `remember(content, anchor?, scope)` · `recall(query)` hybrid RRF · `why(symbol_or_file)` fused decision+callgraph · `trace_callers()` · `check_freshness()` (hash diff report) · `forget()` · `generate_agents_md()` · `index(path, backend)`. Ten tools, but the two that matter — `context_for` and `why` — don't exist anywhere else.

---

## Part 6 — The real growth lever: distribution across ALL agents

Perplexity's launch plan (Show HN, subreddits, Discord) is fine but treats distribution as marketing. The incumbents prove distribution is *architecture*:

- **Ship as skills, not just an MCP server.** Support `npx skills add <you>` so it auto-installs into Claude Code, Cursor, Cline, Codex, Windsurf, Gemini CLI, OpenCode, Goose, Roo, Trae, and 40+ others — the exact channel agentmemory used to win. MCP-only reaches a fraction of that.
- **AGENTS.md is your Trojan horse.** Auto-generate and keep-fresh the one file every agent reads. It's the universal write target; being the tool that maintains it makes you sticky across the whole ecosystem.
- **Hooks are the "for all agents" glue.** SessionStart/PreToolUse/Stop hooks give JIT behavior in Claude Code today, and degrade gracefully to on-query tools for agents without hooks. Same brain, every agent.
- **One binary/one file ergonomics.** Match engram's "no Node, no Python-hell, one SQLite file" install story. If you go Path B (Rust core), ship prebuilt wheels + a single static binary for the CLI.

**Slogan that's actually differentiated:** *"Not another memory list. A brain that anchors every decision to the exact line it's about — and hands it back the instant your agent touches that line."*

---

## Part 7 — Naming

`codebase-mind` is fine but sits in a crowded `codebase-*` namespace (`codebase-memory`, `codebase-memory-mcp`, `codebase-memory`), which hurts search discoverability and invites "which one is this?" confusion. Consider a distinct, ownable name that signals the anchor mechanism — e.g. **`anchor`/`anchormem`**, **`mnemex`**, **`recall-graph`**, or **`throughline`**. Check PyPI + npm + GitHub before committing; grab the matching `npx skills` handle at the same time.

---

## Part 8 — Realistic build plan (corrected)

| Phase | Goal | Reality-checked note |
|---|---|---|
| 0 | Decide Path A (pluggable backend) vs B (Rust core). Scaffold, `sqlite-vec`, FastMCP. | Path A gets you to a demo in **week 1**, not week 3. |
| 1 | **Anchor mechanism + storage.** `remember(anchor)`, hybrid `recall` (BM25 first, vec optional). | This is the moat — build it first, before any indexer. |
| 2 | Structural backend adapter (wrap `codebase-memory-mcp` output, or minimal tree-sitter for Py/TS). Anchor resolution: file+symbol → node id. | If Path A, this is an adapter, not a parser. Days, not weeks. |
| 3 | **JIT via hooks** — PreToolUse → `context_for(path)` with token governor. SessionStart brief. Test in Claude Code. | The headline demo. Record the before/after token count here. |
| 4 | Staleness watcher (git diff → hash mismatch → flag) + `why()` fused query + self-updating AGENTS.md. | Directly attacks the incumbents' documented weakness. |
| 5 | Distribution: `npx skills add`, CLI, prebuilt wheels/binary, secret stripping + audit log. | Distribution is a feature phase, not an afterthought. |
| 6 | README-as-product, benchmarks (honest 10×, not 120×), Show HN + skills-registry listing. | Lead with `context_for`/`why` demo, not the feature table. |

Honest timeline: a compelling **v0.1 demo in ~2 weeks** (anchor + JIT + one repo), publishable **v0.2 in ~5–6 weeks**. The Perplexity 6-week estimate is only realistic if you *don't* try to out-C the structural incumbent.

---

## Bottom line

The research is a good map with unreliable coordinates. The pain is real, the "fact vs shape" framing is gold, and the repos exist — but the star counts are largely invented, MemPalace's are literally bought, and the headline "explicitly unbuilt gap" is false: the structural+episodic combo already ships in several repos. Winning requires (1) not re-fighting the C-speed structural benchmark in Python, (2) one SQLite file via `sqlite-vec` not ChromaDB, (3) a real novel mechanism — **decision anchors + JIT hook injection** — instead of a feature checklist, and (4) treating distribution (skills + AGENTS.md + hooks) as the core product, which is exactly how the current leaders actually won.

---

### Sources
- [rohitg00/agentmemory](https://github.com/rohitg00/agentmemory)
- [MemPalace/mempalace](https://github.com/MemPalace/mempalace) · [purchased-stars audit](https://gist.github.com/roman-rr/0569fc487cc620f54a70c90ab50d32e3)
- [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) · [arXiv 2603.27277](https://arxiv.org/html/2603.27277v1)
- [yuga-hashimoto/codebase-memory](https://github.com/yuga-hashimoto/codebase-memory) · [EtienneBBeaulac/memory-mcp](https://github.com/EtienneBBeaulac/memory-mcp)
- [Gentleman-Programming/engram](https://github.com/Gentleman-Programming/engram)
- [getunblocked: Why Claude Code Forgets Your Codebase](https://getunblocked.com/blog/claude-code-forgets-codebase/)
- [AGENTS.md cross-tool guide](https://www.deployhq.com/blog/ai-coding-config-files-guide)
- [asg017/sqlite-vec](https://github.com/asg017/sqlite-vec)
