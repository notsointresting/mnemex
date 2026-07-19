from __future__ import annotations

from mnemex.anchors import remember
from mnemex.constraints import (
    ConstraintType,
    derive_constraints,
    enforce_constraints,
    scoped_invariant_memories,
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



def test_applies_to_scopes_constraint_to_matching_path() -> None:
    with Storage() as storage:
        forbidden = remember(
            storage,
            "All writes go through Repository",
            tags="constraint:forbidden:direct sqlite write,applies-to:src/payments/**",
            memory_id="scoped",
        )
        matched = enforce_constraints(
            storage, "add a direct sqlite write", path="src/payments/refund.py"
        )
        assert [item.memory_id for item in matched] == [forbidden.id]
        # Outside the glob the same change is not a violation.
        assert (
            enforce_constraints(
                storage, "add a direct sqlite write", path="src/auth/login.py"
            )
            == ()
        )
        # Without a path a scoped constraint cannot be evaluated and does not fire.
        assert enforce_constraints(storage, "add a direct sqlite write") == ()


def test_unscoped_constraint_still_applies_regardless_of_path() -> None:
    with Storage() as storage:
        forbidden = remember(
            storage,
            "Avoid sessions",
            tags="constraint:forbidden:server sessions",
            memory_id="unscoped",
        )
        for path in (None, "src/anything.py", "deep/nested/module.py"):
            violations = enforce_constraints(
                storage, "use server sessions", path=path
            )
            assert [item.memory_id for item in violations] == [forbidden.id]


def test_windows_path_normalizes_to_the_same_glob() -> None:
    with Storage() as storage:
        remember(
            storage,
            "scope",
            tags="constraint:forbidden:x,applies-to:src/payments/**",
            memory_id="w",
        )
        assert enforce_constraints(storage, "x", path="src\\payments\\refund.py")
        assert enforce_constraints(storage, "x", path="src/payments/refund.py")


def test_multiple_globs_are_deterministic_and_deduplicated() -> None:
    with Storage() as storage:
        remember(
            storage,
            "scope",
            tags=(
                "constraint:forbidden:x,applies-to:src/b/**,"
                "applies-to:src/a/**,applies-to:src/a/**"
            ),
            memory_id="m",
        )
        (constraint,) = derive_constraints(storage)
        assert constraint.applies_to == ("src/a/**", "src/b/**")
        assert enforce_constraints(storage, "x", path="src/a/one.py")
        assert enforce_constraints(storage, "x", path="src/b/two.py")
        assert enforce_constraints(storage, "x", path="src/c/three.py") == ()


def test_single_star_stays_within_one_path_segment() -> None:
    with Storage() as storage:
        remember(
            storage,
            "s",
            tags="constraint:forbidden:x,applies-to:src/payments/*",
            memory_id="s",
        )
        assert enforce_constraints(storage, "x", path="src/payments/refund.py")
        assert (
            enforce_constraints(storage, "x", path="src/payments/sub/refund.py") == ()
        )


def test_scoped_invariant_memories_selects_matching_active_decisions() -> None:
    with Storage() as storage:
        governing = remember(
            storage,
            "All writes go through Repository",
            tags="constraint:forbidden:direct sqlite write,applies-to:src/payments/**",
            memory_id="gov",
        )
        assert [
            item.id
            for item in scoped_invariant_memories(storage, "src/payments/refund.py")
        ] == [governing.id]
        assert scoped_invariant_memories(storage, "src/other.py") == ()
        # A superseded governing decision is no longer selectable.
        storage.set_decision_status(governing.id, "superseded")
        assert scoped_invariant_memories(storage, "src/payments/refund.py") == ()
