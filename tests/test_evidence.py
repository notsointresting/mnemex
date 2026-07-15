from __future__ import annotations

from mnemex.anchors import remember
from mnemex.evidence import build_guard_evidence
from mnemex.retrieval import estimate_tokens
from mnemex.storage import Node, Storage


def _node(node_id: str, file: str, name: str) -> Node:
    return Node(
        id=node_id,
        type="function",
        name=name,
        file=file,
        line_start=1,
        content_hash=f"hash-{node_id}",
        language="python",
    )


def test_evidence_prioritizes_file_anchors_and_never_leaks_private_scope() -> None:
    with Storage() as storage:
        storage.upsert_node(_node("auth", "src/auth.py", "authenticate"))
        storage.upsert_node(_node("caller", "src/app.py", "login"))
        with storage.connection:
            storage.connection.execute(
                "INSERT INTO edges(from_id, to_id, type, confidence) VALUES (?, ?, ?, ?)",
                ("caller", "auth", "calls", 1.0),
            )
        direct = remember(
            storage, "Authentication must remain stateless", anchor="auth"
        )
        caller = remember(storage, "Login depends on stateless authentication", anchor="caller")
        private = remember(
            storage,
            "Authentication secret private strategy",
            scope="agent-private",
        )

        bundle = build_guard_evidence(
            storage,
            "src/auth.py",
            "Move session state to Redis",
            max_tokens=200,
        )

        assert [item.memory_id for item in bundle.items][:2] == [
            direct.id,
            caller.id,
        ]
        assert private.id not in {item.memory_id for item in bundle.items}
        assert bundle.used_tokens <= bundle.budget_tokens


def test_evidence_sanitizes_the_remote_payload_and_enforces_the_cap() -> None:
    with Storage() as storage:
        secret = "AKIAIOSFODNN7EXAMPLE"
        memory = remember(storage, f"auth uses secret {secret}")

        bundle = build_guard_evidence(
            storage,
            "src/auth.py",
            f"Patch mentions {secret}",
            max_tokens=0,
        )

        assert bundle.items == ()
        assert bundle.used_tokens == 0
        assert secret not in str(bundle.as_payload())
        assert memory.id not in str(bundle.as_payload())


def test_evidence_hard_caps_the_entire_serialized_remote_payload() -> None:
    with Storage() as storage:
        bundle = build_guard_evidence(
            storage,
            "src/auth.py",
            "word " * 1_000,
            max_tokens=8,
        )

        assert bundle.used_tokens <= 8
        assert estimate_tokens(bundle.payload) <= 8
        assert bundle.as_payload() in ({}, {"path": "", "patch_summary": "", "decisions": []})


def test_evidence_reports_redaction_count() -> None:
    with Storage() as storage:
        secret = "AKIA" + "IOSFODNN7EXAMPLE"
        remember(storage, "auth decision")

        bundle = build_guard_evidence(
            storage,
            "src/auth.py",
            f"Patch mentions {secret} and password=" + "hunter2secret",
        )

        assert bundle.redaction_count >= 2
        assert secret not in bundle.payload

        clean = build_guard_evidence(storage, "src/auth.py", "harmless patch")
        assert clean.redaction_count == 0
