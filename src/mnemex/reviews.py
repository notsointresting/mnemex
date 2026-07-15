"""Deterministic, non-destructive decision review scheduling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from mnemex.anchors import FreshnessStatus, check_freshness
from mnemex.storage import DecisionMetadata, Memory, Storage

__all__ = ["ReviewCandidate", "list_review_candidates", "reinforce_decision"]


@dataclass(frozen=True, slots=True)
class ReviewCandidate:
    memory_id: str
    priority: int
    freshness: str
    reason: str


def list_review_candidates(
    storage: Storage,
    *,
    now: datetime | None = None,
) -> list[ReviewCandidate]:
    """Rank active decisions for review without hiding or decaying any record."""
    current = datetime.now(timezone.utc) if now is None else now
    freshness = {
        report.memory_id: report.status
        for report in check_freshness(
            storage,
            scopes=("agent-private", "project-shared", "user-global"),
        )
    }
    candidates: list[ReviewCandidate] = []
    for memory in storage.list_memories(
        ("agent-private", "project-shared", "user-global")
    ):
        metadata = storage.get_decision_metadata(memory.id)
        if metadata is None or metadata.status != "active":
            continue
        candidate = _candidate(memory, metadata, freshness[memory.id], current)
        if candidate is not None:
            candidates.append(candidate)
    return sorted(candidates, key=lambda item: (-item.priority, item.memory_id))


def reinforce_decision(storage: Storage, memory_id: str) -> DecisionMetadata:
    """Record an explicit review confirmation without changing decision text."""
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    storage.record_recall(memory_id, timestamp)
    return storage.record_confirmation(memory_id, timestamp)


def _candidate(
    memory: Memory,
    metadata: DecisionMetadata,
    freshness: FreshnessStatus,
    now: datetime,
) -> ReviewCandidate | None:
    if freshness is not FreshnessStatus.FRESH:
        return ReviewCandidate(
            memory.id,
            100,
            freshness.value,
            "anchor is no longer fresh",
        )
    due = _parse_timestamp(metadata.review_after)
    if due is not None and due <= now:
        return ReviewCandidate(memory.id, 50, freshness.value, "review is due")
    if metadata.access_count == 0:
        return ReviewCandidate(memory.id, 10, freshness.value, "never recalled")
    return None


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
