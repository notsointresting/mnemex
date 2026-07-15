"""Phase 3 tests — structural backend adapter (indexer).

Uses a golden fixture repo with known Python files to verify node/edge
extraction, incremental re-indexing, caller tracing, and anchor resolution.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mnemex.anchors import Anchor, resolve_anchor
from mnemex.indexer import (
    PythonASTAdapter,
    TypeScriptAdapter,
    index_directory,
    index_file,
    reindex_file,
    trace_callers,
)
from mnemex.storage import Storage


@pytest.fixture
def golden_repo(tmp_path: Path) -> Path:
    """A small Python project with known structure for testing."""
    (tmp_path / "models.py").write_text(
        '''\
class Base:
    """Base model."""
    pass


class User(Base):
    """A user model."""

    def validate(self):
        return True
''',
        encoding="utf-8",
    )
    (tmp_path / "service.py").write_text(
        '''\
from models import User


def create_user(name):
    """Create a new user."""
    user = User()
    user.validate()
    return user


def delete_user(user_id):
    """Delete a user."""
    pass
''',
        encoding="utf-8",
    )
    (tmp_path / "utils.py").write_text(
        '''\
def helper():
    """A utility function."""
    return 42


def format_name(name):
    """Format a name."""
    return helper()
''',
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def typescript_repo(tmp_path: Path) -> Path:
    """A small TypeScript project with cross-file structural links."""
    (tmp_path / "base.ts").write_text(
        '''\
export class Base {
    protected label(): string {
        return "base";
    }
}

export function render(value: string): string {
    return value.toUpperCase();
}
''',
        encoding="utf-8",
    )
    (tmp_path / "service.ts").write_text(
        '''\
import { Base, render } from "./base";

export class Widget extends Base {
    public run(): string {
        return render(this.label());
    }
}

export function boot(): string {
    return render("ready");
}
''',
        encoding="utf-8",
    )
    (tmp_path / "panel.tsx").write_text(
        '''\
export function Panel() {
    return <section>Panel</section>;
}
''',
        encoding="utf-8",
    )
    return tmp_path


def test_index_file_extracts_correct_nodes(
    golden_repo: Path,
) -> None:
    with Storage() as storage:
        result = index_file(storage, golden_repo / "models.py")

        assert result.nodes_upserted >= 4  # module + Base + User + validate
        assert result.nodes_deleted == 0

        file_str = str(golden_repo / "models.py").replace("\\", "/")
        rows = storage.connection.execute(
            "SELECT name, type FROM nodes WHERE file = ? ORDER BY line_start",
            (file_str,),
        ).fetchall()
        names = [row[0] for row in rows]
        types = [row[1] for row in rows]

        assert "models" in names  # module node
        assert "Base" in names
        assert "User" in names
        assert "validate" in names
        assert "class" in types
        assert "function" in types


def test_index_file_extracts_edges(golden_repo: Path) -> None:
    with Storage() as storage:
        index_file(storage, golden_repo / "models.py")

        edges = storage.connection.execute(
            "SELECT from_id, to_id, type FROM edges"
        ).fetchall()

        # User inherits from Base
        edge_types = [row[2] for row in edges]
        assert "inherits" in edge_types


def test_index_file_extracts_call_edges(golden_repo: Path) -> None:
    with Storage() as storage:
        # Index utils.py which has format_name calling helper
        index_file(storage, golden_repo / "utils.py")

        edges = storage.connection.execute(
            "SELECT e.type FROM edges e "
            "JOIN nodes caller ON caller.id = e.from_id "
            "JOIN nodes callee ON callee.id = e.to_id "
            "WHERE caller.name = 'format_name' AND callee.name = 'helper'",
        ).fetchall()
        assert len(edges) == 1
        assert edges[0][0] == "calls"


def test_index_directory_indexes_all_files(golden_repo: Path) -> None:
    with Storage() as storage:
        index_directory(storage, golden_repo)

        # All 3 files should be indexed
        files = storage.connection.execute(
            "SELECT DISTINCT file FROM nodes"
        ).fetchall()
        assert len(files) == 3

        # Total nodes: models(4) + service(3) + utils(3) = 10
        total = storage.connection.execute(
            "SELECT count(*) FROM nodes"
        ).fetchone()[0]
        assert total >= 9  # at minimum module + top-level defs per file


def test_index_directory_skips_generated_dependency_directories(tmp_path: Path) -> None:
    (tmp_path / "application.py").write_text("def app():\n    return True\n")
    ignored = tmp_path / ".venv"
    ignored.mkdir()
    (ignored / "dependency.py").write_text("def dependency():\n    return True\n")

    with Storage() as storage:
        index_directory(storage, tmp_path)
        files = {
            row[0] for row in storage.connection.execute(
                "SELECT DISTINCT file FROM nodes"
            ).fetchall()
        }

    assert any(file.endswith("application.py") for file in files)
    assert not any(".venv" in file for file in files)


def test_trace_callers_returns_reverse_edges(golden_repo: Path) -> None:
    with Storage() as storage:
        index_file(storage, golden_repo / "utils.py")

        file_str = str(golden_repo / "utils.py").replace("\\", "/")
        helper_nodes = storage.find_nodes(file_str, "helper")
        assert len(helper_nodes) == 1

        callers = trace_callers(storage, helper_nodes[0].id)
        caller_names = [node.name for node, _edge_type in callers]
        assert "format_name" in caller_names


def test_reindex_only_updates_changed_nodes(golden_repo: Path) -> None:
    with Storage() as storage:
        # Initial index
        index_file(storage, golden_repo / "utils.py")
        file_str = str(golden_repo / "utils.py").replace("\\", "/")

        original_nodes = storage.connection.execute(
            "SELECT id, content_hash FROM nodes WHERE file = ?",
            (file_str,),
        ).fetchall()
        original_hashes = {row[0]: row[1] for row in original_nodes}

        # Modify only helper(), leave format_name unchanged
        (golden_repo / "utils.py").write_text(
            '''\
def helper():
    """A utility function - MODIFIED."""
    return 99


def format_name(name):
    """Format a name."""
    return helper()
''',
            encoding="utf-8",
        )

        reindex_file(storage, golden_repo / "utils.py")

        # Only helper and the module should have changed
        new_nodes = storage.connection.execute(
            "SELECT id, content_hash FROM nodes WHERE file = ?",
            (file_str,),
        ).fetchall()
        new_hashes = {row[0]: row[1] for row in new_nodes}

        # format_name's hash should be unchanged
        format_name_nodes = storage.find_nodes(file_str, "format_name")
        assert len(format_name_nodes) == 1
        fn_id = format_name_nodes[0].id
        if fn_id in original_hashes:
            assert new_hashes[fn_id] == original_hashes[fn_id]

        # helper's hash should have changed
        helper_nodes = storage.find_nodes(file_str, "helper")
        assert len(helper_nodes) == 1
        h_id = helper_nodes[0].id
        if h_id in original_hashes:
            assert new_hashes[h_id] != original_hashes[h_id]


def test_reindex_deletes_vanished_symbols(golden_repo: Path) -> None:
    with Storage() as storage:
        index_file(storage, golden_repo / "utils.py")
        file_str = str(golden_repo / "utils.py").replace("\\", "/")

        # Confirm format_name exists
        assert len(storage.find_nodes(file_str, "format_name")) == 1

        # Remove format_name from the file
        (golden_repo / "utils.py").write_text(
            '''\
def helper():
    """Only helper remains."""
    return 42
''',
            encoding="utf-8",
        )

        result = reindex_file(storage, golden_repo / "utils.py")
        assert result.nodes_deleted >= 1

        # format_name should be gone
        assert len(storage.find_nodes(file_str, "format_name")) == 0
        # helper should still exist
        assert len(storage.find_nodes(file_str, "helper")) == 1


def test_anchor_resolution_after_indexing(golden_repo: Path) -> None:
    with Storage() as storage:
        index_file(storage, golden_repo / "models.py")
        file_str = str(golden_repo / "models.py").replace("\\", "/")

        # Resolve by file + symbol
        anchor = Anchor(file=file_str, symbol="User")
        node = resolve_anchor(storage, anchor)
        assert node.name == "User"
        assert node.type == "class"
        assert node.file == file_str


def test_python_adapter_handles_syntax_errors(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("def broken(:\n    pass\n", encoding="utf-8")

    adapter = PythonASTAdapter()
    nodes = adapter.extract_nodes(bad_file, bad_file.read_text())
    assert nodes == []


def test_content_hash_is_deterministic(golden_repo: Path) -> None:
    with Storage() as storage:
        index_file(storage, golden_repo / "utils.py")
        file_str = str(golden_repo / "utils.py").replace("\\", "/")
        first = storage.find_nodes(file_str, "helper")[0].content_hash

    with Storage() as storage2:
        index_file(storage2, golden_repo / "utils.py")
        file_str = str(golden_repo / "utils.py").replace("\\", "/")
        second = storage2.find_nodes(file_str, "helper")[0].content_hash

    assert first == second
    assert len(first) == 64  # SHA-256 hex


def test_typescript_adapter_extracts_cross_file_structure(
    typescript_repo: Path,
) -> None:
    with Storage() as storage:
        # Index the imported module first so cross-file targets exist in storage.
        index_file(storage, typescript_repo / "base.ts")
        result = index_file(storage, typescript_repo / "service.ts")

        service_file = str(typescript_repo / "service.ts").replace("\\", "/")
        rows = storage.connection.execute(
            "SELECT name, type, language FROM nodes WHERE file = ? ORDER BY line_start",
            (service_file,),
        ).fetchall()
        assert ("service", "module", "typescript") in rows
        assert ("Widget", "class", "typescript") in rows
        assert ("run", "function", "typescript") in rows
        assert ("boot", "function", "typescript") in rows
        assert result.edges_upserted >= 4

        links = storage.connection.execute(
            """
            SELECT caller.name, callee.name, edge.type
            FROM edges edge
            JOIN nodes caller ON caller.id = edge.from_id
            JOIN nodes callee ON callee.id = edge.to_id
            WHERE caller.file = ?
            """,
            (service_file,),
        ).fetchall()
        assert ("service", "base", "imports") in links
        assert ("Widget", "Base", "inherits") in links
        assert ("run", "render", "calls") in links
        assert ("boot", "render", "calls") in links


def test_tsx_paths_select_typescript_adapter(typescript_repo: Path) -> None:
    with Storage() as storage:
        index_file(storage, typescript_repo / "panel.tsx")
        panel_file = str(typescript_repo / "panel.tsx").replace("\\", "/")
        languages = storage.connection.execute(
            "SELECT DISTINCT language FROM nodes WHERE file = ?", (panel_file,)
        ).fetchall()
        assert languages == [("typescript",)]

    adapter = TypeScriptAdapter()
    assert [
        node.name
        for node in adapter.extract_nodes(
            typescript_repo / "panel.tsx",
            (typescript_repo / "panel.tsx").read_text(encoding="utf-8"),
        )
    ] == ["panel", "Panel"]


def test_reindex_replaces_edges_and_cleans_cross_file_deletions(
    typescript_repo: Path,
) -> None:
    with Storage() as storage:
        base_path = typescript_repo / "base.ts"
        service_path = typescript_repo / "service.ts"
        index_file(storage, base_path)
        index_file(storage, service_path)

        original_count = storage.connection.execute(
            "SELECT count(*) FROM edges"
        ).fetchone()[0]
        reindex_file(storage, service_path)
        replacement_count = storage.connection.execute(
            "SELECT count(*) FROM edges"
        ).fetchone()[0]
        duplicate_count = storage.connection.execute(
            """
            SELECT count(*) FROM (
                SELECT from_id, to_id, type, COUNT(*) AS occurrences
                FROM edges
                GROUP BY from_id, to_id, type
                HAVING occurrences > 1
            )
            """
        ).fetchone()[0]
        assert replacement_count == original_count
        assert duplicate_count == 0

        base_path.write_text(
            '''\
export class Base {
    protected label(): string {
        return "base";
    }
}
''',
            encoding="utf-8",
        )
        result = reindex_file(storage, base_path)
        dangling_count = storage.connection.execute(
            """
            SELECT count(*)
            FROM edges edge
            LEFT JOIN nodes source ON source.id = edge.from_id
            LEFT JOIN nodes target ON target.id = edge.to_id
            WHERE source.id IS NULL OR target.id IS NULL
            """
        ).fetchone()[0]
        assert result.nodes_deleted == 1
        assert dangling_count == 0

        base_path.unlink()
        result = reindex_file(storage, base_path)
        dangling_count = storage.connection.execute(
            """
            SELECT count(*)
            FROM edges edge
            LEFT JOIN nodes source ON source.id = edge.from_id
            LEFT JOIN nodes target ON target.id = edge.to_id
            WHERE source.id IS NULL OR target.id IS NULL
            """
        ).fetchone()[0]
        assert result.nodes_deleted >= 3
        assert dangling_count == 0
