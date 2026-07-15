"""Append-only lifecycle operations for anchored decisions.

Memory rows are evidence and are never rewritten here.  A corrected decision
is represented by a new memory whose metadata points at the decision it
supersedes; the earlier memory is then marked superseded.  This makes the
current decision easy to find without losing the original rationale or anchor
hash that led to it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from mnemex.anchors import Anchor, FreshnessStatus, check_freshness, remember
from mnemex.security import RedactionLog, sanitize
from mnemex.storage import DecisionMetadata, Memory, Storage

__all__ = [
    "create_successor",
    "reconcile_stale_decision",
    "refresh_decision",
    "retire_decision",
    "supersede_decision",
]

_ACTIVE = "active"
_SUPERSEDED = "superseded"
_RETIRED = "retired"


def create_successor(
    storage: Storage,
    memory_id: str,
    content: str,
    *,
    anchor: Anchor | str | None = None,
    rationale: str | None = None,
    tags: str | None = None,
    successor_id: str | None = None,
) -> Memory:
    """Create the active replacement for one active decision.

    ``None`` for ``anchor``, ``rationale``, or ``tags`` retains the prior
    value.  The successor's metadata points backward to ``memory_id``; the
    original memory remains intact and changes only metadata status.
    """
    prior = _require_active_decision(storage, memory_id)
    redactions = RedactionLog()
    clean_content = _clean_required(content, "content", redactions)
    clean_rationale = _clean_optional(
        prior.rationale if rationale is None else rationale, "rationale", redactions
    )
    clean_tags = _clean_optional(
        prior.tags if tags is None else tags, "tags", redactions
    )
    clean_successor_id = _clean_identifier(successor_id)
    effective_anchor: Anchor | str | None = (
        prior.anchor_node_id if anchor is None else anchor
    )

    successor = remember(
        storage,
        clean_content,
        anchor=effective_anchor,
        scope=prior.scope,
        memory_id=clean_successor_id,
        type=prior.type,
        rationale=clean_rationale,
        source=prior.source,
        confidence=prior.confidence,
        importance=prior.importance,
        tags=clean_tags,
        redaction_log=redactions,
    )
    storage.ensure_decision_metadata(
        successor.id, supersedes_memory_id=prior.id
    )
    storage.set_decision_status(prior.id, _SUPERSEDED)
    storage.record_confirmation(prior.id, _utc_timestamp())
    return successor


def supersede_decision(
    storage: Storage,
    memory_id: str,
    content: str,
    *,
    anchor: Anchor | str | None = None,
    rationale: str | None = None,
    tags: str | None = None,
    successor_id: str | None = None,
) -> Memory:
    """Create a successor; explicit name for a supersession transition."""
    return create_successor(
        storage,
        memory_id,
        content,
        anchor=anchor,
        rationale=rationale,
        tags=tags,
        successor_id=successor_id,
    )


def retire_decision(storage: Storage, memory_id: str) -> DecisionMetadata:
    """Retire an active decision while preserving its memory row and anchor."""
    prior = _require_active_decision(storage, memory_id)
    storage.set_decision_status(prior.id, _RETIRED)
    return storage.record_confirmation(prior.id, _utc_timestamp())


def refresh_decision(
    storage: Storage,
    memory_id: str,
    *,
    anchor: Anchor | str | None = None,
    successor_id: str | None = None,
) -> Memory:
    """Append a re-verified successor for a stale or orphaned decision.

    A stale decision refreshes against its current anchor by default.  An
    orphaned or unanchored decision must receive an explicit replacement
    anchor, so a caller cannot silently assert a new code location.
    """
    prior = _require_active_decision(storage, memory_id)
    freshness = _freshness(storage, prior.id)
    if freshness is FreshnessStatus.FRESH:
        raise ValueError("Fresh decisions do not require a lifecycle refresh")
    if freshness in {FreshnessStatus.ORPHANED, FreshnessStatus.UNANCHORED}:
        if anchor is None:
            raise ValueError("An orphaned or unanchored decision needs an anchor")

    return create_successor(
        storage,
        prior.id,
        prior.content,
        anchor=anchor,
        rationale=prior.rationale,
        tags=prior.tags,
        successor_id=successor_id,
    )


def reconcile_stale_decision(
    storage: Storage,
    memory_id: str,
    changed_symbol: str,
    diff: str,
) -> str:
    """Return one offline reconciliation verdict for a decision.

    The result is always exactly ``still_valid``, ``superseded``,
    ``possible_regression``, or ``human_review``.  Fresh anchors are valid;
    a durable successor wins; a stale anchor is a possible regression only
    when the sanitized non-empty diff identifies that same symbol.  Missing,
    ambiguous, and unanchored evidence deliberately requires human review.
    """
    memory = storage.get_memory(memory_id)
    if memory is None:
        return "human_review"

    metadata = storage.get_decision_metadata(memory.id)
    if metadata is not None and metadata.status == _SUPERSEDED:
        return "superseded"
    if _has_active_successor(storage, memory.id):
        return "superseded"
    if metadata is not None and metadata.status == _RETIRED:
        return "human_review"

    freshness = _freshness(storage, memory.id)
    if freshness is FreshnessStatus.FRESH:
        return "still_valid"
    if freshness is not FreshnessStatus.STALE:
        return "human_review"

    clean_symbol = _clean_optional(changed_symbol, "changed_symbol", RedactionLog())
    clean_diff = _clean_optional(diff, "diff", RedactionLog())
    if not clean_symbol.strip() or not clean_diff.strip():
        return "human_review"
    return (
        "possible_regression"
        if _matches_anchor_symbol(storage, memory, clean_symbol)
        else "human_review"
    )


def _require_active_decision(storage: Storage, memory_id: str) -> Memory:
    memory = storage.get_memory(memory_id)
    if memory is None:
        raise ValueError("Unknown decision")
    metadata = storage.ensure_decision_metadata(memory.id)
    if metadata.status != _ACTIVE:
        raise ValueError("Only active decisions can transition")
    return memory


def _freshness(storage: Storage, memory_id: str) -> FreshnessStatus:
    reports = check_freshness(
        storage,
        scopes=("agent-private", "project-shared", "user-global"),
        memory_id=memory_id,
    )
    if not reports:
        raise ValueError("Decision is unavailable for freshness checks")
    return reports[0].status


def _has_active_successor(storage: Storage, memory_id: str) -> bool:
    row = storage.connection.execute(
        """
        SELECT 1
        FROM decision_metadata
        WHERE supersedes_memory_id = ? AND status = ?
        LIMIT 1
        """,
        (memory_id, _ACTIVE),
    ).fetchone()
    return row is not None


def _matches_anchor_symbol(storage: Storage, memory: Memory, symbol: str) -> bool:
    if memory.anchor_node_id is None:
        return False
    node = storage.get_node(memory.anchor_node_id)
    if node is None:
        return False
    normalized = symbol.strip().replace("\\", "/")
    candidates = {
        node.id,
        node.name,
        f"{node.file}:{node.name}",
        f"{node.file}#{node.name}",
    }
    return normalized in candidates


def _clean_required(value: str, field_name: str, log: RedactionLog) -> str:
    clean_value = _clean_optional(value, field_name, log)
    if not clean_value.strip():
        raise ValueError(f"{field_name} must contain non-private text")
    return clean_value


def _clean_optional(value: str, field_name: str, log: RedactionLog) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return sanitize(value, field_name=field_name, log=log)


def _clean_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    clean_value = _clean_optional(value, "successor_id", RedactionLog())
    if not clean_value or clean_value != value:
        raise ValueError("successor_id must be a non-private identifier")
    return clean_value


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
