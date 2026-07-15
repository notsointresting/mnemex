from __future__ import annotations

from mnemex.anchors import remember
from mnemex.decision_guard import check_proposed_change, override_decision_guard
from mnemex.judge import SemanticJudgment, Verdict
from mnemex.storage import Node, Storage


class FixtureJudge:
    def __init__(self, judgment: SemanticJudgment) -> None:
        self.judgment = judgment
        self.payloads: list[str] = []

    def evaluate(self, evidence: str) -> SemanticJudgment:
        self.payloads.append(evidence)
        return self.judgment


def _add_auth_decision(storage: Storage) -> str:
    node = Node(
        id="auth-node",
        type="function",
        name="authenticate",
        file="src/auth.py",
        line_start=1,
        content_hash="auth-hash",
        language="python",
    )
    storage.upsert_node(node)
    return remember(
        storage,
        "Authentication must remain stateless.",
        anchor=node.id,
    ).id


def test_local_mode_records_unavailable_result_without_network() -> None:
    with Storage() as storage:
        decision_id = _add_auth_decision(storage)

        result = check_proposed_change(
            storage, "src/auth.py", "Move session state to Redis."
        )

        assert result.judgment.verdict is Verdict.UNAVAILABLE
        assert result.blocked is False
        assert storage.get_guard_run(result.run_id).verdict == "unavailable"
        assert storage.list_guard_evidence(result.run_id)[0].memory_id == decision_id


def test_explicit_deterministic_constraint_blocks_without_semantic_judge() -> None:
    with Storage() as storage:
        node = Node(
            id="auth-node",
            type="function",
            name="authenticate",
            file="src/auth.py",
            line_start=1,
            content_hash="auth-hash",
            language="python",
        )
        storage.upsert_node(node)
        decision_id = remember(
            storage,
            "Authentication must remain stateless.",
            anchor=node.id,
            tags="constraint:forbidden:redis-backed server sessions",
        ).id

        result = check_proposed_change(
            storage,
            "src/auth.py",
            "Add Redis-backed server sessions.",
            enforce_constraints=True,
        )

        assert result.blocked is True
        assert result.judgment.verdict is Verdict.CONTRADICTION
        assert result.judgment.evidence_ids == (decision_id,)
        assert storage.get_guard_run(result.run_id).provider == "deterministic-constraints"


def test_fresh_high_confidence_contradiction_blocks_and_can_be_overridden() -> None:
    with Storage() as storage:
        decision_id = _add_auth_decision(storage)
        judge = FixtureJudge(
            SemanticJudgment(
                Verdict.CONTRADICTION,
                0.95,
                "Redis-backed sessions conflict with the decision.",
                evidence_ids=(decision_id,),
                model="fixture",
            )
        )

        result = check_proposed_change(
            storage,
            "src/auth.py",
            "Move session state to Redis.",
            judge=judge,
        )
        override = override_decision_guard(
            storage, result.run_id, actor="codex", reason="Approved migration"
        )

        assert result.blocked is True
        assert "Authentication must remain stateless" in judge.payloads[0]
        assert storage.get_guard_run(result.run_id).blocked is True
        assert storage.list_guard_overrides(result.run_id) == [override]


def test_stale_or_low_confidence_contradictions_only_warn() -> None:
    with Storage() as storage:
        decision_id = _add_auth_decision(storage)
        storage.upsert_node(
            Node(
                id="auth-node",
                type="function",
                name="authenticate",
                file="src/auth.py",
                line_start=1,
                content_hash="changed-hash",
                language="python",
            )
        )
        judge = FixtureJudge(
            SemanticJudgment(
                Verdict.CONTRADICTION,
                0.95,
                "Potential conflict.",
                evidence_ids=(decision_id,),
            )
        )

        result = check_proposed_change(
            storage, "src/auth.py", "Move session state to Redis.", judge=judge
        )

        assert result.blocked is False
        assert result.recommended_action.startswith("Review")
