from __future__ import annotations

from datetime import datetime, timezone

from mnemex.anchors import remember
from mnemex.reviews import list_review_candidates, reinforce_decision
from mnemex.storage import Node, Storage


def test_review_candidates_prioritize_stale_over_due_and_never_recalled() -> None:
    with Storage() as storage:
        storage.upsert_node(
            Node("auth", "function", "authenticate", "src/auth.py", 1, "one", "python")
        )
        storage.upsert_node(
            Node("keys", "function", "rotate_keys", "src/keys.py", 1, "one", "python")
        )
        storage.upsert_node(
            Node("clock", "function", "now", "src/clock.py", 1, "one", "python")
        )
        stale = remember(storage, "Keep auth stateless", anchor="auth")
        due = remember(storage, "Rotate signing keys", anchor="keys")
        never = remember(storage, "Use UTC timestamps", anchor="clock")
        storage.ensure_decision_metadata(
            due.id, review_after="2020-01-01T00:00:00Z"
        )
        storage.upsert_node(
            Node("auth", "function", "authenticate", "src/auth.py", 1, "two", "python")
        )

        candidates = list_review_candidates(
            storage, now=datetime(2026, 1, 1, tzinfo=timezone.utc)
        )

        assert [candidate.memory_id for candidate in candidates] == [
            stale.id,
            due.id,
            never.id,
        ]
        assert candidates[0].freshness == "stale"


def test_reinforcement_preserves_decision_and_updates_recall_statistics() -> None:
    with Storage() as storage:
        decision = remember(storage, "Keep auth stateless")
        original = storage.get_memory(decision.id)

        metadata = reinforce_decision(storage, decision.id)

        assert metadata.access_count == 1
        assert metadata.last_confirmed_at is not None
        assert storage.get_memory(decision.id) == original
