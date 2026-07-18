# Mnemex — Codex Execution Plan (Hackathon Final Push)

**Date:** 2026-07-18
**Audience:** Codex (or any coding agent) executing against this repo.
**Prime directive:** Submission quality > new features. Every task below has a
verification gate. Do not mark a task done until its gate passes. Do not start
a lower-priority task while a higher-priority gate is red.

**Standing gate commands (run after every task):**

```powershell
python -m pytest -q            # must stay green (314+ tests)
python -m ruff check src tests tools
```

---

## 0. Current state (verified 2026-07-18)

- 314 tests green, Ruff clean.
- A1 done: `src/mnemex/vector_backend.py` is the ONLY module importing
  `sqlite_vec`; `MNEMEX_NO_VEC=1` prevents even the import attempt
  (sentinel-tested in `tests/test_vector_backend.py`).
- A2 done: `tools/audit_release_artifacts.py` inventories wheel members,
  bans native payloads / nested archives / non-`py3-none-any` tags / forbidden
  core deps; wired into `.github/workflows/ci.yml`; writes
  `build/release-audit.json`.
- Demo fixed: text demo ends with a full WHY block (active decision as
  CURRENT, HISTORY via supersede link, freshness, callers, token count).
- A0 collector done: `tools/collect_windows_security_evidence.ps1`
  (read-only, redacting). Block NOT reproduced; Avast + Bitdefender both
  real-time on dev host (human decision pending, out of scope for Codex).
- Everything uncommitted on `main`. First action: commit current work in
  logical chunks (vector backend, auditor, demo fix, collector, plan docs).

**Invariant kernel — NEVER modify semantics of:** `anchors.py`,
`constraints.py`, `decision_guard.py`, `lifecycle.py`, `evidence.py`,
`security.py` blocking rules. Token caps (800 session / 400 JIT) are hard.
Only fresh, cited, explicit decisions may block. Overrides need actor+reason.

---

## P0 — Submission-critical (do in this order)

### P0.1 Commit the working tree

Split into reviewable commits: (1) lazy vector backend + tests,
(2) release auditor + tests + CI, (3) demo WHY fix, (4) evidence collector,
(5) plan/docs. Conventional messages (`feat:`, `fix:`, `docs:`, `chore:`).

**Gate:** `git status` clean; tests green at HEAD.

### P0.2 Evaluation scorecard (biggest credibility win)

`benchmarks/codex-guard-fixtures/` + `tools/evaluate_codex_guard.py` exist.

1. Run the evaluator; capture machine-readable results to
   `benchmarks/results/codex-guard-scorecard.json` (create dir).
2. Add a validator test `tests/test_scorecard.py` asserting the README
   numbers match the checked-in results file (no hand-typed drift).
3. Surface in README (see P0.3) as a table:
   - decision violations caught (n/N)
   - false blocks on legitimate evolution (target 0)
   - stale decisions correctly demoted to advisory (n/n)
   - avg injected context tokens vs cap
4. Label honestly: "recorded fixture replay, not a live-agent outcome claim."
   One reproduce command under the table.

**Gate:** fresh clone → reproduce command → same numbers; validator test green.

### P0.3 README overhaul (judges read this for 90 seconds)

Rewrite `README.md` top-down:

1. **One-line category claim first:**
   > Memory systems retrieve relevant history. ADR tools check written rules.
   > Mnemex verifies whether a past decision still governs the code — by
   > content hash, not by vibes — then gives the agent the minimum evidence
   > for the current edit.
2. **30-second judge path** (no API key, no vector extra), timed:
   ```
   pip install <wheel-url>           # or: pip install dist/mnemex-*.whl
   python -m mnemex demo --offline
   ```
   Show the expected BLOCKED output snippet inline.
3. **The killer contrast** (violation vs evolution) as a short two-column
   section: same guard BLOCKS a fresh-decision violation, STEPS ASIDE
   (advisory) after legitimate code evolution. Pull from
   `examples/violation-vs-evolution/`. This is the differentiator — above
   the fold, not buried.
4. **Scorecard table** from P0.2.
5. **Positioning table** (honest, 5 rows max): Mem0/Zep-class memory,
   adr-kit-class enforcement, Mnemex — columns: recall, enforcement,
   knows-when-stale (content hash), local-only, audited override.
6. Architecture diagram (existing ASCII fine), MCP tool list, security
   model summary (write-time redaction, token caps, no telemetry, loopback
   only), install matrix (core vs `[vector]` extra) + antivirus note: core
   ships zero native code and never imports `sqlite_vec`.
7. Delete/trim anything a judge won't read: long philosophy, duplicate
   sections, stale claims. Every claim must map to a test/tool/artifact in
   repo — if it doesn't, cut it or link the evidence.

**Gate:** claims audit — every number/claim in README has a checked-in
evidence path; judge path timed under 60s on a clean machine.

### P0.4 Diff-gate live walkthrough

`diff_guard.py` + CLI `check` exist. Verify end-to-end on a real repo:
init brain → remember decision with constraint → contradicting edit →
`git add` → staged-diff check → BLOCKED with citation; then legitimate
edit → advisory/pass. Write `examples/diff-gate-walkthrough.md`
(commands + expected output, PowerShell and bash variants). Cosmetic fixes
only (exit codes, unclear output) — no kernel changes.

**Gate:** walkthrough commands copy-paste clean; wrong verdict = STOP and
report, do not "fix" the guard.

### P0.5 Judge-path smoke in CI

Extend the existing clean-install CI step: after wheel install, run
`python -m mnemex demo --offline --json` and assert
`guard.blocked == true` and `verdict == "contradiction"` with a small
stdlib script.

**Gate:** CI green on all OS cells.

---

## P1 — High-leverage improvements (post-P0, pre-submission if time)

### P1.1 In-flight advisory nudge (validated by adr-kit research)

Post-edit hook: check touched file against active fresh decisions; if
governed, emit ONE advisory line
(`decision <id> may govern src/auth.py — run why`), <100ms, deterministic,
per-session cooldown, NEVER blocks. Extend `hooks.py`; no new MCP tool.

**Gate:** unit tests: governed file → one nudge; second touch → silence
(cooldown); ungoverned file → nothing; latency under 100ms on the golden
fixture repo.

### P1.2 Codebase archaeology (cold-start killer)

`mnemex bootstrap` CLI: scan repo via existing indexer, detect obvious
standing decisions (dependency manifest choices, framework, layout
patterns), emit PROPOSED decisions as JSON for human approval — never
auto-persist as active, never auto-tag constraints. Approved items enter
via existing `remember` path with `source="bootstrap"` provenance.

**Gate:** run on this repo proposes plausible decisions (e.g. "fastmcp is
the only core runtime dep"); zero writes without approval flag;
sanitization applied; test for the no-write default.

### P1.3 Trend history in TUI

Append-only `health_snapshots` table (additive, transactional migration):
per run store fresh/stale/orphaned counts, blocks, overrides. TUI shows
delta (`stale 3 → 1`). Keep it dumb.

**Gate:** two runs produce delta line; migration rollback test; v3 DB
opens unchanged when feature unused.

### P1.4 Version bump + release hygiene

`0.1.0` → `0.2.0`; update existing `CHANGELOG.md` (keep-a-changelog):
lazy vector backend, artifact auditor, demo WHY, collector, scorecard.
Rebuild wheel + bundle; refresh SHA256SUMS.

**Gate:** `python tools/audit_release_artifacts.py dist` exit 0; checksums
match rebuilt artifacts.

---

## P2 — Only if everything above is green

- Progressive retrieval (compact index → ID-based evidence expansion) per
  MNEMEX_COMPETITIVE_CONVERGENCE_PLAN.md Phase 1.
- Deterministic Markdown decision-ledger export (SQLite stays
  authoritative).
- Undocumented-decision harvest from branch diff + commit messages →
  observation drafts (requires Phase-2 inbox promotion boundary; do NOT
  ship without it).

## Explicit DON'Ts (settled; do not relitigate)

- No new MCP tools (3-tool ceiling reserved for Phase 2/3; none needed
  for P0/P1).
- No capture-everything, no LLM-generated blocking rules, no
  auto-promotion of observations/imports to constraints.
- No Chroma/Neo4j/Postgres, no cloud dependency, no telemetry, no unsigned
  standalone executable, no antivirus-exclusion instructions anywhere.
- No kernel semantic changes (§0 invariants). If a task appears to need
  one, stop and report instead.
- No README claims without checked-in evidence.

## Reporting contract

After each task: diff summary, test output tail, gate verdict
(PASS/FAIL + evidence path). Red gate = not done — fix within scope or
report the blocker. Never blind-merge generated code.
