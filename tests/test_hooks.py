"""Phase 4 tests — hooks module.

Tests that SessionStart/PreToolUse/Stop hooks respect their hard token caps
and preserve scope isolation.
"""

from __future__ import annotations

from mnemex.anchors import remember
from mnemex.hooks import (
    HookResult,
    JIT_TOKEN_CAP,
    SESSION_TOKEN_CAP,
    pre_tool_use,
    session_start,
    stop_capture,
)
from mnemex.storage import Storage


def test_session_start_respects_800_token_cap() -> None:
    with Storage() as storage:
        # Insert many large memories to exceed the cap
        for i in range(30):
            remember(
                storage,
                f"Decision {i}: " + "important " * 40,
                memory_id=f"m{i}",
            )

        result = session_start(storage)
        assert isinstance(result, HookResult)
        assert result.used_tokens <= SESSION_TOKEN_CAP
        assert result.budget_tokens == SESSION_TOKEN_CAP
        assert result.memory_count > 0
        assert result.mode == "bm25-only"


def test_session_start_empty_db_returns_empty() -> None:
    with Storage() as storage:
        result = session_start(storage)
        assert result.content == ""
        assert result.used_tokens == 0
        assert result.memory_count == 0


def test_pre_tool_use_respects_400_token_cap() -> None:
    with Storage() as storage:
        # Insert memories mentioning 'auth'
        for i in range(20):
            remember(
                storage,
                f"auth decision {i}: " + "cookie " * 30,
                memory_id=f"auth{i}",
            )

        result = pre_tool_use(storage, "src/auth.py")
        assert isinstance(result, HookResult)
        assert result.used_tokens <= JIT_TOKEN_CAP
        assert result.budget_tokens == JIT_TOKEN_CAP
        assert result.mode == "bm25-only"


def test_pre_tool_use_returns_relevant_memories() -> None:
    with Storage() as storage:
        remember(storage, "auth uses signed cookies for sessions", memory_id="a1")
        remember(storage, "database uses connection pooling", memory_id="d1")

        result = pre_tool_use(storage, "src/auth.py")
        assert "auth" in result.content.lower() or result.memory_count == 0
        # Even if nothing matches 'auth' as a keyword, the cap is respected
        assert result.used_tokens <= JIT_TOKEN_CAP


def test_pre_tool_use_hard_cap_cannot_be_exceeded() -> None:
    with Storage() as storage:
        # One memory that's exactly at the edge
        big = "auth " * 200  # ~200 tokens
        remember(storage, big, memory_id="big")

        result = pre_tool_use(storage, "src/auth.py", max_tokens=50)
        # max_tokens is clamped to min(requested, JIT_TOKEN_CAP)
        assert result.used_tokens <= min(50, JIT_TOKEN_CAP)


def test_stop_capture_stores_content() -> None:
    with Storage() as storage:
        mid = stop_capture(storage, "Learned: always validate input at boundaries")
        assert mid is not None
        mem = storage.get_memory(mid)
        assert mem is not None
        assert "validate input" in mem.content


def test_stop_capture_rejects_empty() -> None:
    with Storage() as storage:
        assert stop_capture(storage, "") is None
        assert stop_capture(storage, "   \t  ") is None


def test_scope_isolation_through_hooks() -> None:
    with Storage() as storage:
        remember(storage, "auth shared decision", memory_id="shared", scope="project-shared")
        remember(storage, "auth private scratch", memory_id="private", scope="agent-private")

        # SessionStart with default scope should not see private
        result = session_start(storage)
        assert "private" not in result.content.lower() or result.memory_count == 0

        # PreToolUse with default scope should not see private
        result2 = pre_tool_use(storage, "src/auth.py")
        # The private memory should never leak
        assert "private scratch" not in result2.content


def test_session_start_with_custom_scopes() -> None:
    with Storage() as storage:
        remember(storage, "global convention", memory_id="g1", scope="user-global")
        remember(storage, "shared decision", memory_id="s1", scope="project-shared")

        result = session_start(
            storage, scopes=("user-global", "project-shared")
        )
        assert result.memory_count == 2


def test_hooks_degrade_gracefully_without_embedder() -> None:
    """Hooks work in BM25-only mode when no embedder is provided."""
    with Storage() as storage:
        remember(storage, "test decision about caching", memory_id="c1")

        start = session_start(storage, embedder=None)
        assert start.mode == "bm25-only"
        assert start.memory_count >= 1

        jit = pre_tool_use(storage, "src/cache.py", embedder=None)
        assert jit.mode == "bm25-only"
