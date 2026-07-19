"""Deterministic constraints derived from explicitly tagged decisions.

An active decision becomes a deterministic rule only through an explicit
``constraint:<kind>:<phrase>`` tag. A decision may additionally carry one or
more ``applies-to:<glob>`` tags to scope the rule to changed paths that match a
glob, even when the governing decision is anchored to a symbol in a different
file. A constraint with no ``applies-to`` tag keeps its original path-independent
behavior. Scoping only decides *where* a rule is evaluated; the anchor's content
hash still decides whether the rule is fresh enough to block (enforced by the
guard, not here).
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache

from mnemex.storage import Memory, Storage

__all__ = [
    "Constraint",
    "ConstraintType",
    "ConstraintViolation",
    "derive_constraints",
    "enforce_constraints",
    "scoped_invariant_memories",
]


class ConstraintType(str, Enum):
    """Constraint kinds accepted by the explicit tag grammar."""

    REQUIRED = "required"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class Constraint:
    memory_id: str
    kind: ConstraintType
    phrase: str
    #: Path globs scoping this rule; empty means it applies to every path.
    applies_to: tuple[str, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class ConstraintViolation:
    memory_id: str
    kind: ConstraintType
    phrase: str
    message: str


def _applies_to_globs(tags: str) -> tuple[str, ...]:
    """Extract deduplicated, deterministic ``applies-to`` globs from a tag string."""
    globs: list[str] = []
    for tag in filter(None, (part.strip() for part in tags.split(","))):
        prefix, separator, value = tag.partition(":")
        if prefix == "applies-to" and separator and value.strip():
            normalized = value.strip().replace("\\", "/")
            if normalized not in globs:
                globs.append(normalized)
    return tuple(sorted(globs))


@lru_cache(maxsize=512)
def _compile_glob(pattern: str) -> re.Pattern[str]:
    """Compile a path glob where ``**`` spans directories and ``*`` stays in one.

    ``?`` matches a single non-separator character. Matching is case-sensitive
    and anchored, so it is deterministic across platforms once paths are
    normalized to forward slashes.
    """
    parts: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if index + 1 < length and pattern[index + 1] == "*":
                parts.append(".*")
                index += 2
            else:
                parts.append("[^/]*")
                index += 1
        elif char == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(char))
            index += 1
    return re.compile("^" + "".join(parts) + r"\Z")


def _match_path_glob(path: str, pattern: str) -> bool:
    return _compile_glob(pattern.replace("\\", "/")).match(path) is not None


def _constraint_applies(constraint: Constraint, path: str | None) -> bool:
    """A scoped constraint fires only when a provided path matches a glob."""
    if not constraint.applies_to:
        return True
    if path is None:
        return False
    normalized = path.replace("\\", "/")
    return any(_match_path_glob(normalized, glob) for glob in constraint.applies_to)


def _active_decisions(
    storage: Storage, scopes: Collection[str]
) -> list[Memory]:
    active: list[Memory] = []
    for memory in storage.list_memories(scopes):
        if memory.type != "decision":
            continue
        metadata = storage.get_decision_metadata(memory.id)
        if metadata is None or metadata.status != "active":
            continue
        active.append(memory)
    return active


def derive_constraints(
    storage: Storage, *, scopes: Collection[str] = ("project-shared",)
) -> tuple[Constraint, ...]:
    """Derive constraints from active decisions tagged `constraint:<kind>:<text>`.

    Supported kinds are `required` and `forbidden`; untagged decisions remain
    advisory and are never converted into deterministic policy. Any
    `applies-to:<glob>` tags on the same decision scope every constraint it
    declares.
    """
    constraints: list[Constraint] = []
    for memory in _active_decisions(storage, scopes):
        globs = _applies_to_globs(memory.tags)
        for tag in filter(None, (part.strip() for part in memory.tags.split(","))):
            prefix, separator, value = tag.partition(":")
            if prefix != "constraint" or not separator:
                continue
            kind, separator, phrase = value.partition(":")
            if not separator or not phrase.strip():
                continue
            try:
                constraint_type = ConstraintType(kind)
            except ValueError:
                continue
            constraints.append(
                Constraint(memory.id, constraint_type, phrase.strip(), globs)
            )
    return tuple(
        sorted(
            constraints,
            key=lambda item: (item.memory_id, item.kind.value, item.phrase),
        )
    )


def scoped_invariant_memories(
    storage: Storage,
    path: str,
    *,
    scopes: Collection[str] = ("project-shared",),
) -> tuple[Memory, ...]:
    """Active decisions whose ``applies-to`` globs match ``path``.

    Used by evidence selection so a governing invariant anchored in another file
    becomes citable (and therefore, only when fresh, blocking) for the changed
    path.
    """
    normalized = path.replace("\\", "/")
    matches: list[Memory] = []
    for memory in _active_decisions(storage, scopes):
        globs = _applies_to_globs(memory.tags)
        if globs and any(_match_path_glob(normalized, glob) for glob in globs):
            matches.append(memory)
    return tuple(matches)


def enforce_constraints(
    storage: Storage,
    patch_summary: str,
    *,
    path: str | None = None,
    scopes: Collection[str] = ("project-shared",),
) -> tuple[ConstraintViolation, ...]:
    """Return deterministic violations for a proposed change summary.

    When ``path`` is provided, scoped constraints (those carrying an
    ``applies-to`` glob) are evaluated only if the path matches; unscoped
    constraints are always evaluated, preserving the original behavior.
    """
    if not isinstance(patch_summary, str):
        raise TypeError("patch_summary must be a string")
    normalized = patch_summary.casefold()
    violations: list[ConstraintViolation] = []
    for constraint in derive_constraints(storage, scopes=scopes):
        if not _constraint_applies(constraint, path):
            continue
        present = constraint.phrase.casefold() in normalized
        if constraint.kind is ConstraintType.REQUIRED and not present:
            violations.append(
                ConstraintViolation(
                    constraint.memory_id,
                    constraint.kind,
                    constraint.phrase,
                    "Required phrase is absent from the proposed change.",
                )
            )
        if constraint.kind is ConstraintType.FORBIDDEN and present:
            violations.append(
                ConstraintViolation(
                    constraint.memory_id,
                    constraint.kind,
                    constraint.phrase,
                    "Forbidden phrase appears in the proposed change.",
                )
            )
    return tuple(violations)
