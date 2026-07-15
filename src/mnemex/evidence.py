"""Deterministic, redacted evidence selection for semantic decision checks."""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from mnemex.anchors import FreshnessStatus, check_freshness
from mnemex.indexer import trace_callers
from mnemex.retrieval import estimate_tokens, recall
from mnemex.security import RedactionLog, sanitize
from mnemex.storage import Memory, Storage

__all__ = [
    "EvidenceItem",
    "EvidenceBundle",
    "build_guard_evidence",
]

DEFAULT_EVIDENCE_TOKEN_CAP = 800


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """A persisted memory selected as bounded guard evidence."""

    memory_id: str
    content: str
    rationale: str
    anchor_node_id: str | None
    freshness: str
    source: str
    rank: int


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """The complete, sanitized payload supplied to a semantic judge."""

    path: str
    patch_summary: str
    items: tuple[EvidenceItem, ...]
    used_tokens: int
    budget_tokens: int
    payload: str

    def as_payload(self) -> dict[str, object]:
        """Return precisely the sanitized payload eligible for remote use."""
        return json.loads(self.payload) if self.payload else {}


def build_guard_evidence(
    storage: Storage,
    path: str,
    patch_summary: str,
    *,
    scopes: Collection[str] = ("project-shared",),
    max_tokens: int = DEFAULT_EVIDENCE_TOKEN_CAP,
) -> EvidenceBundle:
    """Select deterministic, bounded evidence without invoking a provider.

    Direct file anchors outrank caller-neighbour anchors, which outrank BM25
    candidates. Every supplied string is sanitized at this boundary so callers
    can inspect exactly what an opt-in remote provider would receive.
    """
    if max_tokens < 0:
        raise ValueError("max_tokens must be non-negative")

    scope_values = tuple(scopes)
    # Let storage validate scopes even when the database contains no memories.
    storage.list_memories(scope_values)
    redactions = RedactionLog()
    clean_path = sanitize(path, field_name="path", log=redactions)
    clean_patch = sanitize(
        patch_summary, field_name="patch_summary", log=redactions
    )
    freshness = {
        report.memory_id: report.status.value
        for report in check_freshness(storage, scopes=scope_values)
    }

    selected: list[tuple[str, Memory]] = []
    selected.extend(
        ("anchor-file", memory)
        for memory in storage.list_memories_by_anchor_file(path, scope_values)
    )

    caller_files: set[str] = set()
    for (node_id,) in storage.connection.execute(
        "SELECT id FROM nodes WHERE file = ?", (path.replace("\\", "/"),)
    ).fetchall():
        caller_files.update(
            caller.file for caller, _edge_type in trace_callers(storage, node_id)
        )
    for caller_file in sorted(caller_files):
        selected.extend(
            ("caller", memory)
            for memory in storage.list_memories_by_anchor_file(
                caller_file, scope_values
            )
        )

    query = " ".join(part for part in (Path(path).stem, clean_patch) if part)
    recalled = recall(
        storage,
        query,
        scopes=scope_values,
        limit=10,
        max_tokens=None,
    )
    selected.extend(("bm25", scored.memory) for scored in recalled.included)

    path_for_payload, patch_for_payload, payload = _fit_request_fields(
        clean_path, clean_patch, max_tokens
    )
    items: list[EvidenceItem] = []
    seen: set[str] = set()
    for source, memory in selected:
        if memory.id in seen:
            continue
        seen.add(memory.id)
        content = sanitize(memory.content, field_name="content", log=redactions)
        rationale = sanitize(
            memory.rationale, field_name="rationale", log=redactions
        )
        candidate = EvidenceItem(
            memory_id=memory.id,
            content=content,
            rationale=rationale,
            anchor_node_id=memory.anchor_node_id,
            freshness=freshness.get(
                memory.id, FreshnessStatus.UNANCHORED.value
            ),
            source=source,
            rank=len(items) + 1,
        )
        candidate_payload = _serialize_payload(
            path_for_payload, patch_for_payload, [*items, candidate]
        )
        if estimate_tokens(candidate_payload) > max_tokens:
            continue
        items.append(candidate)
        payload = candidate_payload

    return EvidenceBundle(
        path=path_for_payload,
        patch_summary=patch_for_payload,
        items=tuple(items),
        used_tokens=estimate_tokens(payload),
        budget_tokens=max_tokens,
        payload=payload,
    )


def _fit_request_fields(
    path: str, patch_summary: str, max_tokens: int
) -> tuple[str, str, str]:
    """Bound the complete serialized request, including JSON framing."""
    if max_tokens == 0:
        return "", "", ""

    payload = _serialize_payload(path, patch_summary, [])
    if estimate_tokens(payload) <= max_tokens:
        return path, patch_summary, payload

    patch_summary = _truncate_to_budget(
        patch_summary,
        lambda value: _serialize_payload(path, value, []),
        max_tokens,
    )
    payload = _serialize_payload(path, patch_summary, [])
    if estimate_tokens(payload) <= max_tokens:
        return path, patch_summary, payload

    path = _truncate_to_budget(
        path,
        lambda value: _serialize_payload(value, "", []),
        max_tokens,
    )
    payload = _serialize_payload(path, "", [])
    if estimate_tokens(payload) <= max_tokens:
        return path, "", payload

    # Very small valid budgets cannot hold the normal schema. Never send an
    # over-budget structure; an empty object remains inspectable and valid JSON.
    empty = "{}"
    return "", "", empty if estimate_tokens(empty) <= max_tokens else ""


def _truncate_to_budget(
    value: str,
    serialize: Callable[[str], str],
    max_tokens: int,
) -> str:
    """Find the longest prefix whose containing serialized payload fits."""
    low, high = 0, len(value)
    while low < high:
        midpoint = (low + high + 1) // 2
        if estimate_tokens(serialize(value[:midpoint])) <= max_tokens:
            low = midpoint
        else:
            high = midpoint - 1
    return value[:low]


def _serialize_payload(
    path: str, patch_summary: str, items: Collection[EvidenceItem]
) -> str:
    return json.dumps(
        {
            "path": path,
            "patch_summary": patch_summary,
            "decisions": [asdict(item) for item in items],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
