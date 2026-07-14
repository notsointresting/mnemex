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
