"""Phase 4 — MCP server exposing mnemex tools.

Uses ``mcp.server.fastmcp.FastMCP`` (shipped with the ``mcp`` package, which is
a dependency of ``fastmcp==3.4.4``) to expose the 10 tools over stdio transport.
The server is stateful: it opens a single Storage connection at startup and
reuses it for all tool calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from mnemex.anchors import (
    Anchor,
    AmbiguousAnchorError,
    AnchorNotFoundError,
    check_freshness,
    forget,
    remember,
)
from mnemex.retrieval import Embedder, estimate_tokens, recall
from mnemex.storage import Storage

__all__ = ["create_server", "MnemexServer"]


class MnemexServer:
    """Wraps Storage + FastMCP into a runnable MCP server.

    Parameters
    ----------
    db_path
        Path to the SQLite database file.  Use ``":memory:"`` for testing.
    embedder
        Optional embedding function for hybrid retrieval.  When ``None``,
        the server operates in BM25-only mode.
    """

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
        embedder: Embedder | None = None,
    ) -> None:
        self.storage = Storage(db_path)
        self.embedder = embedder
        self.mcp = FastMCP("mnemex")
        self._register_tools()

    def _register_tools(self) -> None:
        storage = self.storage
        embedder = self.embedder

        @self.mcp.tool()
        def remember_decision(
            content: str,
            anchor_file: str | None = None,
            anchor_symbol: str | None = None,
            anchor_node_id: str | None = None,
            scope: str = "project-shared",
            rationale: str = "",
            tags: str = "",
        ) -> dict[str, Any]:
            """Store a decision or convention, optionally anchored to a code symbol."""
            anchor: Anchor | str | None = None
            if anchor_node_id:
                anchor = anchor_node_id
            elif anchor_file and anchor_symbol:
                anchor = Anchor(file=anchor_file, symbol=anchor_symbol)

            try:
                memory = remember(
                    storage,
                    content,
                    anchor=anchor,
                    scope=scope,
                    rationale=rationale,
                    tags=tags,
                )
                return {"memory_id": memory.id, "status": "stored"}
            except (AnchorNotFoundError, AmbiguousAnchorError, ValueError) as e:
                return {"error": str(e)}

        @self.mcp.tool()
        def recall_memories(
            query: str,
            scopes: str = "project-shared",
            limit: int = 10,
            max_tokens: int | None = None,
        ) -> dict[str, Any]:
            """Retrieve relevant memories via hybrid BM25+vector search."""
            scope_list = [s.strip() for s in scopes.split(",")]
            try:
                result = recall(
                    storage,
                    query,
                    scopes=scope_list,
                    embedder=embedder,
                    limit=limit,
                    max_tokens=max_tokens,
                )
                return {
                    "mode": result.mode,
                    "used_tokens": result.used_tokens,
                    "budget_tokens": result.budget_tokens,
                    "included": [
                        {
                            "id": sm.memory.id,
                            "content": sm.memory.content,
                            "rationale": sm.memory.rationale,
                            "score": sm.score,
                            "signals": list(sm.signals),
                        }
                        for sm in result.included
                    ],
                    "dropped_count": len(result.dropped),
                }
            except ValueError as e:
                return {"error": str(e)}

        @self.mcp.tool()
        def forget_memory(memory_id: str) -> dict[str, Any]:
            """Remove a memory by its ID."""
            deleted = forget(storage, memory_id)
            return {"deleted": deleted, "memory_id": memory_id}

        @self.mcp.tool()
        def check_memory_freshness(
            scopes: str = "project-shared",
            memory_id: str | None = None,
        ) -> dict[str, Any]:
            """Check whether anchored memories are fresh, stale, or orphaned."""
            scope_list = [s.strip() for s in scopes.split(",")]
            try:
                reports = check_freshness(
                    storage, scopes=scope_list, memory_id=memory_id
                )
                return {
                    "reports": [
                        {
                            "memory_id": r.memory_id,
                            "status": r.status.value,
                            "anchor_node_id": r.anchor_node_id,
                            "stored_hash": r.stored_hash,
                            "current_hash": r.current_hash,
                        }
                        for r in reports
                    ]
                }
            except ValueError as e:
                return {"error": str(e)}

        @self.mcp.tool()
        def context_for(
            path: str,
            scopes: str = "project-shared",
            max_tokens: int = 400,
        ) -> dict[str, Any]:
            """JIT context for a file about to be edited (<=400 tokens).

            Returns anchored memories relevant to the given path.
            """
            scope_list = [s.strip() for s in scopes.split(",")]
            # Query using the filename as the search term
            filename = Path(path).stem
            try:
                result = recall(
                    storage,
                    filename,
                    scopes=scope_list,
                    embedder=embedder,
                    limit=10,
                    max_tokens=max_tokens,
                )
                return {
                    "path": path,
                    "mode": result.mode,
                    "used_tokens": result.used_tokens,
                    "budget_tokens": result.budget_tokens,
                    "memories": [
                        {
                            "id": sm.memory.id,
                            "content": sm.memory.content,
                            "anchor_node_id": sm.memory.anchor_node_id,
                        }
                        for sm in result.included
                    ],
                }
            except ValueError as e:
                return {"error": str(e)}

        @self.mcp.tool()
        def get_context_brief(
            scopes: str = "project-shared",
            max_tokens: int = 800,
        ) -> dict[str, Any]:
            """Session-start brief of the most relevant recent memories (<=800 tokens).

            Returns a ranked summary of the project's key decisions and conventions.
            """
            scope_list = [s.strip() for s in scopes.split(",")]
            try:
                # Use a broad query to get the most important memories
                memories = storage.list_memories(scope_list)
                if not memories:
                    return {
                        "brief": "",
                        "used_tokens": 0,
                        "budget_tokens": max_tokens,
                    }

                # Rank by importance/recency, truncate to budget
                included = []
                used = 0
                for mem in sorted(
                    memories,
                    key=lambda m: (-m.importance, m.created_at),
                ):
                    combined = mem.content
                    if mem.rationale:
                        combined = f"{mem.content}\n{mem.rationale}"
                    cost = estimate_tokens(combined)
                    if used + cost <= max_tokens:
                        included.append(mem)
                        used += cost
                    if used >= max_tokens:
                        break

                brief_lines = []
                for mem in included:
                    line = f"- {mem.content}"
                    if mem.anchor_node_id:
                        line += f" [anchored: {mem.anchor_node_id}]"
                    brief_lines.append(line)

                return {
                    "brief": "\n".join(brief_lines),
                    "used_tokens": used,
                    "budget_tokens": max_tokens,
                    "memory_count": len(included),
                }
            except ValueError as e:
                return {"error": str(e)}

        @self.mcp.tool()
        def why(symbol_or_file: str) -> dict[str, Any]:
            """Explain why a symbol/file is designed this way.

            Returns the anchored decisions plus caller context. Currently uses
            context_for as the implementation until Phase 5 adds the full
            fusion engine.
            """
            # Phase 5 will add full call-graph fusion; for now delegate to
            # context_for with the symbol as query.
            scope_list = ["project-shared"]
            try:
                result = recall(
                    storage,
                    symbol_or_file,
                    scopes=scope_list,
                    embedder=embedder,
                    limit=10,
                    max_tokens=400,
                )
                return {
                    "query": symbol_or_file,
                    "mode": result.mode,
                    "decisions": [
                        {
                            "id": sm.memory.id,
                            "content": sm.memory.content,
                            "rationale": sm.memory.rationale,
                            "anchor_node_id": sm.memory.anchor_node_id,
                        }
                        for sm in result.included
                    ],
                    "callers": [],  # Phase 5 placeholder
                }
            except ValueError as e:
                return {"error": str(e)}

        @self.mcp.tool()
        def trace_callers_tool(node_id: str) -> dict[str, Any]:
            """Trace which nodes call/reference the given node.

            Requires the indexer (Phase 3) to have populated the edges table.
            """
            try:
                from mnemex.indexer import trace_callers as _trace

                callers = _trace(storage, node_id)
                return {
                    "node_id": node_id,
                    "callers": [
                        {
                            "id": node.id,
                            "name": node.name,
                            "file": node.file,
                            "line_start": node.line_start,
                            "edge_type": edge_type,
                        }
                        for node, edge_type in callers
                    ],
                }
            except ImportError:
                return {"node_id": node_id, "callers": [], "error": "indexer not available"}

        @self.mcp.tool()
        def index_path(
            path: str,
            pattern: str = "**/*.py",
        ) -> dict[str, Any]:
            """Index a file or directory into the structural graph."""
            try:
                from mnemex.indexer import index_directory, index_file

                p = Path(path)
                if p.is_file():
                    result = index_file(storage, p)
                elif p.is_dir():
                    result = index_directory(storage, p, pattern=pattern)
                else:
                    return {"error": f"path not found: {path}"}
                return {
                    "nodes_upserted": result.nodes_upserted,
                    "nodes_deleted": result.nodes_deleted,
                    "edges_upserted": result.edges_upserted,
                }
            except ImportError:
                return {"error": "indexer not available"}

        @self.mcp.tool()
        def generate_agents_md() -> dict[str, Any]:
            """Generate AGENTS.md content from the current memory state.

            Full implementation in Phase 5; returns a basic structure for now.
            """
            try:
                memories = storage.list_memories(["project-shared"])
                sections = []
                for mem in memories:
                    if mem.anchor_node_id:
                        sections.append(
                            f"- {mem.content} (anchor: {mem.anchor_node_id})"
                        )
                    else:
                        sections.append(f"- {mem.content}")
                content = (
                    "# Project Memory (auto-generated)\n\n"
                    + "\n".join(sections)
                    if sections
                    else "# Project Memory (auto-generated)\n\nNo memories stored yet."
                )
                return {"content": content, "memory_count": len(memories)}
            except ValueError as e:
                return {"error": str(e)}

    def close(self) -> None:
        """Close the underlying storage connection."""
        self.storage.close()


def create_server(
    db_path: str | Path = ":memory:",
    *,
    embedder: Embedder | None = None,
) -> MnemexServer:
    """Factory for creating a configured MnemexServer instance."""
    return MnemexServer(db_path, embedder=embedder)
