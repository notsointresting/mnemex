"""Integration coverage for write-time redaction.

All secret-shaped values are SYNTHETIC and assembled at runtime so the
committed source never contains a complete secret pattern.
"""

from __future__ import annotations

import asyncio
import sqlite3

from mnemex.hooks import stop_capture
from mnemex.server import MnemexServer, create_server


AWS_ACCESS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
PASSWORD_VALUE = "hunter2" + "secretvalue"
OPENAI_KEY = "sk-proj-" + "abcdefghijklmnopqrstuvwxyz123456"


def _call(server: MnemexServer, name: str, arguments: dict[str, object]):
    return asyncio.run(server.mcp.call_tool(name, arguments))[1]


def test_remember_decision_never_persists_aws_key() -> None:
    server = create_server(":memory:")
    try:
        result = _call(
            server,
            "remember_decision",
            {"content": f"Deploy credential: {AWS_ACCESS_KEY}"},
        )

        stored = server.storage.get_memory(result["memory_id"])
        assert stored is not None
        assert AWS_ACCESS_KEY not in stored.content
        assert "[REDACTED" in stored.content
        audit_entries = server.storage.list_redactions(result["memory_id"])
        assert audit_entries
        assert audit_entries[0][1] == "aws_access_key"
        assert AWS_ACCESS_KEY not in audit_entries[0][2]
    finally:
        server.close()


def test_password_never_persisted_anywhere_in_db(tmp_path) -> None:
    """The audited leak: password=... must not survive in any table,
    the FTS index, or the raw database file bytes."""
    db_path = tmp_path / "leak-check.sqlite3"
    server = create_server(str(db_path))
    try:
        _call(
            server,
            "remember_decision",
            {
                "content": f"Use password={PASSWORD_VALUE} for staging",
                "rationale": f"Ops shared password={PASSWORD_VALUE} today",
                "tags": f"password={PASSWORD_VALUE}",
            },
        )
    finally:
        server.close()

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT content, rationale, tags FROM memories"
        ).fetchall()
        assert rows
        for row in rows:
            for field in row:
                assert PASSWORD_VALUE not in (field or "")
        fts_rows = connection.execute(
            "SELECT content FROM memories_fts"
        ).fetchall()
        for (content,) in fts_rows:
            assert PASSWORD_VALUE not in (content or "")
    finally:
        connection.close()

    raw = db_path.read_bytes()
    assert PASSWORD_VALUE.encode("utf-8") not in raw


def test_openai_key_never_persisted_anywhere_in_db(tmp_path) -> None:
    """A bare provider key pasted into a decision must be redacted."""
    db_path = tmp_path / "leak-check-openai.sqlite3"
    server = create_server(str(db_path))
    try:
        _call(
            server,
            "remember_decision",
            {"content": f"Judge credential {OPENAI_KEY} configured"},
        )
    finally:
        server.close()

    raw = db_path.read_bytes()
    assert OPENAI_KEY.encode("utf-8") not in raw


def test_stop_capture_never_persists_aws_key() -> None:
    server = create_server(":memory:")
    try:
        memory_id = stop_capture(
            server.storage, f"Captured credential: {AWS_ACCESS_KEY}"
        )

        assert memory_id is not None
        stored = server.storage.get_memory(memory_id)
        assert stored is not None
        assert AWS_ACCESS_KEY not in stored.content
        assert "[REDACTED" in stored.content
    finally:
        server.close()
