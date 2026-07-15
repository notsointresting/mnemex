"""Derived, local conflict inbox for active shared decisions.

The inbox deliberately stores no state: it evaluates in-scope decision memories
against their current lifecycle metadata each time it is queried.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from itertools import combinations

from mnemex.storage import Memory, Storage

__all__ = [
    "Conflict",
    "ConflictListResult",
    "ConflictReviewResult",
    "list_conflicts",
    "review_conflict",
]

_ACTIVE = "active"
_DECISION = "decision"
_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)
_NEGATIONS = frozenset({"avoid", "deny", "disable", "exclude", "never", "no", "not"})
_OPPOSITE_TERMS = frozenset(
    {
        frozenset(("allow", "deny")),
        frozenset(("enable", "disable")),
        frozenset(("include", "exclude")),
        frozenset(("use", "avoid")),
    }
)
_ALIASES = {
    "allowed": "allow",
    "allows": "allow",
    "avoided": "avoid",
    "avoids": "avoid",
    "disabled": "disable",
    "disables": "disable",
    "enabled": "enable",
    "enables": "enable",
    "excluded": "exclude",
    "excludes": "exclude",
    "included": "include",
    "includes": "include",
    "using": "use",
    "used": "use",
    "uses": "use",
}


@dataclass(frozen=True, slots=True)
class Conflict:
    """A potential contradiction between two active, in-scope decisions."""

    memory_ids: tuple[str, str]
    shared_terms: tuple[str, ...]
    shared_tags: tuple[str, ...]
    anchor_file: str | None
    anchor_node_id: str | None
    context: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConflictListResult:
    """The current derived inbox; no result is persisted."""

    conflicts: tuple[Conflict, ...]
    scopes: tuple[str, ...]
    scanned_decision_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConflictReviewResult:
    """Full, scope-filtered decision text for one inbox item."""

    conflict: Conflict
    left: Memory
    right: Memory


def list_conflicts(
    storage: Storage,
    *,
    scopes: Collection[str] = ("project-shared",),
) -> ConflictListResult:
    """Return deterministic conflicts among active decisions in ``scopes``.

    A pair must share an anchor node, anchor file, or normalized tag.  It is
    then considered conflicting only when its overlapping subject terms have
    opposite directives (for example, ``use`` versus ``avoid``) or one side
    negates an otherwise matching directive.
    """
    allowed_scopes = tuple(scopes)
    decisions = _active_decisions(storage, allowed_scopes)
    contexts = {memory.id: _context(storage, memory) for memory in decisions}
    conflicts: list[Conflict] = []

    for left, right in combinations(decisions, 2):
        conflict = _find_conflict(left, right, contexts[left.id], contexts[right.id])
        if conflict is not None:
            conflicts.append(conflict)

    return ConflictListResult(
        conflicts=tuple(sorted(conflicts, key=lambda item: item.memory_ids)),
        scopes=allowed_scopes,
        scanned_decision_ids=tuple(memory.id for memory in decisions),
    )


def review_conflict(
    storage: Storage,
    first_memory_id: str,
    second_memory_id: str,
    *,
    scopes: Collection[str] = ("project-shared",),
) -> ConflictReviewResult:
    """Return one currently valid conflict without bypassing scope filtering."""
    requested_ids = tuple(sorted((first_memory_id, second_memory_id)))
    inbox = list_conflicts(storage, scopes=scopes)
    conflict = next(
        (item for item in inbox.conflicts if item.memory_ids == requested_ids), None
    )
    if conflict is None:
        raise LookupError("No in-scope active conflict exists for these decisions")

    memories = {
        memory.id: memory
        for memory in _active_decisions(storage, inbox.scopes)
        if memory.id in requested_ids
    }
    return ConflictReviewResult(
        conflict=conflict,
        left=memories[requested_ids[0]],
        right=memories[requested_ids[1]],
    )


def _active_decisions(storage: Storage, scopes: Collection[str]) -> list[Memory]:
    decisions = []
    for memory in storage.list_memories(scopes):
        metadata = storage.get_decision_metadata(memory.id)
        if memory.type == _DECISION and metadata is not None and metadata.status == _ACTIVE:
            decisions.append(memory)
    return decisions


def _find_conflict(
    left: Memory,
    right: Memory,
    left_context: tuple[str | None, str | None],
    right_context: tuple[str | None, str | None],
) -> Conflict | None:
    left_node, left_file = left_context
    right_node, right_file = right_context
    shared_tags = tuple(sorted(_tags(left).intersection(_tags(right))))
    context: list[str] = []
    anchor_node_id = None
    anchor_file = None
    if left_node is not None and left_node == right_node:
        anchor_node_id = left_node
        anchor_file = left_file
        context.append("same anchor node")
    elif left_file is not None and left_file == right_file:
        anchor_file = left_file
        context.append("same anchor file")
    if shared_tags:
        context.append("matching tags")
    if not context:
        return None

    left_terms = _terms(left.content + " " + left.rationale)
    right_terms = _terms(right.content + " " + right.rationale)
    shared_terms = tuple(sorted(_subject_terms(left_terms).intersection(_subject_terms(right_terms))))
    if not shared_terms or not _contradicts(left_terms, right_terms):
        return None
    return Conflict(
        memory_ids=tuple(sorted((left.id, right.id))),
        shared_terms=shared_terms,
        shared_tags=shared_tags,
        anchor_file=anchor_file,
        anchor_node_id=anchor_node_id,
        context=tuple(context),
    )


def _context(storage: Storage, memory: Memory) -> tuple[str | None, str | None]:
    if memory.anchor_node_id is None:
        return None, None
    node = storage.get_node(memory.anchor_node_id)
    return memory.anchor_node_id, None if node is None else node.file.replace("\\", "/")


def _tags(memory: Memory) -> set[str]:
    return _terms(memory.tags)


def _terms(value: str) -> set[str]:
    return {_normalize(token) for token in _TOKEN_RE.findall(value.casefold())} - _STOP_WORDS


def _normalize(token: str) -> str:
    token = _ALIASES.get(token, token)
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("s") and len(token) > 3:
        return token[:-1]
    return token


def _subject_terms(terms: set[str]) -> set[str]:
    directives = set().union(*_OPPOSITE_TERMS, _NEGATIONS)
    return terms.difference(directives)


def _contradicts(left_terms: set[str], right_terms: set[str]) -> bool:
    if bool(left_terms.intersection(_NEGATIONS)) != bool(right_terms.intersection(_NEGATIONS)):
        return True
    return any(pair.issubset(left_terms.union(right_terms)) and bool(pair.intersection(left_terms)) != bool(pair.intersection(right_terms)) for pair in _OPPOSITE_TERMS)
