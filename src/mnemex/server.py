"""Phase 4 — MCP server exposing mnemex tools.

Uses ``mcp.server.fastmcp.FastMCP`` (shipped with the ``mcp`` package, which is
a dependency of ``fastmcp==3.4.4``) to expose tools over stdio transport.
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
from mnemex.decision_guard import (
    check_proposed_change as evaluate_proposed_change,
    override_decision_guard as persist_guard_override,
)
from mnemex.evidence import DEFAULT_EVIDENCE_TOKEN_CAP
from mnemex.judge import SemanticJudge
from mnemex.retrieval import Embedder, estimate_tokens, govern_memories, recall
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
    semantic_judge
        Optional, explicitly enabled remote semantic judge. ``None`` keeps the
        server entirely local and returns an advisory unavailable verdict.
    """

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
        embedder: Embedder | None = None,
        semantic_judge: SemanticJudge | None = None,
        max_evidence_tokens: int = DEFAULT_EVIDENCE_TOKEN_CAP,
    ) -> None:
        if max_evidence_tokens <= 0:
            raise ValueError("max_evidence_tokens must be positive")
        self.storage = Storage(db_path)
        self.embedder = embedder
        self.semantic_judge = semantic_judge
        self.max_evidence_tokens = max_evidence_tokens
        self._agents_md_content: str | None = None
        self.mcp = FastMCP("mnemex")
        self._register_tools()

    def _register_tools(self) -> None:
        storage = self.storage
        embedder = self.embedder
        semantic_judge = self.semantic_judge

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
                if embedder is not None and storage.vec_available:
                    from mnemex.retrieval import ensure_embeddings

                    ensure_embeddings(storage, embedder, scopes=(memory.scope,))
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
            # Every retrieval surface is hard-capped; recall is no exception.
            if max_tokens is not None:
                max_tokens = min(max(max_tokens, 0), 800)
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
        def check_proposed_change(
            path: str,
            patch_summary: str,
            scopes: str = "project-shared",
            max_evidence_tokens: int = 800,
            enforce_constraints: bool = False,
        ) -> dict[str, Any]:
            """Check a proposed edit against fresh anchored decisions.

            A change blocks only on a fresh, confidence-qualified semantic
            contradiction. Local mode and every other result are advisory.
            """
            try:
                result = evaluate_proposed_change(
                    storage,
                    path,
                    patch_summary,
                    judge=semantic_judge,
                    scopes=tuple(scope.strip() for scope in scopes.split(",")),
                    max_evidence_tokens=max(
                        0, min(max_evidence_tokens, self.max_evidence_tokens)
                    ),
                    enforce_constraints=enforce_constraints,
                )
                from mnemex.constraints import (
                    enforce_constraints as list_constraint_violations,
                )

                response = result.as_dict()
                response["constraint_violations"] = [
                    {
                        "memory_id": violation.memory_id,
                        "kind": violation.kind,
                        "phrase": violation.phrase,
                        "message": violation.message,
                    }
                    for violation in list_constraint_violations(
                        storage, patch_summary, scopes=tuple(scope.strip() for scope in scopes.split(","))
                    )
                ]
                return response
            except ValueError as e:
                return {"error": str(e)}

        @self.mcp.tool()
        def override_decision_guard(
            run_id: str,
            actor: str,
            reason: str,
        ) -> dict[str, Any]:
            """Record an explicit override for a prior guard result."""
            try:
                override = persist_guard_override(
                    storage, run_id, actor=actor, reason=reason
                )
                return {
                    "run_id": override.guard_run_id,
                    "override_id": override.id,
                    "actor": override.actor,
                    "reason": override.reason,
                    "timestamp": override.timestamp,
                }
            except ValueError as e:
                return {"error": str(e)}

        @self.mcp.tool()
        def reconcile_stale_decision(
            memory_id: str,
            changed_symbol: str,
            diff: str,
        ) -> dict[str, Any]:
            """Classify a stale decision for auditable follow-up."""
            from mnemex.lifecycle import reconcile_stale_decision as reconcile

            status = reconcile(storage, memory_id, changed_symbol, diff)
            return {"memory_id": memory_id, "status": status}

        @self.mcp.tool()
        def export_brain(
            destination: str,
            memory_ids: list[str],
            agents_md: str = "",
            source_commit: str | None = None,
        ) -> dict[str, Any]:
            """Export selected local decision records as a portable bundle."""
            try:
                from mnemex.bundles import export_bundle

                result = export_bundle(
                    storage,
                    destination,
                    memory_ids,
                    agents_md=agents_md,
                    source_commit=source_commit,
                )
                return {"path": str(result.path), "manifest": result.manifest}
            except (OSError, ValueError) as error:
                return {"error": str(error)}

        @self.mcp.tool()
        def import_brain(source: str) -> dict[str, Any]:
            """Import a portable bundle and report immediate anchor freshness."""
            try:
                from mnemex.bundles import import_bundle

                result = import_bundle(storage, source)
                return {
                    "memory_ids": list(result.memory_ids),
                    "id_map": result.id_map,
                    "source_commit": result.source_commit,
                    "agents_md": result.agents_md,
                    "skipped_node_ids": list(result.skipped_node_ids),
                    "freshness": [
                        {
                            "memory_id": report.memory_id,
                            "status": report.status.value,
                            "anchor_node_id": report.anchor_node_id,
                        }
                        for report in result.freshness
                    ],
                }
            except ValueError as error:
                return {"error": str(error)}

        @self.mcp.tool()
        def review_conflicts(
            scopes: str = "project-shared",
        ) -> dict[str, Any]:
            """List derived conflicts among active, in-scope decisions."""
            from mnemex.conflicts import list_conflicts

            result = list_conflicts(
                storage, scopes=tuple(scope.strip() for scope in scopes.split(","))
            )
            return {
                "scopes": list(result.scopes),
                "scanned_decision_ids": list(result.scanned_decision_ids),
                "conflicts": [
                    {
                        "memory_ids": list(conflict.memory_ids),
                        "shared_terms": list(conflict.shared_terms),
                        "shared_tags": list(conflict.shared_tags),
                        "anchor_file": conflict.anchor_file,
                        "anchor_node_id": conflict.anchor_node_id,
                        "context": list(conflict.context),
                    }
                    for conflict in result.conflicts
                ],
            }

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
            max_tokens = min(max(max_tokens, 0), 400)
            filename = Path(path).stem
            try:
                anchored_memories = storage.list_memories_by_anchor_file(
                    path, scope_list
                )
                if anchored_memories:
                    result = govern_memories(
                        anchored_memories,
                        max_tokens=max_tokens,
                        mode="anchor-file",
                    )
                else:
                    result = recall(
                        storage,
                        filename,
                        scopes=scope_list,
                        # This is deliberately filename BM25, not semantic
                        # recall: no file anchor exists to take precedence.
                        embedder=None,
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
            max_tokens = min(max(max_tokens, 0), 800)
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
        def why(
            symbol_or_file: str,
            scopes: str = "project-shared",
        ) -> dict[str, Any]:
            """Explain why a symbol/file is designed this way.

            Returns anchored decisions plus caller context when the optional
            Phase 5 fusion module is available.
            """
            scope_list = [scope.strip() for scope in scopes.split(",")]
            try:
                from mnemex.agents_md import why as build_why

                result = build_why(
                    storage,
                    symbol_or_file,
                    scopes=scope_list,
                    embedder=embedder,
                    max_tokens=400,
                )
                return {
                    "query": result.query,
                    "used_tokens": result.used_tokens,
                    "decisions": [
                        {
                            "id": decision.memory_id,
                            "content": decision.content,
                            "rationale": decision.rationale,
                            "anchor_node_id": decision.anchor_node_id,
                            "freshness": decision.freshness,
                        }
                        for decision in result.decisions
                    ],
                    "callers": [
                        {
                            "id": caller.node_id,
                            "name": caller.name,
                            "file": caller.file,
                            "line_start": caller.line_start,
                            "edge_type": caller.edge_type,
                        }
                        for caller in result.callers
                    ],
                }
            except ImportError:
                return {
                    "error": "agents_md fusion not available",
                    "query": symbol_or_file,
                    "decisions": [],
                    "callers": [],
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

            Uses the optional Phase 5 generator when available and retains the
            prior content to report whether regeneration changed it.
            """
            try:
                from mnemex.agents_md import generate_agents_md as build_agents_md

                result = build_agents_md(
                    storage,
                    previous_content=self._agents_md_content,
                )
                self._agents_md_content = result.content
                return {
                    "content": result.content,
                    "memory_count": result.memory_count,
                    "changed": result.changed,
                }
            except ImportError:
                return {"error": "agents_md generator not available"}
            except ValueError as e:
                return {"error": str(e)}

    def close(self) -> None:
        """Close the underlying storage connection."""
        self.storage.close()


def create_server(
    db_path: str | Path = ":memory:",
    *,
    embedder: Embedder | None = None,
    semantic_judge: SemanticJudge | None = None,
    max_evidence_tokens: int = DEFAULT_EVIDENCE_TOKEN_CAP,
) -> MnemexServer:
    """Factory for creating a configured MnemexServer instance."""
    return MnemexServer(
        db_path,
        embedder=embedder,
        semantic_judge=semantic_judge,
        max_evidence_tokens=max_evidence_tokens,
    )
