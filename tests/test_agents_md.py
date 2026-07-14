"""Phase 5 tests — why() fusion, AGENTS.md generation, staleness watcher.

Tests the fusion engine, AGENTS.md idempotence, and git-diff staleness detection.
"""

from __future__ import annotations

from pathlib import Path

from mnemex.agents_md import (
    AgentsMdResult,
    StalenessWatchResult,
    WhyResult,
    check_staleness_from_diff,
    generate_agents_md,
    why,
)
from mnemex.anchors import remember
from mnemex.indexer import index_file
from mnemex.storage import Node, Storage


@staticmethod
def _make_fixture(tmp_path: Path) -> Path:
    """Create a small Python project for testing."""
    (tmp_path / "auth.py").write_text(
        '''\
def authenticate(username, password):
    """Authenticate a user."""
    return validate(username, password)


def validate(username, password):
    """Validate credentials."""
    return True
''',
        encoding="utf-8",
    )
    return tmp_path


def test_why_returns_decisions_and_callers(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    with Storage() as storage:
        # Index the file
        index_file(storage, fixture / "auth.py")
        file_str = str(fixture / "auth.py").replace("\\", "/")

        # Add a decision anchored to validate
        nodes = storage.find_nodes(file_str, "validate")
        assert len(nodes) == 1
        remember(
            storage,
            "validate must check password strength",
            anchor=nodes[0].id,
            memory_id="decision-1",
        )

        # Query why "validate"
        result = why(storage, "validate")
        assert isinstance(result, WhyResult)
        assert result.query == "validate"
        assert len(result.decisions) >= 1
        assert any("password strength" in d.content for d in result.decisions)

        # Should find callers (authenticate calls validate)
        assert len(result.callers) >= 1
        assert any(c.name == "authenticate" for c in result.callers)


def test_why_without_callers_still_returns_decisions() -> None:
    with Storage() as storage:
        remember(
            storage,
            "always use UTC timestamps",
            memory_id="utc-decision",
        )

        result = why(storage, "UTC timestamps")
        assert isinstance(result, WhyResult)
        assert len(result.decisions) >= 1
        assert result.callers == ()


def test_generate_agents_md_produces_valid_output() -> None:
    with Storage() as storage:
        remember(storage, "Use signed cookies for auth", memory_id="d1")
        remember(storage, "Database uses connection pooling", memory_id="d2")

        result = generate_agents_md(storage)
        assert isinstance(result, AgentsMdResult)
        assert result.memory_count == 2
        assert "signed cookies" in result.content
        assert "connection pooling" in result.content
        assert result.changed is True  # No previous content


def test_generate_agents_md_idempotence() -> None:
    """Regenerating with no changes produces byte-identical output."""
    with Storage() as storage:
        remember(storage, "Always validate input", memory_id="d1")

        first = generate_agents_md(storage)
        second = generate_agents_md(storage, previous_content=first.content)

        assert second.content == first.content
        assert second.changed is False


def test_generate_agents_md_detects_changes() -> None:
    with Storage() as storage:
        remember(storage, "First decision", memory_id="d1")
        first = generate_agents_md(storage)

        # Add a new memory
        remember(storage, "Second decision", memory_id="d2")
        second = generate_agents_md(storage, previous_content=first.content)

        assert second.changed is True
        assert second.memory_count == 2
        assert "Second decision" in second.content


def test_generate_agents_md_marks_stale_memories(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    with Storage() as storage:
        index_file(storage, fixture / "auth.py")
        file_str = str(fixture / "auth.py").replace("\\", "/")

        nodes = storage.find_nodes(file_str, "validate")
        assert len(nodes) == 1
        remember(
            storage,
            "validate checks password strength",
            anchor=nodes[0].id,
            memory_id="anchored",
        )

        # Make the node stale by changing its hash
        storage.upsert_node(Node(
            id=nodes[0].id,
            type=nodes[0].type,
            name=nodes[0].name,
            file=nodes[0].file,
            line_start=nodes[0].line_start,
            content_hash="different-hash-now",
            language=nodes[0].language,
        ))

        result = generate_agents_md(storage)
        assert "STALE" in result.content


def test_generate_agents_md_empty_db() -> None:
    with Storage() as storage:
        result = generate_agents_md(storage)
        assert result.memory_count == 0
        assert "No decisions recorded" in result.content


def test_staleness_watcher_detects_changed_files(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    with Storage() as storage:
        index_file(storage, fixture / "auth.py")
        file_str = str(fixture / "auth.py").replace("\\", "/")

        nodes = storage.find_nodes(file_str, "validate")
        assert len(nodes) == 1
        remember(
            storage,
            "validate must be strict",
            anchor=nodes[0].id,
            memory_id="strict",
        )

        # Make it stale
        storage.upsert_node(Node(
            id=nodes[0].id,
            type=nodes[0].type,
            name=nodes[0].name,
            file=nodes[0].file,
            line_start=nodes[0].line_start,
            content_hash="changed-hash",
            language=nodes[0].language,
        ))

        # Simulate a git diff that touches auth.py
        diff = f"""\
diff --git a/auth.py b/auth.py
index abc1234..def5678 100644
--- a/{file_str}
+++ b/{file_str}
@@ -5,3 +5,3 @@
-    return True
+    return check_password_strength(password)
"""

        result = check_staleness_from_diff(storage, diff)
        assert isinstance(result, StalenessWatchResult)
        assert len(result.stale_memories) >= 1
        assert any(r.memory_id == "strict" for r in result.stale_memories)


def test_staleness_watcher_empty_diff() -> None:
    with Storage() as storage:
        result = check_staleness_from_diff(storage, "")
        assert result.stale_memories == ()
        assert result.files_affected == ()


def test_staleness_watcher_unaffected_files() -> None:
    with Storage() as storage:
        # Memory anchored to a node, but diff touches a different file
        storage.upsert_node(Node(
            id="n1", type="function", name="auth",
            file="src/auth.py", line_start=1,
            content_hash="h1", language="python",
        ))
        remember(storage, "auth decision", anchor="n1", memory_id="a1")

        diff = """\
diff --git a/utils.py b/utils.py
--- a/utils.py
+++ b/utils.py
@@ -1 +1 @@
-old
+new
"""
        result = check_staleness_from_diff(storage, diff)
        # auth.py not in diff, so no stale memories flagged
        assert len(result.stale_memories) == 0
