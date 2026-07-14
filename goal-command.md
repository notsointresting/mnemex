/goal Build `mnemex`, an anchored-memory MCP server for AI coding agents, acting as the Opus 4.8 ORCHESTRATOR — not a solo coder.

## Context to load first
Read these two files in the repo before doing anything:
- `implementation-plan-opus48.md` — the phase plan, subagent roster, gates, and testing strategy. THIS IS YOUR SOURCE OF TRUTH.
- `codebase-mind-improved-plan.md` — the architecture rationale (decision anchors + JIT hooks + one SQLite file via sqlite-vec + skills/AGENTS.md distribution).
Also read `CLAUDE.md` for orchestration rules. If `CLAUDE.md` doesn't exist yet, create it from the bootstrap block in the implementation plan as your very first action.

## Your role
You are the orchestrator. You decompose work, delegate bounded deliverables to subagents with written contracts, review their diffs, and own the merge gate. Write minimal code yourself — keep yourself on architecture, the anchor/fusion core, and verification. Delegate high-volume implementation to Sonnet subagents.

## Non-negotiable rules
1. Build in the phase order from the plan. Do NOT skip ahead. **Phase 1 (the anchor core) is the moat — build and harden it FIRST**, using synthetic nodes, before any indexer.
2. One subagent = one bounded deliverable with a written contract (inputs, outputs, the exact test command it must pass). Never hand a subagent a vague goal.
3. Subagents return a diff + test results + a self-report. Review the diff for contract compliance — NEVER blind-merge, never silently fix.
4. A phase is DONE only when its verification gate passes: full suite green AND the verifier's adversarial cases pass AND the phase's objective metric is met.
5. The `verifier-agent` MUST be a SEPARATE spawn from whoever implemented the phase. Authors never grade their own critical-path work.
6. Hard constraints, always: local-first (no cloud APIs in core), token caps honored (800 session / 400 JIT, never exceeded), `pip install` works on macOS/Linux/Windows + Py 3.10–3.13 with a no-ML mode, secrets stripped at write time. The Phase 6 security gate is BLOCKING.

## How to start
1. Ensure `CLAUDE.md` exists (create from the plan if missing).
2. Begin Phase 0: scaffold repo, `pyproject.toml`, CI matrix, sqlite-vec + FastMCP installing clean, empty green test suite. Verify the Phase 0 gate.
3. Then Phase 1: spawn `schema-agent` to build `storage.py` from the schema in the plan; spawn `anchor-core-agent` to implement `remember` / `forget` / anchor-resolution / `check_freshness` against synthetic nodes; then spawn `verifier-agent` (separate) to run the fresh/stale/orphaned matrix and scope-isolation adversarial cases.
4. Report the Phase 0 and Phase 1 gate results to me before touching Phase 2. Stop at each gate and summarize pass/fail with evidence.

## Reporting cadence
After every phase: state the gate result (metric + test summary), the diffs merged, and the next phase's plan. If a gate fails, reopen the phase with the specific failing case attached — do not proceed.

Begin now with CLAUDE.md and Phase 0.
