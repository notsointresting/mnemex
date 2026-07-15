from __future__ import annotations

import pytest

from mnemex.anchors import remember
from mnemex.conflicts import list_conflicts, review_conflict
from mnemex.storage import Node, Storage


def _decision(
    storage: Storage,
    memory_id: str,
    content: str,
    *,
    anchor: str | None = None,
    scope: str = "project-shared",
    tags: str = "",
) -> None:
    remember(
        storage,
        content,
        memory_id=memory_id,
        anchor=anchor,
        scope=scope,
        tags=tags,
    )


def test_conflict_inbox_does_not_leak_private_decisions() -> None:
    with Storage() as storage:
        _decision(storage, "shared", "Use signed webhook payloads", tags="webhooks")
        _decision(
            storage,
            "private",
            "Avoid signed webhook payloads",
            scope="agent-private",
            tags="webhooks",
        )

        shared = list_conflicts(storage)

        assert shared.conflicts == ()
        assert shared.scanned_decision_ids == ("shared",)
        with pytest.raises(LookupError):
            review_conflict(storage, "shared", "private")


def test_conflict_inbox_excludes_superseded_decisions() -> None:
    with Storage() as storage:
        _decision(storage, "old", "Use signed webhook payloads", tags="webhooks")
        _decision(storage, "new", "Avoid signed webhook payloads", tags="webhooks")
        storage.set_decision_status("old", "superseded", supersedes_memory_id="new")

        result = list_conflicts(storage)

        assert result.conflicts == ()
        assert result.scanned_decision_ids == ("new",)


def test_conflict_inbox_detects_anchored_contradictions() -> None:
    with Storage() as storage:
        storage.upsert_node(
            Node("webhook", "function", "deliver", "src/webhook.py", 1, "one", "python")
        )
        _decision(storage, "use", "Use JSON payloads for webhook retries", anchor="webhook")
        _decision(storage, "avoid", "Do not use JSON payloads for webhook retries", anchor="webhook")

        result = list_conflicts(storage)
        review = review_conflict(storage, "avoid", "use")

        assert len(result.conflicts) == 1
        conflict = result.conflicts[0]
        assert conflict.memory_ids == ("avoid", "use")
        assert conflict.anchor_node_id == "webhook"
        assert conflict.anchor_file == "src/webhook.py"
        assert {"json", "payload", "webhook", "retry"}.issubset(conflict.shared_terms)
        assert review.left.id == "avoid"
        assert review.right.id == "use"


def test_conflict_inbox_order_is_deterministic() -> None:
    with Storage() as storage:
        _decision(storage, "zulu", "Enable retry logging", tags="retry")
        _decision(storage, "alpha", "Disable retry logging", tags="retry")
        _decision(storage, "echo", "Use checksum validation", tags="checksum")
        _decision(storage, "bravo", "Avoid checksum validation", tags="checksum")

        first = list_conflicts(storage)
        second = list_conflicts(storage)

        assert [item.memory_ids for item in first.conflicts] == [
            ("alpha", "zulu"),
            ("bravo", "echo"),
        ]
        assert first == second
