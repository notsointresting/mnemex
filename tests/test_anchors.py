from dataclasses import FrozenInstanceError
from datetime import datetime
from uuid import UUID

import pytest

from mnemex.anchors import (
    AmbiguousAnchorError,
    Anchor,
    AnchorNotFoundError,
    FreshnessReport,
    FreshnessStatus,
    check_freshness,
    forget,
    remember,
    resolve_anchor,
)
from mnemex.storage import Memory, Node, Storage


def make_node(
    node_id: str,
    *,
    file: str = "src/auth.py",
    name: str = "authenticate",
    line_start: int = 10,
    content_hash: str = "hash-1",
) -> Node:
    return Node(
        id=node_id,
        type="function",
        name=name,
        file=file,
        line_start=line_start,
        content_hash=content_hash,
        language="python",
    )


def test_anchor_is_immutable_and_accepts_exactly_two_modes() -> None:
    direct = Anchor(node_id="node-1")
    symbolic = Anchor(file="src/auth.py", symbol="authenticate")

    assert direct == Anchor(node_id="node-1")
    assert symbolic == Anchor(file="src/auth.py", symbol="authenticate")
    assert issubclass(AnchorNotFoundError, LookupError)
    assert issubclass(AmbiguousAnchorError, LookupError)
    with pytest.raises(FrozenInstanceError):
        direct.node_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"node_id": ""},
        {"node_id": "   "},
        {"file": "src/auth.py"},
        {"symbol": "authenticate"},
        {"file": "", "symbol": "authenticate"},
        {"file": "src/auth.py", "symbol": ""},
        {
            "node_id": "node-1",
            "file": "src/auth.py",
            "symbol": "authenticate",
        },
        {"node_id": "node-1", "file": "src/auth.py"},
    ],
)
def test_anchor_rejects_empty_mixed_and_incomplete_references(
    values: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        Anchor(**values)


def test_resolve_anchor_supports_direct_id_and_exact_file_symbol() -> None:
    target = make_node("target")
    same_symbol_other_file = make_node("other", file="src/admin.py")

    with Storage() as storage:
        storage.upsert_node(target)
        storage.upsert_node(same_symbol_other_file)

        assert resolve_anchor(storage, "target") == target
        assert resolve_anchor(storage, Anchor(node_id="target")) == target
        assert resolve_anchor(
            storage,
            Anchor(file="src/auth.py", symbol="authenticate"),
        ) == target


def test_remember_stamps_exact_node_hash_and_persists_returned_memory() -> None:
    node = make_node("auth-node", content_hash="structural-hash")

    with Storage() as storage:
        storage.upsert_node(node)
        memory = remember(
            storage,
            "Use signed session cookies.",
            anchor=Anchor(file=node.file, symbol=node.name),
            memory_id="decision-1",
            type="convention",
            rationale="Request handling stays stateless.",
            source="test-agent",
            confidence=0.9,
            importance=0.8,
            tags="auth,cookies",
        )

        assert memory.id == "decision-1"
        assert memory.type == "convention"
        assert memory.content == "Use signed session cookies."
        assert memory.rationale == "Request handling stays stateless."
        assert memory.anchor_node_id == node.id
        assert memory.anchor_hash == node.content_hash
        assert memory.scope == "project-shared"
        assert memory.source == "test-agent"
        assert memory.confidence == 0.9
        assert memory.importance == 0.8
        assert memory.tags == "auth,cookies"
        assert memory.created_at == memory.last_accessed == memory.last_verified
        parsed = datetime.fromisoformat(memory.created_at.replace("Z", "+00:00"))
        assert parsed.utcoffset() is not None
        assert parsed.utcoffset().total_seconds() == 0
        assert storage.get_memory(memory.id) == memory
        with pytest.raises(FrozenInstanceError):
            memory.content = "changed"  # type: ignore[misc]


def test_remember_without_anchor_is_unanchored_and_generates_uuid() -> None:
    with Storage() as storage:
        memory = remember(storage, "All services use UTC timestamps.")

        assert UUID(memory.id).version == 4
        assert memory.anchor_node_id is None
        assert memory.anchor_hash is None
        assert memory.scope == "project-shared"
        assert memory.type == "decision"
        assert memory.rationale == ""
        assert memory.source == "agent"
        assert memory.confidence == 1.0
        assert memory.importance == 1.0
        assert memory.tags == ""
        assert storage.get_memory(memory.id) == memory


def test_forget_returns_storage_deletion_result() -> None:
    with Storage() as storage:
        memory = remember(storage, "Temporary decision.", memory_id="temporary")

        assert forget(storage, memory.id) is True
        assert storage.get_memory(memory.id) is None
        assert forget(storage, memory.id) is False


def test_resolution_failures_do_not_persist_memory() -> None:
    with Storage() as storage:
        storage.upsert_node(make_node("first", line_start=5))
        storage.upsert_node(make_node("second", line_start=20))
        symbolic = Anchor(file="src/auth.py", symbol="authenticate")

        with pytest.raises(AmbiguousAnchorError, match="matches=2"):
            remember(storage, "Ambiguous decision.", anchor=symbolic)
        with pytest.raises(AnchorNotFoundError):
            remember(storage, "Missing decision.", anchor="missing-node")
        with pytest.raises(AnchorNotFoundError):
            remember(
                storage,
                "Missing symbolic decision.",
                anchor=Anchor(file="src/missing.py", symbol="authenticate"),
            )

        assert storage.list_memories(("project-shared",)) == []


def test_freshness_matrix_is_read_only_ordered_and_repeatable() -> None:
    fresh_node = make_node("fresh-node", content_hash="fresh-hash")
    stale_node = make_node("stale-node", content_hash="old-hash")
    orphan_node = make_node("orphan-node", content_hash="orphan-hash")
    missing_hash_node = make_node("missing-hash-node", content_hash="known-hash")

    with Storage() as storage:
        for node in (
            fresh_node,
            stale_node,
            orphan_node,
            missing_hash_node,
        ):
            storage.upsert_node(node)

        fresh = remember(
            storage,
            "Fresh decision.",
            anchor=fresh_node.id,
            memory_id="fresh",
        )
        stale = remember(
            storage,
            "Stale decision.",
            anchor=stale_node.id,
            memory_id="stale",
        )
        orphaned = remember(
            storage,
            "Orphaned decision.",
            anchor=orphan_node.id,
            memory_id="orphaned",
        )
        unanchored = remember(
            storage,
            "Unanchored decision.",
            memory_id="unanchored",
        )
        missing_hash = Memory(
            id="missing-hash",
            type="decision",
            content="A missing stored hash must be stale.",
            rationale="",
            anchor_node_id=missing_hash_node.id,
            anchor_hash=None,
            scope="project-shared",
            source="test-agent",
            confidence=1.0,
            importance=1.0,
            created_at="2026-01-01T00:00:00Z",
            last_accessed="2026-01-01T00:00:00Z",
            last_verified="2026-01-01T00:00:00Z",
            tags="",
        )
        storage.insert_memory(missing_hash)

        storage.upsert_node(
            make_node("stale-node", content_hash="new-hash")
        )
        storage.delete_node(orphan_node.id)

        memories_before = storage.list_memories(("project-shared",))
        first = check_freshness(storage)
        second = check_freshness(storage)

        assert first == second
        assert [report.memory_id for report in first] == [
            memory.id for memory in memories_before
        ]
        assert storage.list_memories(("project-shared",)) == memories_before

        reports = {report.memory_id: report for report in first}
        assert reports[fresh.id] == FreshnessReport(
            memory_id=fresh.id,
            status=FreshnessStatus.FRESH,
            anchor_node_id=fresh_node.id,
            stored_hash="fresh-hash",
            current_hash="fresh-hash",
        )
        assert reports[stale.id] == FreshnessReport(
            memory_id=stale.id,
            status=FreshnessStatus.STALE,
            anchor_node_id=stale_node.id,
            stored_hash="old-hash",
            current_hash="new-hash",
        )
        assert reports[orphaned.id] == FreshnessReport(
            memory_id=orphaned.id,
            status=FreshnessStatus.ORPHANED,
            anchor_node_id=orphan_node.id,
            stored_hash="orphan-hash",
            current_hash=None,
        )
        assert reports[unanchored.id] == FreshnessReport(
            memory_id=unanchored.id,
            status=FreshnessStatus.UNANCHORED,
            anchor_node_id=None,
            stored_hash=None,
            current_hash=None,
        )
        assert reports[missing_hash.id] == FreshnessReport(
            memory_id=missing_hash.id,
            status=FreshnessStatus.STALE,
            anchor_node_id=missing_hash_node.id,
            stored_hash=None,
            current_hash="known-hash",
        )
        assert storage.get_memory(orphaned.id) == orphaned
        assert check_freshness(storage, memory_id=stale.id) == [
            reports[stale.id]
        ]
        assert check_freshness(storage, memory_id="absent") == []


def test_project_freshness_excludes_agent_private_even_by_memory_id() -> None:
    with Storage() as storage:
        project = remember(
            storage,
            "Shared decision.",
            memory_id="project",
        )
        private = remember(
            storage,
            "Private scratch note.",
            memory_id="private",
            scope="agent-private",
        )

        assert [report.memory_id for report in check_freshness(storage)] == [
            project.id
        ]
        assert check_freshness(storage, memory_id=private.id) == []
        assert check_freshness(
            storage,
            scopes=("agent-private",),
            memory_id=private.id,
        ) == [
            FreshnessReport(
                memory_id=private.id,
                status=FreshnessStatus.UNANCHORED,
                anchor_node_id=None,
                stored_hash=None,
                current_hash=None,
            )
        ]
