"""Phase 3 — structural backend adapter (pluggable, Path A).

Provides a minimal built-in Python AST adapter using only the stdlib ``ast``
module. Other backends (tree-sitter, codebase-memory-mcp, LSP) can be plugged
in by implementing the :class:`BackendAdapter` protocol.

This module never modifies the DB schema — it only writes to the existing
``nodes`` and ``edges`` tables via :class:`mnemex.storage.Storage`.
"""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from mnemex.storage import Node, Storage

__all__ = [
    "BackendAdapter",
    "IndexResult",
    "PythonASTAdapter",
    "index_file",
    "index_directory",
    "reindex_file",
    "trace_callers",
]


@dataclass(frozen=True, slots=True)
class Edge:
    """A structural edge between two nodes."""

    from_id: str
    to_id: str
    type: str
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class IndexResult:
    """Summary of an indexing operation."""

    nodes_upserted: int
    nodes_deleted: int
    edges_upserted: int


@runtime_checkable
class BackendAdapter(Protocol):
    """Protocol for pluggable structural backends.

    Implementations parse source files and return nodes + edges that the
    indexer writes into Storage.
    """

    def extract_nodes(self, path: Path, source: str) -> list[Node]: ...
    def extract_edges(self, path: Path, source: str, nodes: list[Node]) -> list[Edge]: ...


def _content_hash(source: str) -> str:
    """SHA-256 hex digest of source text."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _node_id(file: str, name: str, line_start: int) -> str:
    """Deterministic node ID from file + name + line."""
    return hashlib.sha256(
        f"{file}:{name}:{line_start}".encode("utf-8")
    ).hexdigest()[:16]


class PythonASTAdapter:
    """Built-in adapter that parses Python files using stdlib ``ast``.

    Extracts modules, classes, and functions/methods as nodes. Extracts
    call edges (function calls another function in the same file),
    inheritance edges, and import references.
    """

    def extract_nodes(self, path: Path, source: str) -> list[Node]:
        """Extract all function/class/module nodes from a Python file."""
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            return []

        file_str = str(path).replace("\\", "/")
        nodes: list[Node] = []

        # Module node
        module_hash = _content_hash(source)
        nodes.append(Node(
            id=_node_id(file_str, path.stem, 1),
            type="module",
            name=path.stem,
            file=file_str,
            line_start=1,
            content_hash=module_hash,
            language="python",
        ))

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                segment = _get_segment(source, node)
                nodes.append(Node(
                    id=_node_id(file_str, node.name, node.lineno),
                    type="function",
                    name=node.name,
                    file=file_str,
                    line_start=node.lineno,
                    content_hash=_content_hash(segment),
                    language="python",
                ))
            elif isinstance(node, ast.ClassDef):
                segment = _get_segment(source, node)
                nodes.append(Node(
                    id=_node_id(file_str, node.name, node.lineno),
                    type="class",
                    name=node.name,
                    file=file_str,
                    line_start=node.lineno,
                    content_hash=_content_hash(segment),
                    language="python",
                ))

        return nodes

    def extract_edges(
        self, path: Path, source: str, nodes: list[Node]
    ) -> list[Edge]:
        """Extract structural edges from a Python file."""
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            return []

        str(path).replace("\\", "/")
        edges: list[Edge] = []
        node_by_name: dict[str, Node] = {n.name: n for n in nodes}

        for ast_node in ast.walk(tree):
            # Inheritance edges
            if isinstance(ast_node, ast.ClassDef):
                child = node_by_name.get(ast_node.name)
                if child is None:
                    continue
                for base in ast_node.bases:
                    base_name = _get_name(base)
                    if base_name and base_name in node_by_name:
                        edges.append(Edge(
                            from_id=child.id,
                            to_id=node_by_name[base_name].id,
                            type="inherits",
                        ))

            # Call edges: function/method containing a call to another known symbol
            if isinstance(
                ast_node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                caller = node_by_name.get(ast_node.name)
                if caller is None:
                    continue
                for child in ast.walk(ast_node):
                    if isinstance(child, ast.Call):
                        callee_name = _get_call_name(child)
                        if (
                            callee_name
                            and callee_name in node_by_name
                            and callee_name != ast_node.name
                        ):
                            edges.append(Edge(
                                from_id=caller.id,
                                to_id=node_by_name[callee_name].id,
                                type="calls",
                            ))

        # Deduplicate edges
        seen: set[tuple[str, str, str]] = set()
        unique: list[Edge] = []
        for edge in edges:
            key = (edge.from_id, edge.to_id, edge.type)
            if key not in seen:
                seen.add(key)
                unique.append(edge)
        return unique


def _get_segment(source: str, node: ast.AST) -> str:
    """Get the source text for an AST node via line slicing."""
    lines = source.splitlines(keepends=True)
    start = node.lineno - 1  # type: ignore[attr-defined]
    end = node.end_lineno  # type: ignore[attr-defined]
    if end is None:
        end = start + 1
    return "".join(lines[start:end])


def _get_name(node: ast.expr) -> str | None:
    """Extract a simple name from an AST expression node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _get_call_name(call: ast.Call) -> str | None:
    """Extract the function name from a Call node."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _upsert_edges(storage: Storage, edges: Sequence[Edge]) -> int:
    """Insert edges into the edges table, replacing duplicates."""
    count = 0
    with storage.connection:
        for edge in edges:
            storage.connection.execute(
                """
                INSERT INTO edges(from_id, to_id, type, confidence)
                VALUES (?, ?, ?, ?)
                """,
                (edge.from_id, edge.to_id, edge.type, edge.confidence),
            )
            count += 1
    return count


def _delete_edges_for_file(storage: Storage, file: str) -> int:
    """Delete all edges originating from nodes in a given file."""
    with storage.connection:
        cursor = storage.connection.execute(
            """
            DELETE FROM edges
            WHERE from_id IN (SELECT id FROM nodes WHERE file = ?)
            """,
            (file,),
        )
    return cursor.rowcount


def _get_nodes_for_file(storage: Storage, file: str) -> list[Node]:
    """Get all nodes for a given file."""
    rows = storage.connection.execute(
        """
        SELECT id, type, name, file, line_start, content_hash, language
        FROM nodes
        WHERE file = ?
        ORDER BY line_start, id
        """,
        (file,),
    ).fetchall()
    return [Node(*row) for row in rows]


def index_file(
    storage: Storage,
    path: Path | str,
    *,
    adapter: BackendAdapter | None = None,
) -> IndexResult:
    """Index a single file into the structural graph.

    Uses the PythonASTAdapter by default for .py files. Returns an IndexResult
    summarizing what was written.
    """
    path = Path(path)
    if adapter is None:
        adapter = PythonASTAdapter()

    source = path.read_text(encoding="utf-8")
    nodes = adapter.extract_nodes(path, source)
    edges = adapter.extract_edges(path, source, nodes)

    for node in nodes:
        storage.upsert_node(node)

    edge_count = _upsert_edges(storage, edges)

    return IndexResult(
        nodes_upserted=len(nodes),
        nodes_deleted=0,
        edges_upserted=edge_count,
    )


def index_directory(
    storage: Storage,
    root: Path | str,
    *,
    pattern: str = "**/*.py",
    adapter: BackendAdapter | None = None,
) -> IndexResult:
    """Index all matching files under a directory tree."""
    root = Path(root)
    if adapter is None:
        adapter = PythonASTAdapter()

    total_nodes = 0
    total_edges = 0
    for path in sorted(root.glob(pattern)):
        if path.is_file():
            result = index_file(storage, path, adapter=adapter)
            total_nodes += result.nodes_upserted
            total_edges += result.edges_upserted

    return IndexResult(
        nodes_upserted=total_nodes,
        nodes_deleted=0,
        edges_upserted=total_edges,
    )


def reindex_file(
    storage: Storage,
    path: Path | str,
    *,
    adapter: BackendAdapter | None = None,
) -> IndexResult:
    """Incrementally re-index a single file.

    Only updates nodes whose content_hash changed. Deletes nodes for symbols
    that no longer exist in the file. Replaces all edges from this file.
    """
    path = Path(path)
    if adapter is None:
        adapter = PythonASTAdapter()

    file_str = str(path).replace("\\", "/")
    source = path.read_text(encoding="utf-8")
    new_nodes = adapter.extract_nodes(path, source)
    new_edges = adapter.extract_edges(path, source, new_nodes)

    existing = _get_nodes_for_file(storage, file_str)
    existing_by_id = {n.id: n for n in existing}
    new_by_id = {n.id: n for n in new_nodes}

    # Delete nodes that no longer exist
    deleted = 0
    for node_id in existing_by_id:
        if node_id not in new_by_id:
            storage.delete_node(node_id)
            deleted += 1

    # Upsert only changed or new nodes
    upserted = 0
    for node in new_nodes:
        old = existing_by_id.get(node.id)
        if old is None or old.content_hash != node.content_hash:
            storage.upsert_node(node)
            upserted += 1

    # Replace all edges from this file
    _delete_edges_for_file(storage, file_str)
    edge_count = _upsert_edges(storage, new_edges)

    return IndexResult(
        nodes_upserted=upserted,
        nodes_deleted=deleted,
        edges_upserted=edge_count,
    )


def trace_callers(storage: Storage, node_id: str) -> list[tuple[Node, str]]:
    """Return all nodes that have an edge pointing TO the given node.

    Returns a list of ``(caller_node, edge_type)`` tuples, ordered by
    caller file then line_start.
    """
    rows = storage.connection.execute(
        """
        SELECT n.id, n.type, n.name, n.file, n.line_start,
               n.content_hash, n.language, e.type
        FROM edges e
        JOIN nodes n ON n.id = e.from_id
        WHERE e.to_id = ?
        ORDER BY n.file, n.line_start, n.id
        """,
        (node_id,),
    ).fetchall()
    return [(Node(*row[:7]), row[7]) for row in rows]
