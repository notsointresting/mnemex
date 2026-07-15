from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mnemex.storage import (
    DecisionMetadata,
    GuardEvidence,
    GuardRun,
    Storage,
    _V1_SCHEMA,
)
from tests.test_storage import fts_memory_ids, make_memory, make_node


def make_v1_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        for statement in _V1_SCHEMA:
            connection.execute(statement)
        connection.execute("PRAGMA user_version = 1")
        connection.execute(
            """
            INSERT INTO nodes(
                id, type, name, file, line_start, content_hash, language
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("node-1", "function", "authenticate", "src/auth.py", 10, "hash-1", "python"),
        )
        connection.execute(
            """
            INSERT INTO memories(
                id, type, content, rationale, anchor_node_id, anchor_hash,
                scope, source, confidence, importance, created_at,
                last_accessed, last_verified, tags
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "memory-1",
                "decision",
                "Use signed session cookies for authentication.",
                "They keep request handling stateless.",
                "node-1",
                "hash-1",
                "project-shared",
                "v1-test",
                0.9,
                0.8,
                "2026-07-14T08:00:00Z",
                "2026-07-14T08:00:00Z",
                "2026-07-14T08:00:00Z",
                "auth,cookies",
            ),
        )
        connection.execute(
            """
            INSERT INTO redaction_audit(
                memory_id, timestamp, field, pattern_name,
                original_snippet, replacement
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "memory-1",
                "2026-07-14T08:00:00Z",
                "content",
                "api-key",
                "sk-a...xyz",
                "[REDACTED]",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def make_guard_run(run_id: str = "run-1") -> GuardRun:
    return GuardRun(
        id=run_id,
        path="src/auth.py",
        patch_summary="Preserve signed cookie validation.",
        provider="fixture",
        model="fixture-1",
        payload_hash="payload-hash",
        payload_tokens=125,
        verdict="contradiction",
        confidence=0.95,
        explanation="The patch removes the recorded validation boundary.",
        recommended_action="Keep signature verification.",
        blocked=True,
        created_at="2026-07-15T08:00:00Z",
    )


def test_v1_migration_preserves_records_fts_and_audit(tmp_path: Path) -> None:
    database = tmp_path / "v1.sqlite3"
    make_v1_database(database)

    with Storage(database) as storage:
        assert storage.connection.execute("PRAGMA user_version").fetchone() == (3,)
        assert storage.get_node("node-1") == make_node()
        assert storage.get_memory("memory-1") == replace(
            make_memory(), source="v1-test"
        )
        assert fts_memory_ids(storage, "stateless") == ["memory-1"]
        assert storage.list_redactions("memory-1") == [
            ("content", "api-key", "sk-a...xyz", "[REDACTED]")
        ]
        assert storage.get_decision_metadata("memory-1").status == "active"
        tables = {
            row[0]
            for row in storage.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "decision_metadata",
            "guard_runs",
            "guard_evidence",
            "guard_overrides",
            "persistence_redaction_audit",
        } <= tables


def test_v2_crud_survives_reopen_and_sanitizes_guard_data(tmp_path: Path) -> None:
    database = tmp_path / "v2.sqlite3"
    with Storage(database) as storage:
        storage.upsert_node(make_node())
        storage.insert_memory(make_memory())
        metadata = storage.ensure_decision_metadata(
            "memory-1",
            agent="codex",
            client="codex-desktop",
            session="session-1",
            branch="codex/guard",
            commit_hash="abc123",
            source_request="Implement guard",
            review_after="2026-08-01T00:00:00Z",
        )
        assert metadata == DecisionMetadata(
            memory_id="memory-1",
            status="active",
            supersedes_memory_id=None,
            agent="codex",
            client="codex-desktop",
            session="session-1",
            branch="codex/guard",
            commit_hash="abc123",
            source_request="Implement guard",
            review_after="2026-08-01T00:00:00Z",
            access_count=0,
            last_recalled_at=None,
            last_confirmed_at=None,
        )
        assert storage.record_recall("memory-1", "2026-07-15T09:00:00Z").access_count == 1
        assert storage.record_confirmation(
            "memory-1", "2026-07-15T10:00:00Z"
        ).last_confirmed_at == "2026-07-15T10:00:00Z"

        stored_run = storage.record_guard_run(make_guard_run())
        assert stored_run.id == "run-1"
        evidence = GuardEvidence("run-1", "memory-1", 0, "fresh")
        assert storage.record_guard_evidence(evidence) == evidence
        override = storage.record_guard_override(
            "run-1",
            actor="codex",
            reason="<private>api_key=abcdefghijklmnopqrstuvwxyz</private>",
            timestamp="2026-07-15T11:00:00Z",
        )
        assert override.reason == ""
        audits = storage.list_persistence_redactions("guard_override", "run-1")
        assert len(audits) == 1
        assert "abcdefghijklmnopqrstuvwxyz" not in str(audits)
        assert audits[0][-1] == ""
        assert storage.list_guard_evidence("run-1") == [evidence]

    with Storage(database) as reopened:
        assert reopened.connection.execute("PRAGMA user_version").fetchone() == (3,)
        assert reopened.get_decision_metadata("memory-1").access_count == 1
        assert reopened.get_guard_run("run-1").blocked is True
        assert reopened.list_guard_overrides("run-1") == [override]


def test_failed_v1_migration_rolls_back_without_losing_v1_data(tmp_path: Path) -> None:
    database = tmp_path / "broken-v1.sqlite3"
    make_v1_database(database)
    connection = sqlite3.connect(database)
    try:
        # A v1 database cannot already have this view. It forces the first v2
        # DDL statement to fail after the transaction has begun.
        connection.execute("CREATE VIEW decision_metadata AS SELECT id FROM memories")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(sqlite3.OperationalError, match="views may not be indexed"):
        Storage(database)

    preserved = sqlite3.connect(database)
    try:
        assert preserved.execute("PRAGMA user_version").fetchone() == (1,)
        assert preserved.execute("SELECT id FROM memories").fetchall() == [("memory-1",)]
        assert preserved.execute(
            "SELECT original_snippet FROM redaction_audit"
        ).fetchall() == [("sk-a...xyz",)]
        assert preserved.execute(
            "SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'stateless'"
        ).fetchall()
    finally:
        preserved.close()


def test_unknown_future_schema_version_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA user_version = 999")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="Unsupported schema version: 999"):
        Storage(database)
