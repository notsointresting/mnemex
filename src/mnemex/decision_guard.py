"""Proposed-change evaluation backed by local evidence and an optional judge."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from mnemex.evidence import (
    DEFAULT_EVIDENCE_TOKEN_CAP,
    EvidenceBundle,
    build_guard_evidence,
)
from mnemex.judge import SemanticJudge, SemanticJudgment, Verdict
from mnemex.storage import GuardEvidence, GuardOverride, GuardRun, Storage

__all__ = [
    "ProposedChangeResult",
    "check_proposed_change",
    "override_decision_guard",
]


@dataclass(frozen=True, slots=True)
class ProposedChangeResult:
    """An auditable decision-guard result for one proposed code change."""

    run_id: str
    evidence: EvidenceBundle
    judgment: SemanticJudgment
    blocked: bool
    recommended_action: str

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "path": self.evidence.path,
            "verdict": self.judgment.verdict.value,
            "confidence": self.judgment.confidence,
            "explanation": self.judgment.explanation,
            "recommended_action": self.recommended_action,
            "blocked": self.blocked,
            "decision_ids": list(self.judgment.evidence_ids),
            "evidence": [
                {
                    "memory_id": item.memory_id,
                    "freshness": item.freshness,
                    "source": item.source,
                    "rank": item.rank,
                }
                for item in self.evidence.items
            ],
            "payload_summary": {
                "tokens": self.evidence.used_tokens,
                "budget_tokens": self.evidence.budget_tokens,
                "payload_hash": _payload_hash(self.evidence),
            },
        }


def check_proposed_change(
    storage: Storage,
    path: str,
    patch_summary: str,
    *,
    judge: SemanticJudge | None = None,
    scopes: tuple[str, ...] = ("project-shared",),
    max_evidence_tokens: int = DEFAULT_EVIDENCE_TOKEN_CAP,
    enforce_constraints: bool = False,
) -> ProposedChangeResult:
    """Evaluate a proposed edit without allowing a provider to mutate memory.

    Semantic verdicts preserve the default hybrid policy.  Callers that have
    explicitly opted into deterministic constraints can request local blocking
    for fresh tagged decisions without enabling a remote provider.
    """
    evidence = build_guard_evidence(
        storage,
        path,
        patch_summary,
        scopes=scopes,
        max_tokens=max_evidence_tokens,
    )
    payload = evidence.payload
    judgment = (
        judge.evaluate(payload)
        if judge is not None
        else SemanticJudgment(
            verdict=Verdict.UNAVAILABLE,
            confidence=0.0,
            explanation="Semantic judge is not enabled; local evidence only.",
            payload_tokens=evidence.used_tokens,
        )
    )
    if enforce_constraints:
        from mnemex.constraints import enforce_constraints as find_violations

        violations = find_violations(storage, patch_summary, scopes=scopes)
        if violations:
            violated_ids = tuple(sorted({item.memory_id for item in violations}))
            judgment = SemanticJudgment(
                verdict=Verdict.CONTRADICTION,
                confidence=1.0,
                explanation=(
                    "Deterministic constraint violation: "
                    + "; ".join(item.message for item in violations)
                ),
                evidence_ids=violated_ids,
                model="deterministic-constraints",
                payload_tokens=evidence.used_tokens,
            )
    fresh_ids = {
        item.memory_id for item in evidence.items if item.freshness == "fresh"
    }
    blocking_ids = fresh_ids.intersection(judgment.evidence_ids)
    blocked = judgment.blocks_change and bool(blocking_ids)
    recommended_action = _recommended_action(judgment.verdict, blocked)
    run_id = str(uuid4())
    stored_run = storage.record_guard_run(
        GuardRun(
            id=run_id,
            path=evidence.path,
            patch_summary=evidence.patch_summary,
            provider=(
                "deterministic-constraints"
                if enforce_constraints and judgment.model == "deterministic-constraints"
                else "openai" if judge is not None else "local"
            ),
            model=judgment.model or "none",
            payload_hash=_payload_hash(evidence),
            payload_tokens=evidence.used_tokens,
            verdict=judgment.verdict.value,
            confidence=judgment.confidence,
            explanation=judgment.explanation,
            recommended_action=recommended_action,
            blocked=blocked,
            created_at=_utc_timestamp(),
        )
    )
    for item in evidence.items:
        storage.record_guard_evidence(
            GuardEvidence(
                guard_run_id=stored_run.id,
                memory_id=item.memory_id,
                rank=item.rank,
                freshness=item.freshness,
            )
        )
    return ProposedChangeResult(
        run_id=stored_run.id,
        evidence=evidence,
        judgment=judgment,
        blocked=blocked,
        recommended_action=recommended_action,
    )


def override_decision_guard(
    storage: Storage,
    run_id: str,
    *,
    actor: str,
    reason: str,
) -> GuardOverride:
    """Persist the explicit human or agent override of a guard result."""
    return storage.record_guard_override(
        run_id,
        actor=actor,
        reason=reason,
        timestamp=_utc_timestamp(),
    )


def _recommended_action(verdict: Verdict, blocked: bool) -> str:
    if blocked:
        return "Do not apply this change without an explicit recorded override."
    if verdict is Verdict.CONTRADICTION:
        return "Review the cited decision before applying this change."
    if verdict is Verdict.SUPERSEDES:
        return "Reconcile the cited decision before applying this change."
    if verdict is Verdict.UNAVAILABLE:
        return "Proceed with local evidence; semantic judgment is unavailable."
    if verdict is Verdict.UNCERTAIN:
        return "Review the evidence manually before applying this change."
    return "The proposed change is compatible with the cited decisions."


def _payload_hash(evidence: EvidenceBundle) -> str:
    return hashlib.sha256(evidence.payload.encode("utf-8")).hexdigest()


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
