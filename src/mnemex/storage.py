from __future__ import annotations

import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import sqlite_vec

_VALID_SCOPES = frozenset(
    {"agent-private", "project-shared", "user-global"}
)

_NODE_COLUMNS = "id, type, name, file, line_start, content_hash, language"
_MEMORY_COLUMNS = (
    "id, type, content, rationale, anchor_node_id, anchor_hash, scope, source, "
    "confidence, importance, created_at, last_accessed, last_verified, tags"
)

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS nodes(
        id TEXT PRIMARY KEY,
        type TEXT,
        name TEXT,
        file TEXT,
        line_start INTEGER,
        content_hash TEXT,
        language TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS edges(
        from_id TEXT,
        to_id TEXT,
        type TEXT,
        confidence REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memories(
        id TEXT PRIMARY KEY,
        type TEXT,
        content TEXT,
        rationale TEXT,
        anchor_node_id TEXT,
        anchor_hash TEXT,
        scope TEXT NOT NULL CHECK (
            scope IN ('agent-private', 'project-shared', 'user-global')
        ),
        source TEXT,
        confidence REAL,
        importance REAL,
        created_at TEXT,
        last_accessed TEXT,
        last_verified TEXT,
        tags TEXT
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec
    USING vec0(embedding FLOAT[384])
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
    USING fts5(
        content,
        rationale,
        tags,
        content='memories',
        content_rowid='rowid'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_fts_insert
    AFTER INSERT ON memories
    BEGIN
        INSERT INTO memories_fts(rowid, content, rationale, tags)
        VALUES (new.rowid, new.content, new.rationale, new.tags);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_fts_delete
    AFTER DELETE ON memories
    BEGIN
        INSERT INTO memories_fts(
            memories_fts, rowid, content, rationale, tags
        )
        VALUES (
            'delete', old.rowid, old.content, old.rationale, old.tags
        );
    END
    """,
)


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    type: str
    name: str
    file: str
    line_start: int
    content_hash: str
    language: str


@dataclass(frozen=True, slots=True)
class Memory:
    id: str
    type: str
    content: str
    rationale: str
    anchor_node_id: str | None
    anchor_hash: str | None
    scope: str
    source: str
    confidence: float
    importance: float
    created_at: str
    last_accessed: str
    last_verified: str
    tags: str


class Storage:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._connection: sqlite3.Connection | None = sqlite3.connect(str(path))
        try:
            self._load_sqlite_vec()
            self._initialize()
        except BaseException:
            self.close()
            raise

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Storage is closed")
        return self._connection

    def __enter__(self) -> Storage:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def upsert_node(self, node: Node) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO nodes(
                    id, type, name, file, line_start, content_hash, language
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    type = excluded.type,
                    name = excluded.name,
                    file = excluded.file,
                    line_start = excluded.line_start,
                    content_hash = excluded.content_hash,
                    language = excluded.language
                """,
                (
                    node.id,
                    node.type,
                    node.name,
                    node.file,
                    node.line_start,
                    node.content_hash,
                    node.language,
                ),
            )

    def get_node(self, node_id: str) -> Node | None:
        row = self.connection.execute(
            f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        return None if row is None else Node(*row)

    def find_nodes(self, file: str, name: str) -> list[Node]:
        rows = self.connection.execute(
            f"""
            SELECT {_NODE_COLUMNS}
            FROM nodes
            WHERE file = ? AND name = ?
            ORDER BY line_start, id
            """,
            (file, name),
        ).fetchall()
        return [Node(*row) for row in rows]

    def delete_node(self, node_id: str) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM nodes WHERE id = ?", (node_id,))

    def insert_memory(self, memory: Memory) -> None:
        self._validate_scope(memory.scope)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO memories(
                    id, type, content, rationale, anchor_node_id, anchor_hash,
                    scope, source, confidence, importance, created_at,
                    last_accessed, last_verified, tags
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory.id,
                    memory.type,
                    memory.content,
                    memory.rationale,
                    memory.anchor_node_id,
                    memory.anchor_hash,
                    memory.scope,
                    memory.source,
                    memory.confidence,
                    memory.importance,
                    memory.created_at,
                    memory.last_accessed,
                    memory.last_verified,
                    memory.tags,
                ),
            )

    def get_memory(self, memory_id: str) -> Memory | None:
        row = self.connection.execute(
            f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        return None if row is None else Memory(*row)

    def list_memories(self, scopes: Collection[str]) -> list[Memory]:
        if isinstance(scopes, (str, bytes)):
            raise ValueError("scopes must be a non-empty collection")

        values = tuple(scopes)
        if not values:
            raise ValueError("scopes must be a non-empty collection")
        for scope in values:
            self._validate_scope(scope)

        ordered_scopes = tuple(sorted(set(values)))
        placeholders = ", ".join("?" for _ in ordered_scopes)
        rows = self.connection.execute(
            f"""
            SELECT {_MEMORY_COLUMNS}
            FROM memories
            WHERE scope IN ({placeholders})
            ORDER BY created_at, id
            """,
            ordered_scopes,
        ).fetchall()
        return [Memory(*row) for row in rows]

    def delete_memory(self, memory_id: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM memories WHERE id = ?", (memory_id,)
            )
        return cursor.rowcount > 0

    def _load_sqlite_vec(self) -> None:
        connection = self.connection
        toggle_extension_loading = getattr(
            connection, "enable_load_extension", None
        )
        if toggle_extension_loading is None:
            raise RuntimeError("SQLite extension loading is unavailable")

        toggle_extension_loading(True)
        try:
            sqlite_vec.load(connection)
        finally:
            toggle_extension_loading(False)

    def _initialize(self) -> None:
        row = self.connection.execute("PRAGMA user_version").fetchone()
        version = 0 if row is None else row[0]
        if version not in (0, 1):
            raise RuntimeError(f"Unsupported schema version: {version}")

        with self.connection:
            for statement in _SCHEMA:
                self.connection.execute(statement)
            self.connection.execute("PRAGMA user_version = 1")

    @staticmethod
    def _validate_scope(scope: str) -> None:
        if scope not in _VALID_SCOPES:
            raise ValueError(f"Invalid memory scope: {scope!r}")
