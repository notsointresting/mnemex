"""MCP coverage for file-scoped JIT context and hard token limits."""

from __future__ import annotations

import asyncio
from pathlib import Path

from mnemex.server import MnemexServer, create_server


def _call(server: MnemexServer, name: str, arguments: dict[str, object]):
    return asyncio.run(server.mcp.call_tool(name, arguments))[1]


def test_context_for_prefers_anchor_file_then_uses_bm25_fallback(
    tmp_path: Path,
) -> None:
    auth = tmp_path / "auth.py"
    billing = tmp_path / "billing.py"
    auth.write_text("def authenticate():\n    return True\n", encoding="utf-8")
    billing.write_text("def charge():\n    return True\n", encoding="utf-8")
    auth_path = str(auth).replace("\\", "/")
    billing_path = str(billing).replace("\\", "/")

    server = create_server(":memory:")
    try:
        _call(server, "index_path", {"path": auth_path})
        anchored = _call(
            server,
            "remember_decision",
            {
                "content": "Authentication must use signed cookies.",
                "anchor_file": auth_path,
                "anchor_symbol": "authenticate",
            },
        )

        auth_context = _call(server, "context_for", {"path": auth_path})
        assert auth_context["mode"] == "anchor-file"
        assert [memory["id"] for memory in auth_context["memories"]] == [
            anchored["memory_id"]
        ]

        billing_context = _call(server, "context_for", {"path": billing_path})
        assert anchored["memory_id"] not in {
            memory["id"] for memory in billing_context["memories"]
        }

        fallback = _call(
            server,
            "remember_decision",
            {"content": "Billing retries require idempotency keys."},
        )
        billing_context = _call(server, "context_for", {"path": billing_path})
        assert billing_context["mode"] == "bm25-only"
        assert fallback["memory_id"] in {
            memory["id"] for memory in billing_context["memories"]
        }
    finally:
        server.close()


def test_mcp_context_caps_are_clamped_including_negative_values() -> None:
    server = create_server(":memory:")
    try:
        for number in range(30):
            _call(
                server,
                "remember_decision",
                {"content": f"auth rule {number}: " + "cookie " * 50},
            )

        oversized = _call(
            server,
            "context_for",
            {"path": "src/auth.py", "max_tokens": 10_000},
        )
        assert oversized["budget_tokens"] == 400
        assert oversized["used_tokens"] <= 400

        negative = _call(
            server,
            "context_for",
            {"path": "src/auth.py", "max_tokens": -1},
        )
        assert negative["budget_tokens"] == 0
        assert negative["used_tokens"] == 0
        assert negative["memories"] == []

        brief = _call(server, "get_context_brief", {"max_tokens": 5_000})
        assert brief["budget_tokens"] == 800
        assert brief["used_tokens"] <= 800
    finally:
        server.close()
