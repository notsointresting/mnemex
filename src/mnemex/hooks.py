"""Phase 4 — JIT hook injection (SessionStart, PreToolUse, Stop).

Hooks deliver anchored context just-in-time without requiring the agent to
explicitly call a tool. They degrade gracefully when the agent runtime does not
support hooks (the same logic is available through the equivalent MCP tools).

Token caps are hard limits enforced via the retrieval module's governor:
- SessionStart: <=800 tokens
- PreToolUse (JIT ``context_for``): <=400 tokens
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from mnemex.retrieval import Embedder, estimate_tokens, govern_memories, recall
from mnemex.security import RedactionLog, sanitize
from mnemex.storage import Memory, Storage

__all__ = [
    "HookResult",
    "session_start",
    "pre_tool_use",
    "stop_capture",
    "StopDecisionSuggestion",
    "suggest_stop_decision",
    "confirm_stop_suggestion",
    "SESSION_TOKEN_CAP",
    "JIT_TOKEN_CAP",
]

SESSION_TOKEN_CAP = 800
JIT_TOKEN_CAP = 400


@dataclass(frozen=True, slots=True)
class HookResult:
    """Result of a hook invocation, ready for injection into agent context."""

    content: str
    used_tokens: int
    budget_tokens: int
    memory_count: int
    mode: str


@dataclass(frozen=True, slots=True)
class StopDecisionSuggestion:
    """A non-persistent candidate captured from a Stop-hook transcript."""

    content: str
    origin: str
    requires_confirmation: bool = True


def session_start(
    storage: Storage,
    *,
    scopes: Sequence[str] = ("project-shared",),
    embedder: Embedder | None = None,
    max_tokens: int = SESSION_TOKEN_CAP,
) -> HookResult:
    """SessionStart hook: produce a brief of the most important memories.

    Respects the hard 800-token cap. Returns the highest-importance memories
    ranked by the token governor, formatted as a concise brief.
    """
    cap = min(max(max_tokens, 0), SESSION_TOKEN_CAP)
    memories = storage.list_memories(scopes)
    if not memories:
        return HookResult(
            content="",
            used_tokens=0,
            budget_tokens=cap,
            memory_count=0,
            mode="bm25-only" if embedder is None else "hybrid",
        )

    # Rank by importance descending, then recency
    ranked = sorted(
        memories,
        key=lambda m: (-m.importance, m.created_at),
    )

    included: list[Memory] = []
    used = 0
    for mem in ranked:
        combined = mem.content
        if mem.rationale:
            combined = f"{mem.content}\n{mem.rationale}"
        cost = estimate_tokens(combined)
        if used + cost <= cap:
            included.append(mem)
            used += cost

    lines = _format_brief(included)
    return HookResult(
        content="\n".join(lines),
        used_tokens=used,
        budget_tokens=cap,
        memory_count=len(included),
        mode="bm25-only" if embedder is None else "hybrid",
    )


def pre_tool_use(
    storage: Storage,
    path: str,
    *,
    scopes: Sequence[str] = ("project-shared",),
    embedder: Embedder | None = None,
    max_tokens: int = JIT_TOKEN_CAP,
) -> HookResult:
    """PreToolUse hook: inject context for the file about to be edited.

    Respects the hard 400-token cap. Uses the file stem as a recall query to
    find anchored memories relevant to this specific file.
    """
    cap = min(max(max_tokens, 0), JIT_TOKEN_CAP)
    filename = Path(path).stem

    anchored_memories = storage.list_memories_by_anchor_file(path, scopes)
    if anchored_memories:
        result = govern_memories(
            anchored_memories,
            max_tokens=cap,
            mode="anchor-file",
        )
    else:
        result = recall(
            storage,
            filename,
            scopes=list(scopes),
            embedder=embedder,
            limit=10,
            max_tokens=cap,
        )

    lines: list[str] = []
    used_tokens = 0
    for sm in result.included:
        line = f"- {sm.memory.content}"
        if sm.memory.anchor_node_id:
            line += f" [anchor: {sm.memory.anchor_node_id}]"
        candidate = "\n".join([*lines, line])
        candidate_tokens = estimate_tokens(candidate)
        if candidate_tokens <= cap:
            lines.append(line)
            used_tokens = candidate_tokens

    return HookResult(
        content="\n".join(lines),
        used_tokens=used_tokens,
        budget_tokens=cap,
        memory_count=len(lines),
        mode=result.mode,
    )


def stop_capture(
    storage: Storage,
    content: str,
    *,
    scope: str = "project-shared",
    source: str = "agent",
) -> str | None:
    """Stop hook: capture a decision or note from the agent's completed action.

    This is a simple capture path; the agent can call ``remember`` directly for
    richer anchoring. Returns the memory_id if captured, None if content is
    empty.
    """
    if not content or not content.strip():
        return None

    from mnemex.anchors import remember

    redactions = RedactionLog()
    clean_content = sanitize(
        content.strip(), field_name="content", log=redactions
    )
    memory = remember(
        storage,
        clean_content,
        scope=scope,
        source=source,
        redaction_log=redactions,
    )
    return memory.id


def suggest_stop_decision(
    completed_action: str,
    *,
    extractor: Callable[[str], str | None] | None = None,
    origin: str | None = None,
) -> StopDecisionSuggestion | None:
    """Extract a candidate decision without persisting it.

    ``extractor`` may be backed by a heuristic or an external model, but its
    output is only a suggestion.  Call :func:`confirm_stop_suggestion` with
    explicit confirmation before it can enter local memory.
    """
    if not completed_action or not completed_action.strip():
        return None
    candidate = (
        _heuristic_stop_suggestion(completed_action)
        if extractor is None
        else extractor(completed_action)
    )
    if not candidate or not candidate.strip():
        return None
    return StopDecisionSuggestion(
        content=sanitize(candidate.strip(), field_name="stop_suggestion"),
        origin=(
            "heuristic"
            if origin is None and extractor is None
            else (origin or "extractor")
        ),
    )


def confirm_stop_suggestion(
    storage: Storage,
    suggestion: StopDecisionSuggestion,
    *,
    confirmed: bool,
    scope: str = "project-shared",
    source: str = "agent-confirmed",
) -> str | None:
    """Persist a Stop suggestion only after an explicit confirmation."""
    if not confirmed:
        return None
    return stop_capture(storage, suggestion.content, scope=scope, source=source)


def _heuristic_stop_suggestion(completed_action: str) -> str:
    """Keep the deterministic default deliberately modest and inspectable."""
    for line in completed_action.splitlines():
        candidate = line.strip()
        if candidate:
            return candidate
    return ""


def _format_brief(memories: list[Memory]) -> list[str]:
    """Format a list of memories into a concise brief."""
    lines: list[str] = []
    for mem in memories:
        line = f"- {mem.content}"
        if mem.rationale:
            line += f" ({mem.rationale})"
        if mem.anchor_node_id:
            line += f" [anchor: {mem.anchor_node_id}]"
        lines.append(line)
    return lines
