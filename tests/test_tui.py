from __future__ import annotations

from mnemex.anchors import remember
from mnemex.tui import build_dashboard, render_dashboard
from mnemex.storage import Storage


def test_dashboard_reports_local_decision_health() -> None:
    with Storage() as storage:
        remember(storage, "Use UTC timestamps")

        summary = build_dashboard(storage)

        assert summary.memories == 1
        assert summary.active == 1
        assert summary.decision_health_percent == 100
        assert summary.review_candidates == 1
        rendered = render_dashboard(summary)
        assert "active decisions" in rendered
        assert "decision health" in rendered
        assert "pending conflicts" in rendered
