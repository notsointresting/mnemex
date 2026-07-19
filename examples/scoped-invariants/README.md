# Scoped Invariant: "All writes go through Repository"

This example shows an *explicit, path-scoped* invariant: a single decision,
anchored to the symbol that justifies it, that governs changes in a different
part of the tree. It stays deterministic and inspectable — there is no
repository-wide fuzzy policy and no LLM-authored rule.

## The decision

Store one decision through `remember_decision`, anchored to the `Repository`
class and tagged with both a forbidden phrase and an `applies-to` glob:

```text
content: All persistence writes go through Repository.
anchor:  src/db/repository.py::Repository
tags:    constraint:forbidden:direct sqlite write,applies-to:src/payments/**
```

- `constraint:forbidden:direct sqlite write` — the deterministic rule.
- `applies-to:src/payments/**` — the rule is evaluated only for changed paths
  under `src/payments/`. `**` spans directories; a single `*` stays within one
  path segment.

## Fresh governing anchor → BLOCKED

A staged change under the glob that reintroduces a direct write is blocked,
citing the governing decision even though it lives in another file:

```bash
mnemex check-diff --diff-file add-direct-write.diff --db project.sqlite3 --enforce-constraints
# src/payments/refund.py -> blocked, verdict: contradiction
# cited decision: the Repository invariant (source: scoped-invariant), exit 2
```

The same diff applied under a path *outside* `src/payments/**` is not blocked.

## Stale governing anchor → advisory only

Change the governing `Repository` symbol (so its content hash no longer
matches the anchor) and the invariant becomes **advisory**, never blocking,
until it is reconciled:

```bash
mnemex reconcile <decision-id> "Repository" "signature changed" --db project.sqlite3
```

This is the safety rule that keeps scoped invariants honest: a rule can only
block while the code it is anchored to is unchanged. Stale, superseded,
orphaned, or unanchored scoped decisions are reported as evidence but cannot
block a change.

## Limits

- Evaluation is **file-scoped**: the match is on the changed path, not on an
  exact hunk-to-symbol range.
- The rule is a deterministic phrase check against the change summary, not a
  semantic understanding of the edit.
- A scoped invariant needs a changed-path context to fire; the unscoped
  constraint behavior (no `applies-to` tag) is unchanged and path-independent.
