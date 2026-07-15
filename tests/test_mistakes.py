from __future__ import annotations

from mnemex.hooks import confirm_stop_suggestion, suggest_stop_decision
from mnemex.mistakes import guard_against_past_mistakes, record_mistake
from mnemex.storage import Storage


def _record_auth_mistake(storage: Storage, *, scope: str = "project-shared") -> str:
    return record_mistake(
        storage,
        failure_signature="Session migration dropped CSRF validation",
        root_cause="The authentication middleware was bypassed",
        affected_symbol="authenticate",
        corrective_action="Keep CSRF validation in the authentication middleware",
        scope=scope,
    ).id


def test_mistake_guard_respects_scope_isolation() -> None:
    with Storage() as storage:
        _record_auth_mistake(storage, scope="agent-private")

        result = guard_against_past_mistakes(
            storage,
            "Change CSRF validation in authenticate",
            affected_symbol="authenticate",
        )

        assert result.warnings == ()
        assert not result.should_warn


def test_mistake_memory_is_redacted_before_persistence() -> None:
    aws_key = "AKIAIOSFODNN7EXAMPLE"
    with Storage() as storage:
        memory = record_mistake(
            storage,
            failure_signature=f"Deployment exposed {aws_key}",
            root_cause="Credential copied into deployment command",
            affected_symbol="deploy_service",
            corrective_action="Read the credential from the local secret store",
        )

        stored = storage.get_memory(memory.id)
        assert stored is not None
        assert aws_key not in stored.content
        assert "[REDACTED" in stored.content


def test_matching_past_mistake_returns_deterministic_warning() -> None:
    with Storage() as storage:
        _record_auth_mistake(storage)

        result = guard_against_past_mistakes(
            storage,
            "Edit authentication middleware to change CSRF validation",
            affected_symbol="authenticate",
        )

        assert result.should_warn
        assert result.used_tokens <= result.budget_tokens
        assert result.warnings[0].reason == "affected symbol matches"
        assert "CSRF validation" in result.warnings[0].corrective_action


def test_stop_suggestions_do_not_persist_without_confirmation() -> None:
    with Storage() as storage:
        suggestion = suggest_stop_decision("Decision: preserve request signatures")
        assert suggestion is not None
        assert suggestion.requires_confirmation

        assert confirm_stop_suggestion(storage, suggestion, confirmed=False) is None
        assert storage.list_memories(("project-shared",)) == []

        memory_id = confirm_stop_suggestion(storage, suggestion, confirmed=True)
        assert memory_id is not None
        assert storage.get_memory(memory_id) is not None
