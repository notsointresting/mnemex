"""A dependency-free terminal dashboard for local mnemex state."""

from __future__ import annotations

from dataclasses import dataclass

from mnemex.anchors import check_freshness
from mnemex.conflicts import list_conflicts
from mnemex.reviews import list_review_candidates
from mnemex.storage import Storage

__all__ = ["DashboardSummary", "build_dashboard", "render_dashboard"]


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    memories: int
    active: int
    fresh: int
    stale: int
    orphaned: int
    guard_runs: int
    blocked_runs: int
    review_candidates: int
    conflict_count: int
    decision_health_percent: int
    guard_payload_tokens: int
    redaction_audit_records: int
    last_verified_at: str | None


def build_dashboard(storage: Storage) -> DashboardSummary:
    scopes = ("agent-private", "project-shared", "user-global")
    memories = storage.list_memories(scopes)
    freshness = check_freshness(storage, scopes=scopes)
    statuses = [
        storage.get_decision_metadata(memory.id).status
        for memory in memories
        if storage.get_decision_metadata(memory.id) is not None
    ]
    guard_runs, blocked_runs, guard_payload_tokens = storage.connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(blocked), 0), COALESCE(SUM(payload_tokens), 0) FROM guard_runs"
    ).fetchone()
    fresh = sum(report.status.value == "fresh" for report in freshness)
    stale = sum(report.status.value == "stale" for report in freshness)
    orphaned = sum(report.status.value == "orphaned" for report in freshness)
    anchored = fresh + stale + orphaned
    health = 100 if anchored == 0 else round(fresh * 100 / anchored)
    redaction_audits = storage.connection.execute(
        "SELECT COUNT(*) FROM persistence_redaction_audit"
    ).fetchone()[0]
    last_verified = storage.connection.execute(
        "SELECT MAX(last_verified) FROM memories"
    ).fetchone()[0]
    return DashboardSummary(
        memories=len(memories),
        active=statuses.count("active"),
        fresh=fresh,
        stale=stale,
        orphaned=orphaned,
        guard_runs=guard_runs,
        blocked_runs=blocked_runs,
        review_candidates=len(list_review_candidates(storage)),
        conflict_count=len(list_conflicts(storage).conflicts),
        decision_health_percent=health,
        guard_payload_tokens=guard_payload_tokens,
        redaction_audit_records=redaction_audits,
        last_verified_at=last_verified,
    )


def render_dashboard(summary: DashboardSummary) -> str:
    """Render stable, script-friendly terminal output without a TUI dependency."""
    rows = (
        ("memories", summary.memories),
        ("active decisions", summary.active),
        ("decision health", f"{summary.decision_health_percent}%"),
        ("fresh anchors", summary.fresh),
        ("stale anchors", summary.stale),
        ("orphaned anchors", summary.orphaned),
        ("guard runs", summary.guard_runs),
        ("blocked runs", summary.blocked_runs),
        ("guard payload tokens", summary.guard_payload_tokens),
        ("pending conflicts", summary.conflict_count),
        ("review candidates", summary.review_candidates),
        ("redaction audit records", summary.redaction_audit_records),
        ("last verified", summary.last_verified_at or "never"),
    )
    width = max(len(label) for label, _ in rows)
    return "\n".join(f"{label.ljust(width)}  {value}" for label, value in rows)
