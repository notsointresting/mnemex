"""Regression tests for public anchor-API input-type validation.

The independent Phase 1 verifier observed that an invalid ``anchor`` argument
type previously surfaced a raw ``AttributeError`` from internal attribute
access. A public entry point should fail closed with a clear ``TypeError``.
"""

import pytest

from mnemex.anchors import Anchor, remember, resolve_anchor
from mnemex.storage import Storage


@pytest.mark.parametrize("bad_anchor", [123, 4.5, b"node", ["node"], {"n": 1}])
def test_resolve_anchor_rejects_non_str_non_anchor(bad_anchor: object) -> None:
    with Storage() as storage:
        with pytest.raises(TypeError, match="anchor must be an Anchor or str"):
            resolve_anchor(storage, bad_anchor)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_anchor", [123, object()])
def test_remember_rejects_bad_anchor_type_without_persisting(
    bad_anchor: object,
) -> None:
    with Storage() as storage:
        with pytest.raises(TypeError, match="anchor must be an Anchor or str"):
            remember(storage, "should not persist", anchor=bad_anchor)  # type: ignore[arg-type]
        assert storage.list_memories(("project-shared",)) == []


def test_valid_anchor_types_still_resolve() -> None:
    with Storage() as storage:
        node = storage
        assert isinstance(node, Storage)
        # A str and an equivalent Anchor must remain accepted paths.
        with pytest.raises(Exception) as by_string:
            resolve_anchor(storage, "absent-node")
        with pytest.raises(Exception) as by_anchor:
            resolve_anchor(storage, Anchor(node_id="absent-node"))
        # Both take the not-found path, not the TypeError guard.
        assert not isinstance(by_string.value, TypeError)
        assert not isinstance(by_anchor.value, TypeError)
