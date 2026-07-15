# Decision-Integrity Fixture Evaluation

## Scope

Offline synthetic fixtures exercising local constraints, append-only supersession, anchor freshness, and past-mistake warnings.

Every case runs against a new in-memory SQLite database through the local Mnemex APIs. No network, embeddings, or semantic provider is used.

## Result

**25/25 cases passed** (0 failed).

| Category | Passed | Total |
|---|---:|---:|
| compatible change | 5 | 5 |
| direct contradiction | 5 | 5 |
| freshness | 2 | 2 |
| legitimate supersession | 4 | 4 |
| repeated mistake | 5 | 5 |
| stale orphaned | 4 | 4 |

## Cases

| ID | Category | Expected | Actual | Pass |
|---|---|---|---|---|
| contradiction-01 | direct contradiction | 1 | 1 | yes |
| contradiction-02 | direct contradiction | 1 | 1 | yes |
| contradiction-03 | direct contradiction | 1 | 1 | yes |
| contradiction-04 | direct contradiction | 1 | 1 | yes |
| contradiction-05 | direct contradiction | 1 | 1 | yes |
| compatible-01 | compatible change | 0 | 0 | yes |
| compatible-02 | compatible change | 0 | 0 | yes |
| compatible-03 | compatible change | 0 | 0 | yes |
| compatible-04 | compatible change | 0 | 0 | yes |
| compatible-05 | compatible change | 0 | 0 | yes |
| supersession-01 | legitimate supersession | superseded | superseded | yes |
| supersession-02 | legitimate supersession | superseded | superseded | yes |
| supersession-03 | legitimate supersession | superseded | superseded | yes |
| supersession-04 | legitimate supersession | superseded | superseded | yes |
| freshness-01 | freshness | fresh | fresh | yes |
| freshness-02 | freshness | fresh | fresh | yes |
| freshness-03 | stale orphaned | stale | stale | yes |
| freshness-04 | stale orphaned | stale | stale | yes |
| freshness-05 | stale orphaned | orphaned | orphaned | yes |
| freshness-06 | stale orphaned | orphaned | orphaned | yes |
| mistake-01 | repeated mistake | warned | warned | yes |
| mistake-02 | repeated mistake | warned | warned | yes |
| mistake-03 | repeated mistake | warned | warned | yes |
| mistake-04 | repeated mistake | warned | warned | yes |
| mistake-05 | repeated mistake | warned | warned | yes |

## Limits

Remote semantic-model precision/recall, production prevalence, agent task completion, and end-to-end user outcomes.

The separate three-repository token figures are **context-delivery microbenchmarks**. They must not be read as decision-integrity accuracy, agent-quality, or universal token-savings claims.

## Reproduce

```text
python tools/evaluate_decision_integrity.py --format markdown
python tools/evaluate_decision_integrity.py --format json
```
