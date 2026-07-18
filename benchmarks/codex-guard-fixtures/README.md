# Codex Decision-Guard Fixtures

Pre-labeled task definitions for the Phase 4 Codex impact evaluation
(`HACKATHON_FINAL_SPRINT_PLAN.md` §9). This directory contains **only
scaffolding**: task definitions, a clearly-labeled synthetic results template,
and the reviewer rules. The live control/treatment Codex runs are a **human
step** performed later. Nothing here fabricates agent outcomes.

## Files

| File | Purpose |
|---|---|
| `task-01-stateless-auth-violation.json` | Seeded violation: prompt tempts server-side sessions. |
| `task-02-payment-idempotency-violation.json` | Seeded violation: naive retry can double-charge. |
| `task-03-compatible-extraction.json` | Behavior-preserving refactor (must NOT be blocked). |
| `task-04-legitimate-evolution.json` | Mechanism changes, outcome preserved (must NOT be blocked). |
| `task-05-stale-decision.json` | Governing symbol already changed; guard must warn, not block. |
| `example-results.synthetic.json` | **Synthetic template only.** Not real outcomes. Shows the exact results shape. |

Each `task-*.json` carries its `category`, whether it is a
`is_seeded_violation_opportunity`, the seeded anchored decision(s), the task
prompt, predeclared `expected_labels` for both arms, and human `reviewer_rules`.

## Experiment protocol (human)

For every task, run both arms in **separate sessions on clean fixture copies**:

- **Control:** Codex gets the repo and prompt with **no** Mnemex context/guard.
- **Treatment:** Codex gets the same repo and prompt **with** Codex Guard Mode
  and the same pre-seeded brain.

Pin and record: the exact Codex model, the top-level prompt, the fixture commit,
and the stopping condition. One run per cell is the minimum; three per cell are
preferred if quota permits (keep control and treatment counts equal per task).

### Suggested per-task setup

1. Create the fixture repo under `repos/<repo_dir>` with the `primary_file` and a
   `test_command` that encodes the behavior the decision protects.
2. `mnemex init` + `mnemex index` the **clean baseline**, then seed the
   decision(s) from `seeded_brain` via `remember_decision` (use the listed
   `tags` so deterministic constraints apply).
3. For the stale-decision task only, follow its `setup_note`: mutate the
   governing symbol after indexing so the decision is genuinely stale, and do
   **not** re-index before the run.
4. Commit the fixture repo and record the commit hash.

## Recording results

Produce one results JSON (see `example-results.synthetic.json` for the exact
shape). Required per run: `run_id` (unique), `task_id` (matching a fixture),
`arm` (`control`/`treatment`), `fixture_commit`, and all seven `labels`
(`task_completed`, `decision_preserved`, `guard_intervened`, `correct_block`,
`false_block`, `override_used`, `final_tests_passed`). Set `synthetic_example`
to `false` and put the exact model in `model`.

Fixture-commit matching: every run's `fixture_commit` must equal the `commit`
recorded in its `task-*.json`. Ship placeholders as `PENDING_FIXTURE_COMMIT`;
after pinning real commits, update both the fixture files and the results, then
validate with `--require-pinned-commits` to forbid leftover placeholders.

## Validate and render

```powershell
python tools/evaluate_codex_guard.py --self-check
python tools/evaluate_codex_guard.py benchmarks/codex-guard-fixtures/example-results.synthetic.json
python tools/evaluate_codex_guard.py <your-results.json> --require-pinned-commits --format markdown
```

The validator rejects missing labels, duplicate run IDs, mismatched fixture
commits, and incomplete/unbalanced control/treatment pairs, and computes the
three Phase 4 metrics. It never runs Codex and makes no network call.
