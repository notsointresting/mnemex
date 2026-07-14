"""Phase 4 tests — MCP server.

Tests the FastMCP server instantiation, tool listing, and round-trip
remember/recall through the MCP tool interface.
"""

from __future__ import annotations

import asyncio

import pytest

from mnemex.server import MnemexServer, create_server


@pytest.fixture
def server() -> MnemexServer:
    s = create_server(":memory:")
    yield s
    s.close()


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def test_server_instantiates_with_all_tools(server: MnemexServer) -> None:
    tools = _run(server.mcp.list_tools())
    names = sorted(t.name for t in tools)
    assert len(names) == 10
    expected = sorted([
        "remember_decision",
        "recall_memories",
        "forget_memory",
        "check_memory_freshness",
        "context_for",
        "get_context_brief",
        "why",
        "trace_callers_tool",
        "index_path",
        "generate_agents_md",
    ])
    assert names == expected


def test_remember_recall_roundtrip(server: MnemexServer) -> None:
    # Store a memory
    result = _run(server.mcp.call_tool(
        "remember_decision",
        {"content": "Use JWT for auth tokens", "rationale": "Stateless sessions"},
    ))
    # call_tool returns (contents, raw_result)
    contents, raw = result
    assert "memory_id" in raw or raw.get("status") == "stored"

    # Recall it
    result2 = _run(server.mcp.call_tool(
        "recall_memories",
        {"query": "JWT auth tokens"},
    ))
    _, raw2 = result2
    assert raw2["mode"] == "bm25-only"
    assert len(raw2["included"]) >= 1
    assert any("JWT" in m["content"] for m in raw2["included"])


def test_forget_removes_memory(server: MnemexServer) -> None:
    _, raw = _run(server.mcp.call_tool(
        "remember_decision",
        {"content": "temporary note about caching"},
    ))
    mid = raw["memory_id"]

    _, raw2 = _run(server.mcp.call_tool("forget_memory", {"memory_id": mid}))
    assert raw2["deleted"] is True

    _, raw3 = _run(server.mcp.call_tool(
        "recall_memories", {"query": "caching"}
    ))
    assert all(m["id"] != mid for m in raw3["included"])


def test_context_for_respects_400_token_cap(server: MnemexServer) -> None:
    # Insert a large memory
    big_content = "auth " * 200  # ~200 tokens
    _run(server.mcp.call_tool(
        "remember_decision", {"content": big_content}
    ))
    _run(server.mcp.call_tool(
        "remember_decision", {"content": "auth " * 200}
    ))

    _, raw = _run(server.mcp.call_tool(
        "context_for", {"path": "src/auth.py", "max_tokens": 400}
    ))
    assert raw["used_tokens"] <= 400
    assert raw["budget_tokens"] == 400


def test_get_context_brief_respects_800_token_cap(
    server: MnemexServer,
) -> None:
    # Insert many memories
    for i in range(20):
        _run(server.mcp.call_tool(
            "remember_decision",
            {"content": f"Decision {i}: " + "word " * 50},
        ))

    _, raw = _run(server.mcp.call_tool(
        "get_context_brief", {"max_tokens": 800}
    ))
    assert raw["used_tokens"] <= 800
    assert raw["budget_tokens"] == 800


def test_check_freshness_returns_reports(server: MnemexServer) -> None:
    _run(server.mcp.call_tool(
        "remember_decision", {"content": "A decision without anchor"}
    ))
    _, raw = _run(server.mcp.call_tool(
        "check_memory_freshness", {"scopes": "project-shared"}
    ))
    assert "reports" in raw
    assert len(raw["reports"]) == 1
    assert raw["reports"][0]["status"] == "unanchored"


def test_generate_agents_md_returns_content(server: MnemexServer) -> None:
    _run(server.mcp.call_tool(
        "remember_decision", {"content": "Always use UTC timestamps"}
    ))
    _, raw = _run(server.mcp.call_tool("generate_agents_md", {}))
    assert "content" in raw
    assert "UTC timestamps" in raw["content"]
    assert raw["memory_count"] == 1


def test_why_returns_decisions(server: MnemexServer) -> None:
    _run(server.mcp.call_tool(
        "remember_decision",
        {"content": "authenticate uses signed cookies for sessions"},
    ))
    _, raw = _run(server.mcp.call_tool(
        "why", {"symbol_or_file": "authenticate cookies"}
    ))
    assert "decisions" in raw
    assert len(raw["decisions"]) >= 1


def test_invalid_scope_returns_error(server: MnemexServer) -> None:
    _, raw = _run(server.mcp.call_tool(
        "recall_memories", {"query": "test", "scopes": "bogus"}
    ))
    assert "error" in raw
