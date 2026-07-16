# Violation vs Evolution — the judgment a hash cannot make

Two proposed changes to `process_payment` look similar at the diff level.
One is a legitimate refactor; one silently breaks the anchored decision
"payment writes must be idempotent". Deterministic anchor hashing detects
*that* the symbol changed — only a semantic judge can tell *which kind* of
change it is.

## Setup

```bash
python setup.py     # creates fixture.sqlite3, indexes payments.py, anchors the decision
```

## Case 1 — legitimate evolution (must NOT be blocked)

The refactor in `case_evolution.diff` moves the idempotency check into a
helper. Behavior preserved.

```bash
mnemex check src/payments.py \
  "Refactor: move the idempotency-key validation and duplicate-charge check into a _ensure_single_charge helper called before the ledger write" \
  --db fixture.sqlite3 --replay replay/evolution.json
```

Expected: `"blocked": false`, `"verdict": "compatible"`.

## Case 2 — subtle violation (must be BLOCKED)

`case_violation.diff` adds a retry loop and drops the idempotency check —
a transient error after a successful write now double-charges.

```bash
mnemex check src/payments.py \
  "Add a retry loop around the ledger write; remove the idempotency-key requirement and the duplicate-charge early return" \
  --db fixture.sqlite3 --replay replay/violation.json
```

Expected: `"blocked": true`, `"verdict": "contradiction"`, confidence 0.96,
citing the fresh anchored decision.

## About replay mode

`--replay` substitutes a recorded semantic verdict so the fixture runs
deterministically with no API key; results are labeled `provider: replay`
and must never be presented as a live call. To run the same cases live,
install the `openai` extra, set `OPENAI_API_KEY` and
`MNEMEX_SEMANTIC_JUDGE_ENABLED=true`, and drop the `--replay` flag. The
bundled JSON files are representative verdicts; re-record them from a live
run for your own demos.

Clean up with `rm fixture.sqlite3`.
