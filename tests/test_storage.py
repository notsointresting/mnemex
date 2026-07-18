import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import mnemex.storage as storage_module

try:
    import sqlite_vec
except (ImportError, OSError):
    sqlite_vec = None

from mnemex.storage import Memory, Node, Storage

with Storage() as _probe:
    VEC_AVAILABLE = _probe.vec_available
_needs_vec = pytest.mark.skipif(
    not VEC_AVAILABLE, reason="sqlite-vec extension unavailable (no-ML mode)"
)


def make_node(
    node_id: str = "node-1",
    *,
    name: str = "authenticate",
    line_start: int = 10,
    content_hash: str = "hash-1",
) -> Node:
    return Node(
        id=node_id,
        type="function",
        name=name,
        file="src/auth.py",
        line_start=line_start,
        content_hash=content_hash,
        language="python",
    )


def make_memory(
    memory_id: str = "memory-1",
    *,
    scope: str = "project-shared",
    anchor_node_id: str | None = "node-1",
    anchor_hash: str | None = "hash-1",
    created_at: str = "2026-07-14T08:00:00Z",
) -> Memory:
    return Memory(
        id=memory_id,
        type="decision",
        content="Use signed session cookies for authentication.",
        rationale="They keep request handling stateless.",
        anchor_node_id=anchor_node_id,
        anchor_hash=anchor_hash,
        scope=scope,
        source="test-agent",
        confidence=0.9,
        importance=0.8,
        created_at=created_at,
        last_accessed="2026-07-14T08:00:00Z",
        last_verified="2026-07-14T08:00:00Z",
        tags="auth,cookies",
    )


def fts_memory_ids(storage: Storage, query: str) -> list[str]:
    rows = storage.connection.execute(
        """
        SELECT memories.id
        FROM memories_fts
        JOIN memories ON memories.rowid = memories_fts.rowid
        WHERE memories_fts MATCH ?
        ORDER BY memories.id
        """,
        (query,),
    ).fetchall()
    return [row[0] for row in rows]


def test_schema_is_complete_and_reopen_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "mnemex.sqlite3"
    expected_columns = {
        "nodes": [
            "id",
            "type",
            "name",
            "file",
            "line_start",
            "content_hash",
            "language",
        ],
        "edges": ["from_id", "to_id", "type", "confidence"],
        "memories": [
            "id",
            "type",
            "content",
            "rationale",
            "anchor_node_id",
            "anchor_hash",
            "scope",
            "source",
            "confidence",
            "importance",
            "created_at",
            "last_accessed",
            "last_verified",
            "tags",
        ],
        "memories_vec": ["rowid", "embedding"],
        "memories_fts": ["content", "rationale", "tags"],
    }
    if not VEC_AVAILABLE:
        # No-ML mode: the optional vector table is not created.
        del expected_columns["memories_vec"]

    with Storage(database) as storage:
        assert storage.connection.execute(
            "PRAGMA user_version"
        ).fetchone() == (3,)
        for table, columns in expected_columns.items():
            actual = storage.connection.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
            assert [row[1] for row in actual] == columns
        assert storage.connection.execute(
            "PRAGMA foreign_key_list(memories)"
        ).fetchall() == []
        storage.insert_memory(make_memory(anchor_node_id=None, anchor_hash=None))

    with Storage(database) as reopened:
        assert reopened.connection.execute(
            "PRAGMA user_version"
        ).fetchone() == (3,)
        assert reopened.get_memory("memory-1") == make_memory(
            anchor_node_id=None, anchor_hash=None
        )
        assert fts_memory_ids(reopened, "stateless") == ["memory-1"]

    storage.close()


def test_node_upsert_find_and_delete_are_deterministic() -> None:
    with Storage() as storage:
        later = make_node("node-b", line_start=20)
        earlier = make_node("node-a", line_start=5)
        storage.upsert_node(later)
        storage.upsert_node(earlier)
        storage.upsert_node(make_node("other", name="authorize"))

        assert storage.find_nodes("src/auth.py", "authenticate") == [
            earlier,
            later,
        ]

        updated = make_node("node-a", line_start=7, content_hash="hash-2")
        storage.upsert_node(updated)
        assert storage.get_node("node-a") == updated

        storage.delete_node("node-a")
        assert storage.get_node("node-a") is None


def test_records_are_immutable() -> None:
    node = make_node()
    memory = make_memory()

    with pytest.raises(FrozenInstanceError):
        node.name = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        memory.scope = "agent-private"  # type: ignore[misc]


def test_memory_crud_keeps_fts_synchronized() -> None:
    with Storage() as storage:
        memory = make_memory()
        storage.insert_memory(memory)

        assert storage.get_memory(memory.id) == memory
        assert fts_memory_ids(storage, "signed") == [memory.id]
        assert fts_memory_ids(storage, "stateless") == [memory.id]
        assert fts_memory_ids(storage, "cookies") == [memory.id]

        assert storage.delete_memory(memory.id) is True
        assert storage.get_memory(memory.id) is None
        assert fts_memory_ids(storage, "signed") == []
        assert storage.delete_memory(memory.id) is False


@_needs_vec
def test_memories_vec_exists_and_accepts_384_dimension_vectors() -> None:
    with Storage() as storage:
        embedding = sqlite_vec.serialize_float32([0.0] * 384)
        with storage.connection:
            storage.connection.execute(
                "INSERT INTO memories_vec(rowid, embedding) VALUES (?, ?)",
                (1, embedding),
            )

        result = storage.connection.execute(
            """
            SELECT rowid, distance
            FROM memories_vec
            WHERE embedding MATCH ? AND k = 1
            ORDER BY distance
            """,
            (embedding,),
        ).fetchone()
        assert result == (1, 0.0)


def test_list_memories_filters_scopes_without_private_leakage() -> None:
    with Storage() as storage:
        private = make_memory("private", scope="agent-private")
        project_b = make_memory("project-b")
        project_a = make_memory("project-a")
        global_memory = make_memory("global", scope="user-global")
        for memory in (private, project_b, project_a, global_memory):
            storage.insert_memory(memory)

        project_results = storage.list_memories({"project-shared"})
        assert [memory.id for memory in project_results] == [
            "project-a",
            "project-b",
        ]
        assert private not in project_results
        assert storage.list_memories(
            ["project-shared", "user-global"]
        ) == [global_memory, project_a, project_b]


def test_empty_and_invalid_scopes_are_rejected_before_sql() -> None:
    with Storage() as storage:
        statements: list[str] = []
        storage.connection.set_trace_callback(statements.append)

        with pytest.raises(ValueError, match="non-empty collection"):
            storage.list_memories([])
        with pytest.raises(ValueError, match="non-empty collection"):
            storage.list_memories("project-shared")
        with pytest.raises(ValueError, match="Invalid memory scope"):
            storage.list_memories(["invalid"])
        with pytest.raises(ValueError, match="Invalid memory scope"):
            storage.insert_memory(make_memory(scope="invalid"))

        assert not any("FROM memories" in sql for sql in statements)
        assert not any("INSERT INTO memories" in sql for sql in statements)

        with pytest.raises(sqlite3.IntegrityError):
            storage.connection.execute(
                """
                INSERT INTO memories(
                    id, type, content, rationale, anchor_node_id, anchor_hash,
                    scope, source, confidence, importance, created_at,
                    last_accessed, last_verified, tags
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "invalid-direct",
                    "decision",
                    "content",
                    "rationale",
                    None,
                    None,
                    "invalid",
                    "test-agent",
                    1.0,
                    1.0,
                    "2026-07-14T08:00:00Z",
                    "2026-07-14T08:00:00Z",
                    "2026-07-14T08:00:00Z",
                    "test",
                ),
            )


def test_deleting_node_preserves_memory_anchor_for_orphan_detection() -> None:
    with Storage() as storage:
        node = make_node()
        memory = make_memory(
            anchor_node_id=node.id,
            anchor_hash=node.content_hash,
        )
        storage.upsert_node(node)
        storage.insert_memory(memory)

        storage.delete_node(node.id)

        assert storage.get_node(node.id) is None
        assert storage.get_memory(memory.id) == memory
        assert storage.get_memory(memory.id).anchor_node_id == node.id
        assert storage.get_memory(memory.id).anchor_hash == node.content_hash



# --------------------------------------------------------------------------- #
# Optional sqlite-vec accelerator: stable status + graceful degradation
# --------------------------------------------------------------------------- #


def test_vec_status_disabled_by_environment(monkeypatch) -> None:
    monkeypatch.setenv("MNEMEX_NO_VEC", "1")
    with Storage() as storage:
        assert storage.vec_available is False
        assert storage.vec_status == "disabled-by-environment"


def test_vec_status_package_not_installed_still_runs_bm25(monkeypatch) -> None:
    # Simulate an absent package without uninstalling it: the lazy backend
    # loader is what _load_sqlite_vec consults.
    monkeypatch.setattr(
        storage_module.vector_backend,
        "load_module",
        lambda: (None, "package-not-installed"),
    )
    with Storage() as storage:
        assert storage.vec_available is False
        assert storage.vec_status == "package-not-installed"
        # Schema + FTS5 initialize normally and the brain is fully usable.
        assert "memories_vec" not in _table_names(storage)
        storage.insert_memory(make_memory(anchor_node_id=None, anchor_hash=None))
        assert fts_memory_ids(storage, "stateless") == ["memory-1"]


def test_vec_status_import_time_oserror_reports_load_failed(monkeypatch) -> None:
    # An installed package whose native payload raised OSError at import time
    # is recorded as extension-load-failed, distinct from not-installed.
    monkeypatch.setattr(
        storage_module.vector_backend,
        "load_module",
        lambda: (None, "extension-load-failed"),
    )
    with Storage() as storage:
        assert storage.vec_available is False
        assert storage.vec_status == "extension-load-failed"


def test_vec_status_extension_loading_unsupported() -> None:
    # Some Python builds ship sqlite3 without loadable-extension support.
    storage = Storage()
    real = storage._connection
    try:
        storage._connection = object()  # no enable_load_extension attribute
        available, status = storage._load_sqlite_vec()
        assert available is False
        assert status == "extension-loading-unsupported"
    finally:
        storage._connection = real
        storage.close()


def test_vec_status_extension_load_failed_on_load_call(monkeypatch) -> None:
    class _FailingVec:
        @staticmethod
        def load(connection: object) -> None:
            raise OSError("simulated native load failure")

    storage = Storage()
    try:
        if getattr(storage._connection, "enable_load_extension", None) is None:
            pytest.skip("platform sqlite3 cannot load extensions")
        monkeypatch.setattr(
            storage_module.vector_backend,
            "load_module",
            lambda: (_FailingVec, "available"),
        )
        available, status = storage._load_sqlite_vec()
        assert available is False
        assert status == "extension-load-failed"
    finally:
        storage.close()


def test_vec_status_never_leaks_exception_or_path(monkeypatch) -> None:
    class _FailingVec:
        @staticmethod
        def load(connection: object) -> None:
            raise OSError(r"C:\secret\path\vec0.dll not permitted")

    storage = Storage()
    try:
        if getattr(storage._connection, "enable_load_extension", None) is None:
            pytest.skip("platform sqlite3 cannot load extensions")
        monkeypatch.setattr(
            storage_module.vector_backend,
            "load_module",
            lambda: (_FailingVec, "available"),
        )
        _available, status = storage._load_sqlite_vec()
        assert status in {
            "available",
            "disabled-by-environment",
            "package-not-installed",
            "extension-loading-unsupported",
            "extension-load-failed",
        }
        assert "secret" not in status and ".dll" not in status
    finally:
        storage.close()


@_needs_vec
def test_vec_status_available_when_extension_loads() -> None:
    with Storage() as storage:
        assert storage.vec_available is True
        assert storage.vec_status == "available"


def test_core_mode_creates_recalls_and_reopens_one_brain(
    tmp_path: Path, monkeypatch
) -> None:
    # A single SQLite brain must create, recall, and reopen with no vector
    # backend present.
    from mnemex.retrieval import recall

    monkeypatch.setenv("MNEMEX_NO_VEC", "1")
    database = tmp_path / "core.sqlite3"
    with Storage(database) as storage:
        assert storage.vec_available is False
        storage.insert_memory(make_memory(anchor_node_id=None, anchor_hash=None))
        result = recall(storage, "stateless", scopes=("project-shared",))
        assert result.mode == "bm25-only"
        assert [sm.memory.id for sm in result.included] == ["memory-1"]

    with Storage(database) as reopened:
        assert reopened.vec_available is False
        assert reopened.get_memory("memory-1") is not None


@_needs_vec
def test_existing_memories_vec_reopens_without_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "hybrid.sqlite3"
    with Storage(database) as storage:
        assert storage.vec_available is True
        storage.insert_memory(make_memory(anchor_node_id=None, anchor_hash=None))
        rowid = storage.connection.execute(
            "SELECT rowid FROM memories WHERE id = ?", ("memory-1",)
        ).fetchone()[0]
        with storage.connection:
            storage.connection.execute(
                "INSERT INTO memories_vec(rowid, embedding) VALUES (?, ?)",
                (rowid, sqlite_vec.serialize_float32([0.0] * 384)),
            )
        before = storage.connection.execute(
            "SELECT count(*) FROM memories_vec"
        ).fetchone()[0]
    assert before == 1

    # Reopen with vector loading disabled: opening must not crash and must not
    # drop the vec table. (A vec0 table cannot be *queried* without the loaded
    # extension, so we only assert the definition survives here.)
    monkeypatch.setenv("MNEMEX_NO_VEC", "1")
    with Storage(database) as reopened:
        assert reopened.vec_available is False
        assert reopened.vec_status == "disabled-by-environment"
        assert "memories_vec" in _table_names(reopened)

    # Re-enable vectors: the previously stored embedding is intact, proving the
    # no-vec open neither dropped nor mutated the vector data.
    monkeypatch.delenv("MNEMEX_NO_VEC", raising=False)
    with Storage(database) as revived:
        assert revived.vec_available is True
        after = revived.connection.execute(
            "SELECT count(*) FROM memories_vec"
        ).fetchone()[0]
        assert after == before


def _table_names(storage: Storage) -> set[str]:
    return {
        row[0]
        for row in storage.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
