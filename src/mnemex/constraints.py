"""Deterministic constraints derived from explicitly tagged decisions."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum

from mnemex.storage import Storage

__all__ = [
    "Constraint",
    "ConstraintType",
    "ConstraintViolation",
    "derive_constraints",
    "enforce_constraints",
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


@dataclass(frozen=True, slots=True)
class ConstraintViolation:
    memory_id: str
    kind: ConstraintType
    phrase: str
    message: str


def derive_constraints(
    storage: Storage, *, scopes: Collection[str] = ("project-shared",)
) -> tuple[Constraint, ...]:
    """Derive constraints from active decisions tagged `constraint:<kind>:<text>`.

    Supported kinds are `required` and `forbidden`; untagged decisions remain
    advisory and are never converted into deterministic policy.
    """
    constraints: list[Constraint] = []
    for memory in storage.list_memories(scopes):
        if memory.type != "decision":
            continue
        metadata = storage.get_decision_metadata(memory.id)
        if metadata is None or metadata.status != "active":
            continue
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
            constraints.append(Constraint(memory.id, constraint_type, phrase.strip()))
    return tuple(
        sorted(
            constraints,
            key=lambda item: (item.memory_id, item.kind.value, item.phrase),
        )
    )


def enforce_constraints(
    storage: Storage,
    patch_summary: str,
    *,
    scopes: Collection[str] = ("project-shared",),
) -> tuple[ConstraintViolation, ...]:
    """Return deterministic violations for a proposed change summary."""
    if not isinstance(patch_summary, str):
        raise TypeError("patch_summary must be a string")
    normalized = patch_summary.casefold()
    violations: list[ConstraintViolation] = []
    for constraint in derive_constraints(storage, scopes=scopes):
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
