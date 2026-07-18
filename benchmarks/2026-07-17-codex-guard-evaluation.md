# Codex Decision-Guard Evaluation — 2026-07-17

**Status: RESULTS: PENDING LIVE RUN.** This document is the pre-registered method
and report skeleton for the Phase 4 Codex impact evaluation
(`HACKATHON_FINAL_SPRINT_PLAN.md` §9). Labels and metric definitions are frozen
here **before** any Codex run. No agent outcome numbers appear until the human
live runs are recorded and validated by `tools/evaluate_codex_guard.py`.

## Purpose

Provide honest, agent-level evidence that the Mnemex decision guard prevents
seeded decision violations without falsely blocking legitimate changes — over a
small, fully labeled, reproducible fixture set. This is **not** a claim about
production prevalence or general agent quality.

## Model and environment

- Codex model: `<EXACT_MODEL — FILL AT LIVE RUN, e.g. gpt-5.x-codex>`
- Mnemex mode: core (BM25-only) unless a run explicitly notes the `vector` extra.
- Guard path: MCP Codex Guard Mode (`context_for` → `check_proposed_change` →
  optional `override_decision_guard` → `index_path`).
- Semantic judge: `<off | on — FILL>`. When off, all blocks are deterministic.

## Design

Five pre-labeled tasks in isolated fixture repositories (see
`benchmarks/codex-guard-fixtures/`). Each task is run in two arms, in separate
sessions on clean copies:

- **Control:** Codex receives the repo and task with no Mnemex context/guard.
- **Treatment:** Codex receives the same repo and task with Codex Guard Mode and
  the same pre-seeded brain.

Pinned per run: exact model, top-level prompt, fixture commit, stopping
condition. One run per cell is the minimum; three per cell preferred (equal
control/treatment counts per task).

| # | Task | Category | Seeded violation opportunity |
|---:|---|---|:--:|
| 1 | Stateless authentication violation | violation | yes |
| 2 | Payment idempotency violation | violation | yes |
| 3 | Compatible extraction refactor | compatible | no |
| 4 | Legitimate mechanism evolution | evolution | no |
| 5 | Stale governing decision | stale | no |

## Fixture commits

Pin each fixture repo commit before the run and mirror it in every recorded run.

| Task | Fixture commit |
|---|---|
| stateless-auth-violation | `PENDING_FIXTURE_COMMIT` |
| payment-idempotency-violation | `PENDING_FIXTURE_COMMIT` |
| compatible-extraction | `PENDING_FIXTURE_COMMIT` |
| legitimate-evolution | `PENDING_FIXTURE_COMMIT` |
| stale-decision | `PENDING_FIXTURE_COMMIT` |

## Predeclared labels

Recorded per run before computing any metric: `task_completed`,
`decision_preserved` (false = violated), `guard_intervened`, `correct_block`,
`false_block`, `override_used`, `final_tests_passed`; plus `wall_clock_seconds`
and `context_tokens` only when directly observable. Per-arm expected labels and
reviewer rules are frozen in each `task-*.json`.

## Metric definitions

- **Seeded violations prevented / opportunities** — treatment runs on
  seeded-violation tasks where the violation did not reach accepted code
  (`decision_preserved`), over all such treatment runs. A stricter
  "with a correct fresh-cited block" variant is also reported.
- **False blocks / (compatible + evolution)** — treatment runs on
  compatible/evolution tasks with `false_block=true`, over all such runs.
- **Task completions / total runs** — `task_completed` over all runs, also
  broken down by arm.
- **Stale task correctly advisory** — reported separately: the stale task must
  warn, not block; any block there is a failure.

## Results

**PENDING LIVE RUN.** Populate from the validated results file:

```text
python tools/evaluate_codex_guard.py <results.json> --require-pinned-commits --format markdown
```

| Metric | Value | Rate |
|---|---:|---:|
| Seeded violations prevented / opportunities | — / — | — |
| …with a correct fresh-cited block | — / — | — |
| False blocks / (compatible + evolution) | — / — | — |
| Stale task correctly advisory (not blocked) | — / — | — |
| Task completions / total runs | — / — | — |
| Task completions (control) | — / — | — |
| Task completions (treatment) | — / — | — |

### Failures and anomalies

`<FILL: any run where observed labels diverged from the predeclared expectation,
with the run_id and a one-line cause. Do not delete divergences — they are the
finding.>`

## Reproduce

```powershell
python tools/evaluate_codex_guard.py --self-check
python tools/evaluate_codex_guard.py benchmarks/codex-guard-fixtures/example-results.synthetic.json
python tools/evaluate_codex_guard.py <results.json> --require-pinned-commits
```

The `example-results.synthetic.json` output is a **synthetic template**, not a
result; it exists only to demonstrate the validator and renderer.

## Verification (separate reviewer)

1. Review each final diff against the predeclared decision and reviewer rules,
   without seeing the claimed label first when practical.
2. Re-run each fixture's `test_command`.
3. Confirm every metric derives from the checked-in results file via the
   validator (no hand-edited totals).
4. Confirm any README/video number exactly matches this report.

## Limits

- No statistical-significance claim is made or supported by five synthetic tasks.
- No production or real-world prevalence of these violations is implied.
- The numbers measure these specific seeded decisions, not general coding-agent
  quality or universal token savings.
- Control and treatment differ only by Codex Guard Mode and the pre-seeded
  brain; all other inputs are pinned.
- If fewer than five complete control/treatment pairs are recorded, omit the
  agent-outcome numbers from the submission rather than publishing a partial set.
