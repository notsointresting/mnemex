"""Phase 4 tests — MCP server.

Tests the FastMCP server instantiation, tool listing, and round-trip
remember/recall through the MCP tool interface.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from mnemex.server import MnemexServer, create_server
from mnemex.judge import SemanticJudgment, Verdict
from mnemex.storage import Node


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
    assert len(names) == 16
    expected = sorted([
        "remember_decision",
        "recall_memories",
        "review_conflicts",
        "reconcile_stale_decision",
        "forget_memory",
        "check_memory_freshness",
        "check_proposed_change",
        "context_for",
        "get_context_brief",
        "why",
        "trace_callers_tool",
        "index_path",
        "import_brain",
        "generate_agents_md",
        "override_decision_guard",
        "export_brain",
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


class _ContradictionJudge:
    def evaluate(self, evidence: str) -> SemanticJudgment:
        decision_id = json.loads(evidence)["decisions"][0]["memory_id"]
        return SemanticJudgment(
            Verdict.CONTRADICTION,
            0.95,
            "The proposal conflicts with the recorded decision.",
            evidence_ids=(decision_id,),
            model="fixture",
        )


def test_guard_tools_return_auditable_block_and_override() -> None:
    server = create_server(":memory:", semantic_judge=_ContradictionJudge())
    try:
        server.storage.upsert_node(
            Node(
                id="auth-node",
                type="function",
                name="authenticate",
                file="src/auth.py",
                line_start=1,
                content_hash="auth-hash",
                language="python",
            )
        )
        _, remembered = _run(server.mcp.call_tool(
            "remember_decision",
            {
                "content": "Authentication must remain stateless.",
                "anchor_node_id": "auth-node",
            },
        ))
        assert remembered["memory_id"]
        _, checked = _run(server.mcp.call_tool(
            "check_proposed_change",
            {"path": "src/auth.py", "patch_summary": "Add server sessions."},
        ))
        assert checked["blocked"] is True
        assert checked["payload_summary"]["tokens"] <= 800

        _, overridden = _run(server.mcp.call_tool(
            "override_decision_guard",
            {"run_id": checked["run_id"], "actor": "codex", "reason": "approved"},
        ))
        assert overridden["run_id"] == checked["run_id"]
    finally:
        server.close()


def test_guard_tool_uses_the_server_configured_evidence_cap() -> None:
    server = create_server(":memory:", max_evidence_tokens=16)
    try:
        _, checked = _run(server.mcp.call_tool(
            "check_proposed_change",
            {
                "path": "src/auth.py",
                "patch_summary": "word " * 100,
                "max_evidence_tokens": 999,
            },
        ))
        assert checked["payload_summary"]["budget_tokens"] == 16
        assert checked["payload_summary"]["tokens"] <= 16
    finally:
        server.close()


def test_reconcile_tool_returns_fixed_lifecycle_state(server: MnemexServer) -> None:
    _, remembered = _run(server.mcp.call_tool(
        "remember_decision", {"content": "Keep authentication stateless"}
    ))

    _, reconciled = _run(server.mcp.call_tool(
        "reconcile_stale_decision",
        {
            "memory_id": remembered["memory_id"],
            "changed_symbol": "authenticate",
            "diff": "changed authentication",
        },
    ))

    assert reconciled["status"] == "human_review"


def test_remember_event_populates_local_embeddings_when_available() -> None:
    from mnemex.storage import Storage

    with Storage() as probe:
        if not probe.vec_available:
            pytest.skip("sqlite-vec extension unavailable")

    def embedder(text: str) -> list[float]:
        return [0.0] * 384

    server = create_server(":memory:", embedder=embedder)
    try:
        _, stored = _run(server.mcp.call_tool(
            "remember_decision", {"content": "event-driven embeddings"}
        ))
        row = server.storage.connection.execute(
            "SELECT rowid FROM memories WHERE id = ?", (stored["memory_id"],)
        ).fetchone()
        assert server.storage.connection.execute(
            "SELECT 1 FROM memories_vec WHERE rowid = ?", row
        ).fetchone()
    finally:
        server.close()
