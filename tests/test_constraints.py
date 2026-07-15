from __future__ import annotations

from mnemex.anchors import remember
from mnemex.constraints import (
    ConstraintType,
    derive_constraints,
    enforce_constraints,
)
from mnemex.storage import Storage


def test_constraints_are_explicit_scope_safe_and_deterministic() -> None:
    with Storage() as storage:
        required = remember(
            storage,
            "Use signed cookies",
            tags="constraint:required:signed cookies",
            memory_id="a",
        )
        forbidden = remember(
            storage,
            "Avoid sessions",
            tags="constraint:forbidden:server sessions",
            memory_id="b",
        )
        remember(
            storage,
            "private",
            tags="constraint:forbidden:private",
            scope="agent-private",
            memory_id="c",
        )

        assert [item.memory_id for item in derive_constraints(storage)] == [
            required.id,
            forbidden.id,
        ]
        violations = enforce_constraints(storage, "Use server sessions")
        assert [(item.memory_id, item.kind) for item in violations] == [
            (required.id, ConstraintType.REQUIRED),
            (forbidden.id, ConstraintType.FORBIDDEN),
        ]


def test_superseded_constraint_is_not_enforced() -> None:
    with Storage() as storage:
        memory = remember(storage, "Old constraint", tags="constraint:forbidden:legacy")
        storage.set_decision_status(memory.id, "superseded")
        assert enforce_constraints(storage, "legacy") == ()


def test_non_decision_memory_never_activates_a_constraint() -> None:
    with Storage() as storage:
        remember(
            storage,
            "A fact, not a decision.",
            type="fact",
            tags="constraint:forbidden:must not activate",
        )

        assert derive_constraints(storage) == ()
        assert enforce_constraints(storage, "must not activate") == ()
