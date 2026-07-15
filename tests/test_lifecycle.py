from __future__ import annotations

import pytest

from mnemex.anchors import remember
from mnemex.lifecycle import (
    create_successor,
    reconcile_stale_decision,
    refresh_decision,
    retire_decision,
)
from mnemex.storage import Node, Storage


def _node(node_id: str = "auth-node", content_hash: str = "hash-1") -> Node:
    return Node(
        id=node_id,
        type="function",
        name="authenticate",
        file="src/auth.py",
        line_start=10,
        content_hash=content_hash,
        language="python",
    )


def _decision(storage: Storage, memory_id: str = "decision-1") -> str:
    storage.upsert_node(_node())
    return remember(
        storage,
        "Use signed session cookies.",
        anchor="auth-node",
        rationale="Requests remain stateless.",
        tags="auth,cookies",
        memory_id=memory_id,
    ).id


def test_reconcile_is_deterministic_for_fresh_stale_and_orphaned() -> None:
    with Storage() as storage:
        decision_id = _decision(storage)

        assert reconcile_stale_decision(
            storage, decision_id, "authenticate", "- old\n+ new"
        ) == "still_valid"

        storage.upsert_node(_node(content_hash="hash-2"))
        assert reconcile_stale_decision(
            storage, decision_id, "authenticate", "- old\n+ new"
        ) == "possible_regression"
        assert reconcile_stale_decision(
            storage, decision_id, "other_symbol", "- old\n+ new"
        ) == "human_review"

        storage.delete_node("auth-node")
        assert reconcile_stale_decision(
            storage, decision_id, "authenticate", "- old\n+ new"
        ) == "human_review"


def test_successor_preserves_original_and_records_auditable_link() -> None:
    with Storage() as storage:
        decision_id = _decision(storage)
        original = storage.get_memory(decision_id)

        successor = create_successor(
            storage,
            decision_id,
            "Use signed session cookies with rotation. <private>token=secret</private>",
            successor_id="decision-2",
        )

        assert storage.get_memory(decision_id) == original
        assert storage.get_decision_metadata(decision_id).status == "superseded"
        successor_metadata = storage.get_decision_metadata(successor.id)
        assert successor_metadata.status == "active"
        assert successor_metadata.supersedes_memory_id == decision_id
        assert "token=secret" not in successor.content
        assert reconcile_stale_decision(
            storage, decision_id, "authenticate", "- old\n+ new"
        ) == "superseded"


def test_retire_preserves_evidence_and_disallows_more_transitions() -> None:
    with Storage() as storage:
        decision_id = _decision(storage)
        original = storage.get_memory(decision_id)

        metadata = retire_decision(storage, decision_id)

        assert metadata.status == "retired"
        assert metadata.last_confirmed_at is not None
        assert storage.get_memory(decision_id) == original
        assert reconcile_stale_decision(
            storage, decision_id, "authenticate", "- old\n+ new"
        ) == "human_review"
        with pytest.raises(ValueError, match="Only active"):
            retire_decision(storage, decision_id)


def test_refresh_stale_decision_creates_fresh_successor_without_rewriting_prior() -> None:
    with Storage() as storage:
        decision_id = _decision(storage)
        original = storage.get_memory(decision_id)
        storage.upsert_node(_node(content_hash="hash-2"))

        refreshed = refresh_decision(storage, decision_id, successor_id="decision-2")

        assert storage.get_memory(decision_id) == original
        assert refreshed.content == original.content
        assert refreshed.rationale == original.rationale
        assert refreshed.anchor_node_id == original.anchor_node_id
        assert refreshed.anchor_hash == "hash-2"
        assert storage.get_decision_metadata(decision_id).status == "superseded"
        refreshed_metadata = storage.get_decision_metadata(refreshed.id)
        assert refreshed_metadata.supersedes_memory_id == decision_id
        assert reconcile_stale_decision(
            storage, refreshed.id, "authenticate", "- old\n+ new"
        ) == "still_valid"


def test_refresh_orphan_requires_explicit_replacement_anchor() -> None:
    with Storage() as storage:
        decision_id = _decision(storage)
        storage.delete_node("auth-node")
        storage.upsert_node(_node("replacement-node", "replacement-hash"))

        with pytest.raises(ValueError, match="needs an anchor"):
            refresh_decision(storage, decision_id)

        refreshed = refresh_decision(
            storage, decision_id, anchor="replacement-node"
        )

        assert refreshed.anchor_node_id == "replacement-node"
        assert refreshed.anchor_hash == "replacement-hash"
