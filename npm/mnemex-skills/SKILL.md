# mnemex

Use mnemex before implementing a significant code change. Mnemex is a
local-first decision-integrity layer: it anchors a decision to code and reports
whether that decision is still fresh.

1. Run `check_proposed_change` with the path and a concise patch summary.
2. Treat a block as requiring `override_decision_guard` with a reason.
3. Run `why` or `review_conflicts` when the returned decision evidence is unclear.
4. Reconcile a stale anchor before replacing its decision.
5. Treat local-only or unavailable semantic verdicts as advisory evidence, not
   permission to ignore an active decision.

Mnemex remains local by default. The semantic provider is opt-in.
