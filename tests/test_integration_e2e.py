"""End-to-end MCP tests for the Day 1 product claims."""

from __future__ import annotations

import asyncio
from pathlib import Path

from mnemex.server import MnemexServer, create_server


def _call(server: MnemexServer, name: str, arguments: dict[str, object]):
    return asyncio.run(server.mcp.call_tool(name, arguments))[1]


def test_mcp_why_returns_anchored_decision_and_callers(tmp_path: Path) -> None:
    source = tmp_path / "auth.py"
    source.write_text(
        "def authenticate():\n    return validate()\n\n\ndef validate():\n    return True\n",
        encoding="utf-8",
    )
    source_path = str(source).replace("\\", "/")
    server = create_server(":memory:")
    try:
        indexed = _call(server, "index_path", {"path": source_path})
        assert indexed["edges_upserted"] >= 1
        remembered = _call(
            server,
            "remember_decision",
            {
                "content": "validate requires password strength checks.",
                "rationale": "Keep credential policy centralized.",
                "anchor_file": source_path,
                "anchor_symbol": "validate",
            },
        )

        result = _call(server, "why", {"symbol_or_file": "validate"})
        assert remembered["memory_id"] in {
            decision["id"] for decision in result["decisions"]
        }
        assert any(caller["name"] == "authenticate" for caller in result["callers"])
        assert result["used_tokens"] <= 400
    finally:
        server.close()


def test_mcp_generate_agents_md_groups_anchored_and_global_memories(
    tmp_path: Path,
) -> None:
    source = tmp_path / "auth.py"
    source.write_text("def authenticate():\n    return True\n", encoding="utf-8")
    source_path = str(source).replace("\\", "/")
    server = create_server(":memory:")
    try:
        _call(server, "index_path", {"path": source_path})
        _call(
            server,
            "remember_decision",
            {
                "content": "Authentication uses signed cookies.",
                "anchor_file": source_path,
                "anchor_symbol": "authenticate",
            },
        )
        _call(
            server,
            "remember_decision",
            {"content": "Use UTC timestamps for persisted events."},
        )

        result = _call(server, "generate_agents_md", {})
        assert result["memory_count"] == 2
        assert f"## {source_path}" in result["content"]
        assert "## (global)" in result["content"]
        assert "Authentication uses signed cookies." in result["content"]
        assert "Use UTC timestamps for persisted events." in result["content"]
        assert result["changed"] is True
    finally:
        server.close()
