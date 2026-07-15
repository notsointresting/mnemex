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

_SCHEMA_VERSION = 3
_DECISION_STATUSES = frozenset({"active", "superseded", "retired"})
_GUARD_VERDICTS = frozenset(
    {"compatible", "contradiction", "supersedes", "uncertain", "unavailable"}
)
_FRESHNESS_STATES = frozenset({"fresh", "stale", "orphaned", "unanchored"})

_V1_SCHEMA = (
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

# Version 2 is deliberately additive. Existing v1 memories remain the source
# of truth, while the new tables hold optional decision-guard and lifecycle
# state keyed by the pre-existing memory identifiers.
_V2_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS decision_metadata(
        memory_id TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'active' CHECK (
            status IN ('active', 'superseded', 'retired')
        ),
        supersedes_memory_id TEXT,
        agent TEXT,
        client TEXT,
        session TEXT,
        branch TEXT,
        commit_hash TEXT,
        source_request TEXT,
        review_after TEXT,
        access_count INTEGER NOT NULL DEFAULT 0 CHECK (access_count >= 0),
        last_recalled_at TEXT,
        last_confirmed_at TEXT,
        FOREIGN KEY(memory_id) REFERENCES memories(id),
        FOREIGN KEY(supersedes_memory_id) REFERENCES memories(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS guard_runs(
        id TEXT PRIMARY KEY,
        path TEXT NOT NULL,
        patch_summary TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        payload_tokens INTEGER NOT NULL CHECK (payload_tokens >= 0),
        verdict TEXT NOT NULL CHECK (
            verdict IN ('compatible', 'contradiction', 'supersedes',
                        'uncertain', 'unavailable')
        ),
        confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
        explanation TEXT NOT NULL,
        recommended_action TEXT NOT NULL,
        blocked INTEGER NOT NULL CHECK (blocked IN (0, 1)),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS guard_evidence(
        guard_run_id TEXT NOT NULL,
        memory_id TEXT NOT NULL,
        rank INTEGER NOT NULL CHECK (rank >= 0),
        freshness TEXT NOT NULL CHECK (
            freshness IN ('fresh', 'stale', 'orphaned', 'unanchored')
        ),
        PRIMARY KEY(guard_run_id, memory_id),
        UNIQUE(guard_run_id, rank),
        FOREIGN KEY(guard_run_id) REFERENCES guard_runs(id),
        FOREIGN KEY(memory_id) REFERENCES memories(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS guard_overrides(
        id INTEGER PRIMARY KEY,
        guard_run_id TEXT NOT NULL,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        FOREIGN KEY(guard_run_id) REFERENCES guard_runs(id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS decision_metadata_status_review_idx
    ON decision_metadata(status, review_after)
    """,
    """
    CREATE INDEX IF NOT EXISTS guard_evidence_memory_idx
    ON guard_evidence(memory_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS guard_overrides_run_idx
    ON guard_overrides(guard_run_id, id)
    """,
)

# Version 3 records the masked redaction audit for every non-memory write.
# Memory audits stay in their established table for backward compatibility.
_V3_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS persistence_redaction_audit(
        id INTEGER PRIMARY KEY,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        field TEXT NOT NULL,
        pattern_name TEXT NOT NULL,
        original_snippet TEXT NOT NULL,
        replacement TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS persistence_redaction_audit_entity_idx
    ON persistence_redaction_audit(entity_type, entity_id, id)
    """,
)

# Kept as a compatibility alias for code and tests that inspect the original
# bootstrap schema. New databases receive v1 followed by the v1-to-v2 upgrade.
_SCHEMA = _V1_SCHEMA

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


@dataclass(frozen=True, slots=True)
class DecisionMetadata:
    memory_id: str
    status: str
    supersedes_memory_id: str | None
    agent: str | None
    client: str | None
    session: str | None
    branch: str | None
    commit_hash: str | None
    source_request: str | None
    review_after: str | None
    access_count: int
    last_recalled_at: str | None
    last_confirmed_at: str | None


@dataclass(frozen=True, slots=True)
class GuardRun:
    id: str
    path: str
    patch_summary: str
    provider: str
    model: str
    payload_hash: str
    payload_tokens: int
    verdict: str
    confidence: float
    explanation: str
    recommended_action: str
    blocked: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class GuardEvidence:
    guard_run_id: str
    memory_id: str
    rank: int
    freshness: str


@dataclass(frozen=True, slots=True)
class GuardOverride:
    id: int
    guard_run_id: str
    actor: str
    reason: str
    timestamp: str


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
            # All decisions, including legacy Memory callers, receive the
            # durable lifecycle row required by schema v2.
            self.connection.execute(
                "INSERT INTO decision_metadata(memory_id) VALUES (?)",
                (memory.id,),
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

    def list_persistence_redactions(
        self, entity_type: str, entity_id: str
    ) -> list[tuple[str, str, str, str]]:
        """Return masked audits for guard and provenance persistence paths."""
        rows = self.connection.execute(
            """
            SELECT field, pattern_name, original_snippet, replacement
            FROM persistence_redaction_audit
            WHERE entity_type = ? AND entity_id = ?
            ORDER BY id
            """,
            (entity_type, entity_id),
        ).fetchall()
        return [tuple(row) for row in rows]

    def ensure_decision_metadata(
        self,
        memory_id: str,
        *,
        status: str = "active",
        supersedes_memory_id: str | None = None,
        agent: str | None = None,
        client: str | None = None,
        session: str | None = None,
        branch: str | None = None,
        commit_hash: str | None = None,
        source_request: str | None = None,
        review_after: str | None = None,
    ) -> DecisionMetadata:
        """Create immutable-by-default lifecycle metadata for a memory.

        Repeated calls are idempotent and return the originally recorded row;
        lifecycle code must use :meth:`set_decision_status` for a deliberate
        status transition.
        """
        self._require_memory(memory_id)
        self._validate_status(status)
        if supersedes_memory_id is not None:
            self._require_memory(supersedes_memory_id)
        redactions = RedactionLog()
        values = (
            memory_id,
            status,
            supersedes_memory_id,
            self._sanitize_optional(agent, "agent", redactions),
            self._sanitize_optional(client, "client", redactions),
            self._sanitize_optional(session, "session", redactions),
            self._sanitize_optional(branch, "branch", redactions),
            self._sanitize_optional(commit_hash, "commit_hash", redactions),
            self._sanitize_optional(source_request, "source_request", redactions),
            self._sanitize_optional(review_after, "review_after", redactions),
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO decision_metadata(
                    memory_id, status, supersedes_memory_id, agent, client,
                    session, branch, commit_hash, source_request, review_after
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    supersedes_memory_id = COALESCE(
                        decision_metadata.supersedes_memory_id,
                        excluded.supersedes_memory_id
                    ),
                    agent = COALESCE(decision_metadata.agent, excluded.agent),
                    client = COALESCE(decision_metadata.client, excluded.client),
                    session = COALESCE(decision_metadata.session, excluded.session),
                    branch = COALESCE(decision_metadata.branch, excluded.branch),
                    commit_hash = COALESCE(
                        decision_metadata.commit_hash, excluded.commit_hash
                    ),
                    source_request = COALESCE(
                        decision_metadata.source_request, excluded.source_request
                    ),
                    review_after = COALESCE(
                        decision_metadata.review_after, excluded.review_after
                    )
                """,
                values,
            )
            self._record_persistence_redactions(
                "decision_metadata", memory_id, redactions
            )
        metadata = self.get_decision_metadata(memory_id)
        assert metadata is not None
        return metadata

    def get_decision_metadata(self, memory_id: str) -> DecisionMetadata | None:
        row = self.connection.execute(
            """
            SELECT memory_id, status, supersedes_memory_id, agent, client,
                   session, branch, commit_hash, source_request, review_after,
                   access_count, last_recalled_at, last_confirmed_at
            FROM decision_metadata
            WHERE memory_id = ?
            """,
            (memory_id,),
        ).fetchone()
        return None if row is None else DecisionMetadata(*row)

    def set_decision_status(
        self,
        memory_id: str,
        status: str,
        *,
        supersedes_memory_id: str | None = None,
        review_after: str | None = None,
    ) -> DecisionMetadata:
        """Persist an explicit lifecycle transition for an existing decision."""
        self._validate_status(status)
        current = self.ensure_decision_metadata(memory_id)
        if supersedes_memory_id is not None:
            self._require_memory(supersedes_memory_id)
        redactions = RedactionLog()
        with self.connection:
            self.connection.execute(
                """
                UPDATE decision_metadata
                SET status = ?, supersedes_memory_id = ?, review_after = ?
                WHERE memory_id = ?
                """,
                (
                    status,
                    supersedes_memory_id
                    if supersedes_memory_id is not None
                    else current.supersedes_memory_id,
                    self._sanitize_optional(
                        review_after, "review_after", redactions
                    ),
                    memory_id,
                ),
            )
            self._record_persistence_redactions(
                "decision_metadata", memory_id, redactions
            )
        metadata = self.get_decision_metadata(memory_id)
        assert metadata is not None
        return metadata

    def record_recall(
        self, memory_id: str, timestamp: str
    ) -> DecisionMetadata:
        """Increment recall statistics without changing the memory itself."""
        self.ensure_decision_metadata(memory_id)
        with self.connection:
            self.connection.execute(
                """
                UPDATE decision_metadata
                SET access_count = access_count + 1, last_recalled_at = ?
                WHERE memory_id = ?
                """,
                (self._sanitize_optional(timestamp, "last_recalled_at"), memory_id),
            )
        metadata = self.get_decision_metadata(memory_id)
        assert metadata is not None
        return metadata

    def record_confirmation(
        self, memory_id: str, timestamp: str
    ) -> DecisionMetadata:
        """Record a human or agent confirmation without rewriting the decision."""
        self.ensure_decision_metadata(memory_id)
        with self.connection:
            self.connection.execute(
                """
                UPDATE decision_metadata
                SET last_confirmed_at = ?
                WHERE memory_id = ?
                """,
                (self._sanitize_optional(timestamp, "last_confirmed_at"), memory_id),
            )
        metadata = self.get_decision_metadata(memory_id)
        assert metadata is not None
        return metadata

    def record_guard_run(self, run: GuardRun) -> GuardRun:
        """Persist a sanitized, bounded guard result and return its stored form."""
        self._validate_guard_run(run)
        redactions = RedactionLog()
        stored = GuardRun(
            id=self._sanitize(run.id, "guard_run_id", redactions),
            path=self._sanitize(run.path, "path", redactions),
            patch_summary=self._sanitize(
                run.patch_summary, "patch_summary", redactions
            ),
            provider=self._sanitize(run.provider, "provider", redactions),
            model=self._sanitize(run.model, "model", redactions),
            payload_hash=self._sanitize(
                run.payload_hash, "payload_hash", redactions
            ),
            payload_tokens=run.payload_tokens,
            verdict=run.verdict,
            confidence=run.confidence,
            explanation=self._sanitize(run.explanation, "explanation", redactions),
            recommended_action=self._sanitize(
                run.recommended_action, "recommended_action", redactions
            ),
            blocked=run.blocked,
            created_at=self._sanitize(run.created_at, "created_at", redactions),
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO guard_runs(
                    id, path, patch_summary, provider, model, payload_hash,
                    payload_tokens, verdict, confidence, explanation,
                    recommended_action, blocked, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.id,
                    stored.path,
                    stored.patch_summary,
                    stored.provider,
                    stored.model,
                    stored.payload_hash,
                    stored.payload_tokens,
                    stored.verdict,
                    stored.confidence,
                    stored.explanation,
                    stored.recommended_action,
                    int(stored.blocked),
                    stored.created_at,
                ),
            )
            self._record_persistence_redactions(
                "guard_run", stored.id, redactions
            )
        return stored

    def get_guard_run(self, run_id: str) -> GuardRun | None:
        row = self.connection.execute(
            """
            SELECT id, path, patch_summary, provider, model, payload_hash,
                   payload_tokens, verdict, confidence, explanation,
                   recommended_action, blocked, created_at
            FROM guard_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        values = list(row)
        values[11] = bool(values[11])
        return GuardRun(*values)

    def record_guard_evidence(self, evidence: GuardEvidence) -> GuardEvidence:
        self._require_guard_run(evidence.guard_run_id)
        self._require_memory(evidence.memory_id)
        if evidence.rank < 0:
            raise ValueError("guard evidence rank must be non-negative")
        if evidence.freshness not in _FRESHNESS_STATES:
            raise ValueError(f"Invalid freshness state: {evidence.freshness!r}")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO guard_evidence(guard_run_id, memory_id, rank, freshness)
                VALUES (?, ?, ?, ?)
                """,
                (
                    evidence.guard_run_id,
                    evidence.memory_id,
                    evidence.rank,
                    evidence.freshness,
                ),
            )
        return evidence

    def list_guard_evidence(self, run_id: str) -> list[GuardEvidence]:
        rows = self.connection.execute(
            """
            SELECT guard_run_id, memory_id, rank, freshness
            FROM guard_evidence
            WHERE guard_run_id = ?
            ORDER BY rank, memory_id
            """,
            (run_id,),
        ).fetchall()
        return [GuardEvidence(*row) for row in rows]

    def record_guard_override(
        self,
        run_id: str,
        *,
        actor: str,
        reason: str,
        timestamp: str,
    ) -> GuardOverride:
        self._require_guard_run(run_id)
        redactions = RedactionLog()
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO guard_overrides(guard_run_id, actor, reason, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (
                    run_id,
                    self._sanitize(actor, "override_actor", redactions),
                    self._sanitize(reason, "override_reason", redactions),
                    self._sanitize(timestamp, "override_timestamp", redactions),
                ),
            )
            self._record_persistence_redactions(
                "guard_override", run_id, redactions
            )
        override = self.connection.execute(
            """
            SELECT id, guard_run_id, actor, reason, timestamp
            FROM guard_overrides
            WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
        assert override is not None
        return GuardOverride(*override)

    def list_guard_overrides(self, run_id: str) -> list[GuardOverride]:
        rows = self.connection.execute(
            """
            SELECT id, guard_run_id, actor, reason, timestamp
            FROM guard_overrides
            WHERE guard_run_id = ?
            ORDER BY id
            """,
            (run_id,),
        ).fetchall()
        return [GuardOverride(*row) for row in rows]

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
        if version < 0 or version > _SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported schema version: {version}")

        # SQLite DDL participates in this transaction. A failed migration
        # therefore leaves a v1 database, its FTS index, and audit data intact.
        with self.connection:
            if version == 0:
                self._execute_schema(_V1_SCHEMA)
                self.connection.execute("PRAGMA user_version = 1")
                version = 1
            elif version == 1:
                # Preserve the historical idempotent bootstrap behaviour for
                # valid v1 databases before applying the next migration.
                self._execute_schema(_V1_SCHEMA)

            while version < _SCHEMA_VERSION:
                self._migrate(version)
                version += 1

            # A reopened v2 database remains idempotent even if sqlite-vec
            # availability differs from its original host.
            if version == _SCHEMA_VERSION:
                self._execute_schema(_V2_SCHEMA)
                self._execute_schema(_V3_SCHEMA)
            if self._vec_available:
                self.connection.execute(_VEC_SCHEMA)

    def _migrate(self, version: int) -> None:
        if version == 1:
            self._execute_schema(_V2_SCHEMA)
            self.connection.execute(
                """
                INSERT OR IGNORE INTO decision_metadata(memory_id)
                SELECT id FROM memories
                """
            )
            self.connection.execute("PRAGMA user_version = 2")
            return
        if version == 2:
            self._execute_schema(_V3_SCHEMA)
            self.connection.execute("PRAGMA user_version = 3")
            return
        raise RuntimeError(f"No migration from schema version: {version}")

    def _execute_schema(self, statements: tuple[str, ...]) -> None:
        for statement in statements:
            self.connection.execute(statement)

    def _require_memory(self, memory_id: str) -> None:
        exists = self.connection.execute(
            "SELECT 1 FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if exists is None:
            raise ValueError(f"Unknown memory: {memory_id!r}")

    def _require_guard_run(self, run_id: str) -> None:
        exists = self.connection.execute(
            "SELECT 1 FROM guard_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if exists is None:
            raise ValueError(f"Unknown guard run: {run_id!r}")

    @staticmethod
    def _validate_status(status: str) -> None:
        if status not in _DECISION_STATUSES:
            raise ValueError(f"Invalid decision status: {status!r}")

    def _record_persistence_redactions(
        self, entity_type: str, entity_id: str, log: RedactionLog
    ) -> None:
        self.connection.executemany(
            """
            INSERT INTO persistence_redaction_audit(
                entity_type, entity_id, timestamp, field, pattern_name,
                original_snippet, replacement
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    entity_type,
                    entity_id,
                    entry.timestamp,
                    entry.field,
                    entry.pattern_name,
                    entry.original_snippet,
                    entry.replacement,
                )
                for entry in log.entries
            ],
        )

    @staticmethod
    def _sanitize(
        value: str, field_name: str, log: RedactionLog | None = None
    ) -> str:
        return sanitize(value, field_name=field_name, log=log)

    @classmethod
    def _sanitize_optional(
        cls,
        value: str | None,
        field_name: str,
        log: RedactionLog | None = None,
    ) -> str | None:
        return None if value is None else cls._sanitize(value, field_name, log)

    @staticmethod
    def _validate_guard_run(run: GuardRun) -> None:
        if not run.id:
            raise ValueError("guard run id must not be empty")
        if run.verdict not in _GUARD_VERDICTS:
            raise ValueError(f"Invalid guard verdict: {run.verdict!r}")
        if run.payload_tokens < 0:
            raise ValueError("payload_tokens must be non-negative")
        if not 0 <= run.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")

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
