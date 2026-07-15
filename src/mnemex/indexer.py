"""Phase 3 — structural backend adapter (pluggable, Path A).

Provides built-in Python and dependency-free TypeScript adapters. Other
backends (tree-sitter, codebase-memory-mcp, LSP) can be plugged in by
implementing the :class:`BackendAdapter` protocol.

This module never modifies the DB schema — it only writes to the existing
``nodes`` and ``edges`` tables via :class:`mnemex.storage.Storage`.
"""

from __future__ import annotations

import ast
import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from mnemex.storage import Node, Storage

__all__ = [
    "BackendAdapter",
    "IndexResult",
    "PythonASTAdapter",
    "TypeScriptAdapter",
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
    call edges (function calls another function in the same file) and
    inheritance edges.
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


_TS_IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$]*"
_TS_CLASS_RE = re.compile(
    rf"(?m)^\s*(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+"
    rf"(?P<name>{_TS_IDENTIFIER})(?:\s+extends\s+(?P<base>{_TS_IDENTIFIER}))?"
)
_TS_FUNCTION_RE = re.compile(
    rf"(?m)^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+"
    rf"(?P<name>{_TS_IDENTIFIER})\s*(?:<[^>]*>)?\s*\("
)
_TS_ARROW_RE = re.compile(
    rf"(?m)^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>{_TS_IDENTIFIER})"
    rf"(?:\s*:[^=;\n]+)?\s*=\s*(?:async\s*)?(?:\([^\n]*?\)|{_TS_IDENTIFIER})\s*=>"
)
_TS_METHOD_RE = re.compile(
    rf"(?m)^\s*(?:(?:public|private|protected|static|async|readonly|"
    rf"override|get|set)\s+)*(?P<name>{_TS_IDENTIFIER})\s*"
    rf"(?:<[^>]*>)?\s*\([^\n;]*\)\s*(?::[^\n{{]+)?\{{"
)
_TS_IMPORT_RE = re.compile(
    r"(?m)^\s*import\s+(?P<bindings>.*?)\s+from\s+[\"'](?P<module>[^\"']+)[\"']\s*;?"
)
_TS_CALL_RE = re.compile(rf"\b(?P<name>{_TS_IDENTIFIER})\s*\(")
_TS_NON_CALL_NAMES = frozenset(
    {"catch", "for", "function", "if", "switch", "while", "with"}
)


class TypeScriptAdapter:
    """Minimal deterministic TypeScript/TSX adapter with no dependencies.

    This intentionally recognizes common declaration and import forms rather
    than attempting to parse the entire language. Relative named imports are
    resolved to deterministic node IDs when their local source is available.
    """

    def extract_nodes(self, path: Path, source: str) -> list[Node]:
        """Extract module, class, and function-like nodes from TS/TSX source."""
        file_str = _file_string(path)
        nodes = [
            Node(
                id=_node_id(file_str, path.stem, 1),
                type="module",
                name=path.stem,
                file=file_str,
                line_start=1,
                content_hash=_content_hash(source),
                language="typescript",
            )
        ]
        declarations: list[tuple[str, str, int, int]] = []
        declarations.extend(
            ("class", match.group("name"), match.start(), match.end())
            for match in _TS_CLASS_RE.finditer(source)
        )
        declarations.extend(
            ("function", match.group("name"), match.start(), match.end())
            for match in _TS_FUNCTION_RE.finditer(source)
        )
        declarations.extend(
            ("function", match.group("name"), match.start(), match.end())
            for match in _TS_ARROW_RE.finditer(source)
        )
        declarations.extend(
            ("function", match.group("name"), match.start(), match.end())
            for match in _TS_METHOD_RE.finditer(source)
            if match.group("name") not in {"constructor", "if", "for", "while"}
        )

        seen: set[tuple[str, int]] = set()
        for node_type, name, start, declaration_end in sorted(
            declarations, key=lambda item: (item[2], item[1])
        ):
            line_start = source.count("\n", 0, start) + 1
            key = (name, line_start)
            if key in seen:
                continue
            seen.add(key)
            segment = _typescript_segment(source, start, declaration_end)
            nodes.append(
                Node(
                    id=_node_id(file_str, name, line_start),
                    type=node_type,
                    name=name,
                    file=file_str,
                    line_start=line_start,
                    content_hash=_content_hash(segment),
                    language="typescript",
                )
            )
        return nodes

    def extract_edges(
        self, path: Path, source: str, nodes: list[Node]
    ) -> list[Edge]:
        """Extract local and relative-import structural edges from TS/TSX."""
        node_by_name = {node.name: node for node in nodes}
        module = next((node for node in nodes if node.type == "module"), None)
        imported_nodes = self._imported_nodes(path, source)
        targets = {**node_by_name, **imported_nodes}
        edges: list[Edge] = []

        if module is not None:
            for match in _TS_IMPORT_RE.finditer(source):
                target_path = _resolve_typescript_import(path, match.group("module"))
                if target_path is None:
                    continue
                target_module = Node(
                    id=_node_id(_file_string(target_path), target_path.stem, 1),
                    type="module",
                    name=target_path.stem,
                    file=_file_string(target_path),
                    line_start=1,
                    content_hash="",
                    language="typescript",
                )
                edges.append(Edge(module.id, target_module.id, "imports"))

        for match in _TS_CLASS_RE.finditer(source):
            child = node_by_name.get(match.group("name"))
            base = targets.get(match.group("base") or "")
            if child is not None and base is not None and child.id != base.id:
                edges.append(Edge(child.id, base.id, "inherits"))

        function_nodes = [node for node in nodes if node.type == "function"]
        for caller in function_nodes:
            start = _offset_for_line(source, caller.line_start)
            end = _typescript_segment_end(source, start)
            for match in _TS_CALL_RE.finditer(source, start, end):
                name = match.group("name")
                callee = targets.get(name)
                if (
                    name not in _TS_NON_CALL_NAMES
                    and callee is not None
                    and callee.id != caller.id
                ):
                    edges.append(Edge(caller.id, callee.id, "calls"))

        return _unique_edges(edges)

    def _imported_nodes(self, path: Path, source: str) -> dict[str, Node]:
        """Map named relative imports to nodes declared in their local source."""
        imported: dict[str, Node] = {}
        for match in _TS_IMPORT_RE.finditer(source):
            target_path = _resolve_typescript_import(path, match.group("module"))
            if target_path is None:
                continue
            try:
                target_source = target_path.read_text(encoding="utf-8")
            except OSError:
                continue
            target_nodes = {
                node.name: node
                for node in self.extract_nodes(target_path, target_source)
            }
            bindings = match.group("bindings").strip()
            named_match = re.search(r"\{(?P<names>[^}]+)\}", bindings)
            if named_match is None:
                continue
            for binding in named_match.group("names").split(","):
                imported_name, _, local_name = binding.strip().partition(" as ")
                imported_node = target_nodes.get(imported_name.strip())
                if imported_node is not None:
                    imported[(local_name or imported_name).strip()] = imported_node
        return imported


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


def _file_string(path: Path) -> str:
    """Return a cross-platform representation used in deterministic IDs."""
    return str(path).replace("\\", "/")


def _typescript_segment(source: str, start: int, declaration_end: int) -> str:
    """Return a declaration's block, or its declaration line when unbraced."""
    brace = source.find("{", declaration_end)
    line_end = source.find("\n", declaration_end)
    if line_end == -1:
        line_end = len(source)
    if brace == -1 or brace > line_end:
        return source[start:line_end]
    return source[start:_typescript_segment_end(source, brace)]


def _typescript_segment_end(source: str, start: int) -> int:
    """Find a balanced brace block end with conservative string handling."""
    brace = source.find("{", start)
    if brace == -1:
        line_end = source.find("\n", start)
        return len(source) if line_end == -1 else line_end

    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(brace, len(source)):
        character = source[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '\"', "`"}:
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return len(source)


def _offset_for_line(source: str, line_start: int) -> int:
    """Return the zero-based offset for a one-based source line."""
    if line_start <= 1:
        return 0
    offset = 0
    for _ in range(line_start - 1):
        newline = source.find("\n", offset)
        if newline == -1:
            return len(source)
        offset = newline + 1
    return offset


def _resolve_typescript_import(path: Path, module: str) -> Path | None:
    """Resolve a local relative TypeScript import without node-style packages."""
    if not module.startswith("."):
        return None
    base = path.parent / module
    candidates = [base]
    if base.suffix not in {".ts", ".tsx"}:
        candidates.extend(
            [
                base.with_suffix(".ts"),
                base.with_suffix(".tsx"),
                base / "index.ts",
                base / "index.tsx",
            ]
        )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _unique_edges(edges: Sequence[Edge]) -> list[Edge]:
    """Return edges in first-seen order without duplicate structural links."""
    seen: set[tuple[str, str, str]] = set()
    unique: list[Edge] = []
    for edge in edges:
        key = (edge.from_id, edge.to_id, edge.type)
        if key not in seen:
            seen.add(key)
            unique.append(edge)
    return unique


def _upsert_edges(storage: Storage, edges: Sequence[Edge]) -> int:
    """Insert unique edges whose endpoint nodes exist in the current graph."""
    count = 0
    with storage.connection:
        for edge in _unique_edges(edges):
            cursor = storage.connection.execute(
                """
                INSERT INTO edges(from_id, to_id, type, confidence)
                SELECT ?, ?, ?, ?
                WHERE EXISTS (SELECT 1 FROM nodes WHERE id = ?)
                  AND EXISTS (SELECT 1 FROM nodes WHERE id = ?)
                  AND NOT EXISTS (
                      SELECT 1 FROM edges
                      WHERE from_id = ? AND to_id = ? AND type = ?
                  )
                """,
                (
                    edge.from_id,
                    edge.to_id,
                    edge.type,
                    edge.confidence,
                    edge.from_id,
                    edge.to_id,
                    edge.from_id,
                    edge.to_id,
                    edge.type,
                ),
            )
            count += cursor.rowcount
    return count


def _delete_edges_from_nodes(storage: Storage, node_ids: Sequence[str]) -> int:
    """Delete all edges emitted by the supplied nodes."""
    if not node_ids:
        return 0
    placeholders = ", ".join("?" for _ in node_ids)
    with storage.connection:
        cursor = storage.connection.execute(
            f"DELETE FROM edges WHERE from_id IN ({placeholders})",
            tuple(node_ids),
        )
    return cursor.rowcount


def _delete_incident_edges(storage: Storage, node_ids: Sequence[str]) -> int:
    """Delete incoming and outgoing edges for nodes about to be removed."""
    if not node_ids:
        return 0
    placeholders = ", ".join("?" for _ in node_ids)
    with storage.connection:
        cursor = storage.connection.execute(
            f"""
            DELETE FROM edges
            WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})
            """,
            (*node_ids, *node_ids),
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


def _default_adapter(path: Path) -> BackendAdapter:
    """Choose the built-in adapter appropriate for a source path."""
    if path.suffix.lower() in {".ts", ".tsx"}:
        return TypeScriptAdapter()
    return PythonASTAdapter()


def _replace_file_graph(
    storage: Storage,
    file: str,
    nodes: Sequence[Node],
    edges: Sequence[Edge],
    *,
    changed_only: bool,
) -> IndexResult:
    """Replace one file's nodes and outgoing edges without dangling links."""
    existing = _get_nodes_for_file(storage, file)
    existing_by_id = {node.id: node for node in existing}
    new_by_id = {node.id: node for node in nodes}
    removed_ids = [node.id for node in existing if node.id not in new_by_id]

    # Source edges must be cleared before old nodes disappear; removed nodes also
    # need their incoming edges cleared because SQLite does not enforce FKs here.
    _delete_edges_from_nodes(storage, [node.id for node in existing])
    _delete_incident_edges(storage, removed_ids)
    for node_id in removed_ids:
        storage.delete_node(node_id)

    upserted = 0
    for node in nodes:
        old = existing_by_id.get(node.id)
        if not changed_only or old is None or old.content_hash != node.content_hash:
            storage.upsert_node(node)
            upserted += 1

    return IndexResult(
        nodes_upserted=upserted,
        nodes_deleted=len(removed_ids),
        edges_upserted=_upsert_edges(storage, edges),
    )


def index_file(
    storage: Storage,
    path: Path | str,
    *,
    adapter: BackendAdapter | None = None,
) -> IndexResult:
    """Index a single file into the structural graph.

    Uses TypeScriptAdapter for .ts/.tsx paths and PythonASTAdapter otherwise.
    Re-indexing an existing path replaces its stale nodes and outgoing edges.
    """
    path = Path(path)
    if adapter is None:
        adapter = _default_adapter(path)

    source = path.read_text(encoding="utf-8")
    nodes = adapter.extract_nodes(path, source)
    edges = adapter.extract_edges(path, source, nodes)
    return _replace_file_graph(
        storage,
        _file_string(path),
        nodes,
        edges,
        changed_only=False,
    )


def index_directory(
    storage: Storage,
    root: Path | str,
    *,
    pattern: str = "**/*",
    adapter: BackendAdapter | None = None,
) -> IndexResult:
    """Index matching Python, TypeScript, and TSX files under a directory tree."""
    root = Path(root)

    total_nodes = 0
    total_deleted = 0
    total_edges = 0
    ignored_directories = {
        ".git", ".mnemex", ".venv", "__pycache__", "node_modules", "venv"
    }
    for path in sorted(root.glob(pattern)):
        if any(part in ignored_directories for part in path.parts):
            continue
        if path.is_file() and (
            adapter is not None or path.suffix.lower() in {".py", ".ts", ".tsx"}
        ):
            result = index_file(storage, path, adapter=adapter)
            total_nodes += result.nodes_upserted
            total_deleted += result.nodes_deleted
            total_edges += result.edges_upserted

    return IndexResult(
        nodes_upserted=total_nodes,
        nodes_deleted=total_deleted,
        edges_upserted=total_edges,
    )


def reindex_file(
    storage: Storage,
    path: Path | str,
    *,
    adapter: BackendAdapter | None = None,
) -> IndexResult:
    """Incrementally re-index a single file.

    Only updates nodes whose content_hash changed. Deletes symbols that no
    longer exist, including all incoming/outgoing edges for deleted nodes.
    A missing path is treated as a deleted indexed file.
    """
    path = Path(path)
    if adapter is None:
        adapter = _default_adapter(path)

    file_str = _file_string(path)
    if path.is_file():
        source = path.read_text(encoding="utf-8")
        new_nodes = adapter.extract_nodes(path, source)
        new_edges = adapter.extract_edges(path, source, new_nodes)
    else:
        new_nodes = []
        new_edges = []
    return _replace_file_graph(
        storage,
        file_str,
        new_nodes,
        new_edges,
        changed_only=True,
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
