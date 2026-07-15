"""Phase 5 — ``why()`` fusion, self-updating AGENTS.md, git-diff staleness watcher.

This module ties together the structural graph (indexer) and episodic memory
(anchors + retrieval) into three user-facing capabilities:

1. ``why(symbol_or_file)`` — returns the anchored decision(s) PLUS surrounding
   call-graph context in a single response.
2. ``generate_agents_md()`` — produces AGENTS.md content, regenerating only
   sections whose anchors changed (idempotent when nothing changed).
3. ``check_staleness_from_diff(diff_text)`` — parses a git-diff and reports which
   anchored memories are now stale because their anchored symbol changed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from mnemex.anchors import check_freshness, FreshnessReport, FreshnessStatus
from mnemex.indexer import trace_callers
from mnemex.retrieval import Embedder, ScoredMemory, estimate_tokens, recall
from mnemex.storage import Memory, Storage

__all__ = [
    "WhyResult",
    "AgentsMdResult",
    "StalenessWatchResult",
    "why",
    "generate_agents_md",
    "check_staleness_from_diff",
]


@dataclass(frozen=True, slots=True)
class CallerInfo:
    """A node that calls/references the queried symbol."""

    node_id: str
    name: str
    file: str
    line_start: int
    edge_type: str


@dataclass(frozen=True, slots=True)
class DecisionInfo:
    """A memory (decision/convention) relevant to the query."""

    memory_id: str
    content: str
    rationale: str
    anchor_node_id: str | None
    freshness: str  # "fresh" | "stale" | "orphaned" | "unanchored"


@dataclass(frozen=True, slots=True)
class WhyResult:
    """Full fusion result: decisions + call-graph context."""

    query: str
    decisions: tuple[DecisionInfo, ...]
    callers: tuple[CallerInfo, ...]
    used_tokens: int


@dataclass(frozen=True, slots=True)
class AgentsMdResult:
    """Result of AGENTS.md generation."""

    content: str
    memory_count: int
    changed: bool  # True if content differs from previous


@dataclass(frozen=True, slots=True)
class StalenessWatchResult:
    """Result of checking staleness from a git diff."""

    stale_memories: tuple[FreshnessReport, ...]
    files_affected: tuple[str, ...]


def why(
    storage: Storage,
    symbol_or_file: str,
    *,
    scopes: Sequence[str] = ("project-shared",),
    embedder: Embedder | None = None,
    max_tokens: int = 400,
) -> WhyResult:
    """Explain why a symbol or file is designed this way.

    Fuses:
    1. Anchored decisions relevant to the query (via recall)
    2. The call-graph context (who calls it / what it calls) from the indexer

    Returns both in a single structured response under the token budget.
    """
    scope_list = list(scopes)

    # Resolve matching structural nodes first. An anchor match is authoritative:
    # `why authenticate` must find decisions anchored to authenticate even when
    # the decision text itself does not repeat the symbol name.
    query_nodes = storage.connection.execute(
        "SELECT id FROM nodes WHERE name = ? OR file LIKE ?",
        (symbol_or_file, f"%{symbol_or_file}%"),
    ).fetchall()
    query_node_ids = {node_id for (node_id,) in query_nodes}

    # 1. Retrieve relevant memories
    result = recall(
        storage,
        symbol_or_file,
        scopes=scope_list,
        embedder=embedder,
        limit=10,
        max_tokens=max_tokens,
    )

    # 2. Build freshness map for included memories
    freshness_map: dict[str, str] = {}
    try:
        reports = check_freshness(storage, scopes=scope_list)
        for report in reports:
            freshness_map[report.memory_id] = report.status.value
    except ValueError:
        pass

    included = list(result.included)
    included_ids = {item.memory.id for item in included}
    used_tokens = result.used_tokens
    for memory in storage.list_memories(scope_list):
        if memory.id in included_ids or memory.anchor_node_id not in query_node_ids:
            continue
        cost = estimate_tokens(memory.content + "\n" + memory.rationale)
        if used_tokens + cost > max_tokens:
            continue
        included.append(
            ScoredMemory(memory, 1.0, len(included) + 1, ("anchor",))
        )
        included_ids.add(memory.id)
        used_tokens += cost

    decisions = tuple(
        DecisionInfo(
            memory_id=sm.memory.id,
            content=sm.memory.content,
            rationale=sm.memory.rationale,
            anchor_node_id=sm.memory.anchor_node_id,
            freshness=freshness_map.get(sm.memory.id, "unanchored"),
        )
        for sm in included
    )

    # 3. Gather callers from the structural graph
    callers_list: list[CallerInfo] = []
    seen_nodes: set[str] = set()

    for (node_id,) in query_nodes:
        if node_id in seen_nodes:
            continue
        seen_nodes.add(node_id)
        for caller_node, edge_type in trace_callers(storage, node_id):
            callers_list.append(CallerInfo(
                node_id=caller_node.id,
                name=caller_node.name,
                file=caller_node.file,
                line_start=caller_node.line_start,
                edge_type=edge_type,
            ))

    return WhyResult(
        query=symbol_or_file,
        decisions=decisions,
        callers=tuple(callers_list),
        used_tokens=used_tokens,
    )


def generate_agents_md(
    storage: Storage,
    *,
    scopes: Sequence[str] = ("project-shared",),
    previous_content: str | None = None,
) -> AgentsMdResult:
    """Generate AGENTS.md content from the current memory state.

    Only regenerates sections whose anchors changed. When ``previous_content``
    is provided and nothing has changed, returns ``changed=False`` with the same
    content (idempotent).
    """
    scope_list = list(scopes)
    memories = storage.list_memories(scope_list)

    if not memories:
        content = _AGENTS_MD_HEADER + "\nNo decisions recorded yet.\n"
        return AgentsMdResult(
            content=content,
            memory_count=0,
            changed=content != previous_content,
        )

    # Get freshness for all memories
    reports = check_freshness(storage, scopes=scope_list)
    freshness_map = {r.memory_id: r.status for r in reports}

    # Group memories by anchor file (or "global" for unanchored)
    sections: dict[str, list[Memory]] = {}
    for mem in memories:
        if mem.anchor_node_id:
            node = storage.get_node(mem.anchor_node_id)
            key = node.file if node else "(orphaned)"
        else:
            key = "(global)"
        sections.setdefault(key, []).append(mem)

    # Build content
    lines = [_AGENTS_MD_HEADER]

    for section_key in sorted(sections):
        lines.append(f"\n## {section_key}\n")
        for mem in sections[section_key]:
            status = freshness_map.get(mem.id, FreshnessStatus.UNANCHORED)
            status_str = status.value if isinstance(status, FreshnessStatus) else str(status)
            marker = ""
            if status_str == "stale":
                marker = " ⚠️ STALE"
            elif status_str == "orphaned":
                marker = " ❌ ORPHANED"

            line = f"- {mem.content}{marker}"
            if mem.rationale:
                line += f"\n  _Rationale: {mem.rationale}_"
            lines.append(line)

    content = "\n".join(lines) + "\n"
    return AgentsMdResult(
        content=content,
        memory_count=len(memories),
        changed=content != previous_content,
    )


_AGENTS_MD_HEADER = """# Project Memory (auto-generated by mnemex)

> Decisions and conventions anchored to the codebase.
> Regenerate with `generate_agents_md()`. Stale entries are flagged.
"""


# Git-diff staleness watcher
_DIFF_FILE_RE = re.compile(r"^(?:---|\+\+\+)\s+[ab]/(.+)$", re.MULTILINE)


def check_staleness_from_diff(
    storage: Storage,
    diff_text: str,
    *,
    scopes: Sequence[str] = ("project-shared",),
) -> StalenessWatchResult:
    """Parse a git diff and report which anchored memories are now stale.

    Extracts filenames from the diff, finds anchored memories whose
    anchor_node_id points to a node in one of those files, and runs
    freshness checks on them.
    """
    scope_list = list(scopes)

    # Extract affected files from the diff
    files = set(_DIFF_FILE_RE.findall(diff_text))
    # Normalize: remove /dev/null
    files.discard("/dev/null")
    files_tuple = tuple(sorted(files))

    if not files_tuple:
        return StalenessWatchResult(stale_memories=(), files_affected=())

    # Find nodes in affected files
    affected_node_ids: set[str] = set()
    for file_path in files_tuple:
        rows = storage.connection.execute(
            "SELECT id FROM nodes WHERE file = ? OR file LIKE ?",
            (file_path, f"%{file_path}"),
        ).fetchall()
        for (node_id,) in rows:
            affected_node_ids.add(node_id)

    if not affected_node_ids:
        return StalenessWatchResult(
            stale_memories=(), files_affected=files_tuple
        )

    # Check freshness of memories anchored to affected nodes
    all_reports = check_freshness(storage, scopes=scope_list)
    stale = tuple(
        report
        for report in all_reports
        if (
            report.anchor_node_id in affected_node_ids
            and report.status in (FreshnessStatus.STALE, FreshnessStatus.ORPHANED)
        )
    )

    return StalenessWatchResult(
        stale_memories=stale, files_affected=files_tuple
    )
