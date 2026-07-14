# mnemex — Agent Build Context

## Sources of truth
Read `implementation-plan-opus48.md`, `codebase-mind-improved-plan.md`,
`goal-command.md`, and `CLAUDE.md` before changing the project. Build phases in plan
order; Phase 1's anchor core must be complete before any indexer work.

## Goal
Build a local-first MCP server that anchors each remembered decision to a
file/symbol and content hash, then delivers relevant context just in time. Use
one SQLite file with sqlite-vec and FTS5, and distribute context through MCP,
skills, hooks, and AGENTS.md.

## Orchestration rules
- Give each subagent one bounded contract with inputs, outputs, and exact tests.
- Require diffs, test results, and a self-report; never blind-merge.
- A phase is complete only after its objective gate passes.
- Use a separate verifier instance; authors do not grade their own work.
- Keep changes within the current phase and do not add speculative APIs.

## Hard constraints
- Core features stay local and require no cloud API or ML model.
- Context caps are hard limits: 800 tokens at session start and 400 for JIT.
- Support macOS, Linux, and Windows on Python 3.10–3.13.
- Strip secrets and PII at write time; the Phase 6 security gate is blocking.
- Prefer a pluggable structural backend over implementing another parser.
