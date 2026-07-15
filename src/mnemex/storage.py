from __future__ import annotations

import os
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass, replace
from pathlib import Path
from types import TracebackType

import sqlite_vec

from mnemex.security import RedactionLog, sanitize

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
    CREATE TABLE IF NOT EXISTS redaction_audit(
        id INTEGER PRIMARY KEY,
        memory_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        field TEXT NOT NULL,
        pattern_name TEXT NOT NULL,
        original_snippet TEXT NOT NULL,
        replacement TEXT NOT NULL
    )
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

# Created only when the sqlite-vec extension is available. It is an optional
# semantic upgrade: without it, mnemex runs in BM25/FTS5-only ("no-ML") mode.
_VEC_SCHEMA = """
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec
    USING vec0(embedding FLOAT[384])
    """


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
        self._vec_available = False
        try:
            self._vec_available = self._load_sqlite_vec()
            self._initialize()
        except BaseException:
            self.close()
            raise

    @property
    def vec_available(self) -> bool:
        """True when the sqlite-vec vector backend loaded.

        When False, ``memories_vec`` does not exist and mnemex operates in
        BM25/FTS5-only ("no-ML") mode. Retrieval degrades gracefully.
        """
        return self._vec_available

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

    def insert_memory(
        self,
        memory: Memory,
        *,
        redactions: RedactionLog | None = None,
    ) -> None:
        self._validate_scope(memory.scope)
        audit_log = RedactionLog() if redactions is None else redactions
        memory = replace(
            memory,
            content=sanitize(
                memory.content, field_name="content", log=audit_log
            ),
            rationale=sanitize(
                memory.rationale, field_name="rationale", log=audit_log
            ),
            tags=sanitize(memory.tags, field_name="tags", log=audit_log)
            if memory.tags
            else "",
        )
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
            self.connection.executemany(
                """
                INSERT INTO redaction_audit(
                    memory_id, timestamp, field, pattern_name,
                    original_snippet, replacement
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        memory.id,
                        entry.timestamp,
                        entry.field,
                        entry.pattern_name,
                        entry.original_snippet,
                        entry.replacement,
                    )
                    for entry in audit_log.entries
                ],
            )

    def get_memory(self, memory_id: str) -> Memory | None:
        row = self.connection.execute(
            f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        return None if row is None else Memory(*row)

    def list_redactions(self, memory_id: str) -> list[tuple[str, str, str, str]]:
        """Return masked audit entries for a persisted memory."""
        rows = self.connection.execute(
            """
            SELECT field, pattern_name, original_snippet, replacement
            FROM redaction_audit
            WHERE memory_id = ?
            ORDER BY id
            """,
            (memory_id,),
        ).fetchall()
        return [tuple(row) for row in rows]

    def list_memories(self, scopes: Collection[str]) -> list[Memory]:
        ordered_scopes = self._normalize_scopes(scopes)
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

    def list_memories_by_anchor_file(
        self, path: str, scopes: Collection[str]
    ) -> list[Memory]:
        """Return in-scope memories anchored to nodes in ``path``."""
        ordered_scopes = self._normalize_scopes(scopes)
        normalized_path = path.replace("\\", "/")
        placeholders = ", ".join("?" for _ in ordered_scopes)
        memory_columns = ", ".join(
            f"m.{column.strip()}" for column in _MEMORY_COLUMNS.split(",")
        )
        rows = self.connection.execute(
            f"""
            SELECT {memory_columns}
            FROM memories AS m
            JOIN nodes AS n ON n.id = m.anchor_node_id
            WHERE n.file = ? AND m.scope IN ({placeholders})
            ORDER BY m.created_at, m.id
            """,
            (normalized_path, *ordered_scopes),
        ).fetchall()
        return [Memory(*row) for row in rows]

    def delete_memory(self, memory_id: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM memories WHERE id = ?", (memory_id,)
            )
        return cursor.rowcount > 0

    def _load_sqlite_vec(self) -> bool:
        """Load the sqlite-vec extension if the platform supports it.

        Returns ``True`` when the vector backend is available. When the stdlib
        ``sqlite3`` was built without loadable-extension support (some macOS
        Python builds) or the extension otherwise fails to load, returns
        ``False`` and mnemex runs in BM25/FTS5-only ("no-ML") mode. FTS5 is
        compiled into SQLite and needs no extension, so keyword retrieval
        always works. Set ``MNEMEX_NO_VEC=1`` to force no-ML mode.
        """
        if os.environ.get("MNEMEX_NO_VEC"):
            return False

        connection = self.connection
        toggle_extension_loading = getattr(
            connection, "enable_load_extension", None
        )
        if toggle_extension_loading is None:
            return False

        try:
            toggle_extension_loading(True)
        except (sqlite3.OperationalError, sqlite3.NotSupportedError, AttributeError):
            return False
        try:
            sqlite_vec.load(connection)
        except Exception:
            return False
        finally:
            try:
                toggle_extension_loading(False)
            except Exception:
                pass
        return True

    def _initialize(self) -> None:
        row = self.connection.execute("PRAGMA user_version").fetchone()
        version = 0 if row is None else row[0]
        if version not in (0, 1):
            raise RuntimeError(f"Unsupported schema version: {version}")

        with self.connection:
            for statement in _SCHEMA:
                self.connection.execute(statement)
            if self._vec_available:
                self.connection.execute(_VEC_SCHEMA)
            self.connection.execute("PRAGMA user_version = 1")

    @staticmethod
    def _validate_scope(scope: str) -> None:
        if scope not in _VALID_SCOPES:
            raise ValueError(f"Invalid memory scope: {scope!r}")

    @classmethod
    def _normalize_scopes(cls, scopes: Collection[str]) -> tuple[str, ...]:
        if isinstance(scopes, (str, bytes)):
            raise ValueError("scopes must be a non-empty collection")
        values = tuple(scopes)
        if not values:
            raise ValueError("scopes must be a non-empty collection")
        for scope in values:
            cls._validate_scope(scope)
        return tuple(sorted(set(values)))
