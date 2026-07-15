"""Integration coverage for write-time redaction."""

from __future__ import annotations

import asyncio

from mnemex.hooks import stop_capture
from mnemex.server import MnemexServer, create_server


AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"


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
