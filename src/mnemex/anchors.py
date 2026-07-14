from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from mnemex.storage import Memory, Node, Storage


@dataclass(frozen=True, slots=True)
class Anchor:
    node_id: str | None = None
    file: str | None = None
    symbol: str | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("node_id", self.node_id),
            ("file", self.file),
            ("symbol", self.symbol),
        ):
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(f"{field_name} must be a non-empty string")

        has_node_id = self.node_id is not None
        has_file = self.file is not None
        has_symbol = self.symbol is not None
        if has_node_id:
            if has_file or has_symbol:
                raise ValueError(
                    "an anchor cannot mix node_id with file or symbol"
                )
            return
        if has_file and has_symbol:
            return
        raise ValueError(
            "an anchor requires either node_id or both file and symbol"
        )


class AnchorNotFoundError(LookupError):
    pass


class AmbiguousAnchorError(LookupError):
    pass


class FreshnessStatus(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    ORPHANED = "orphaned"
    UNANCHORED = "unanchored"


@dataclass(frozen=True, slots=True)
class FreshnessReport:
    memory_id: str
    status: FreshnessStatus
    anchor_node_id: str | None
    stored_hash: str | None
    current_hash: str | None


def resolve_anchor(storage: Storage, anchor: Anchor | str) -> Node:
    if isinstance(anchor, str):
        reference = Anchor(node_id=anchor)
    elif isinstance(anchor, Anchor):
        reference = anchor
    else:
        raise TypeError(
            f"anchor must be an Anchor or str, got {type(anchor).__name__}"
        )

    if reference.node_id is not None:
        node = storage.get_node(reference.node_id)
        if node is None:
            raise AnchorNotFoundError(
                f"anchor node not found: {reference.node_id!r}"
            )
        return node

    assert reference.file is not None
    assert reference.symbol is not None
    matches = storage.find_nodes(reference.file, reference.symbol)
    if not matches:
        raise AnchorNotFoundError(
            "anchor symbol not found: "
            f"file={reference.file!r}, symbol={reference.symbol!r}"
        )
    if len(matches) > 1:
        raise AmbiguousAnchorError(
            "anchor symbol is ambiguous: "
            f"file={reference.file!r}, symbol={reference.symbol!r}, "
            f"matches={len(matches)}"
        )
    return matches[0]


def remember(
    storage: Storage,
    content: str,
    *,
    anchor: Anchor | str | None = None,
    scope: str = "project-shared",
    memory_id: str | None = None,
    type: str = "decision",
    rationale: str = "",
    source: str = "agent",
    confidence: float = 1.0,
    importance: float = 1.0,
    tags: str = "",
) -> Memory:
    node = None if anchor is None else resolve_anchor(storage, anchor)
    timestamp = _utc_timestamp()
    memory = Memory(
        id=str(uuid4()) if memory_id is None else memory_id,
        type=type,
        content=content,
        rationale=rationale,
        anchor_node_id=None if node is None else node.id,
        anchor_hash=None if node is None else node.content_hash,
        scope=scope,
        source=source,
        confidence=confidence,
        importance=importance,
        created_at=timestamp,
        last_accessed=timestamp,
        last_verified=timestamp,
        tags=tags,
    )
    storage.insert_memory(memory)
    return memory


def forget(storage: Storage, memory_id: str) -> bool:
    return storage.delete_memory(memory_id)


def check_freshness(
    storage: Storage,
    *,
    scopes: Collection[str] = ("project-shared",),
    memory_id: str | None = None,
) -> list[FreshnessReport]:
    memories = storage.list_memories(scopes)
    reports: list[FreshnessReport] = []

    for memory in memories:
        if memory_id is not None and memory.id != memory_id:
            continue

        if memory.anchor_node_id is None:
            reports.append(
                FreshnessReport(
                    memory_id=memory.id,
                    status=FreshnessStatus.UNANCHORED,
                    anchor_node_id=None,
                    stored_hash=memory.anchor_hash,
                    current_hash=None,
                )
            )
            continue

        node = storage.get_node(memory.anchor_node_id)
        if node is None:
            reports.append(
                FreshnessReport(
                    memory_id=memory.id,
                    status=FreshnessStatus.ORPHANED,
                    anchor_node_id=memory.anchor_node_id,
                    stored_hash=memory.anchor_hash,
                    current_hash=None,
                )
            )
            continue

        status = FreshnessStatus.STALE
        if (
            memory.anchor_hash is not None
            and memory.anchor_hash == node.content_hash
        ):
            status = FreshnessStatus.FRESH
        reports.append(
            FreshnessReport(
                memory_id=memory.id,
                status=status,
                anchor_node_id=memory.anchor_node_id,
                stored_hash=memory.anchor_hash,
                current_hash=node.content_hash,
            )
        )

    return reports


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
