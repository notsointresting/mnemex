from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "evaluate_decision_integrity.py"


def _tool_module():
    spec = importlib.util.spec_from_file_location("hackathon_evaluation", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_fixture_evaluation_has_25_labeled_passing_cases() -> None:
    tool = _tool_module()

    results = tool.evaluate(tool.load_cases())
    summary = tool.summarize(results)

    assert summary["fixture_cases"] == 25
    assert summary["passed"] == 25
    assert summary["failed"] == 0
    assert summary["by_category"] == {
        "compatible_change": {"passed": 5, "total": 5},
        "direct_contradiction": {"passed": 5, "total": 5},
        "freshness": {"passed": 2, "total": 2},
        "legitimate_supersession": {"passed": 4, "total": 4},
        "repeated_mistake": {"passed": 5, "total": 5},
        "stale_orphaned": {"passed": 4, "total": 4},
    }


def test_cli_json_is_deterministic_and_declares_its_limits() -> None:
    first = subprocess.run(
        [sys.executable, str(TOOL), "--format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    second = subprocess.run(
        [sys.executable, str(TOOL), "--format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert first.stdout == second.stdout
    report = json.loads(first.stdout)
    assert report["summary"]["passed"] == 25
    assert "Remote semantic-model precision/recall" in report["summary"]["not_measured"]


def test_markdown_calls_token_figures_context_delivery_microbenchmarks() -> None:
    tool = _tool_module()
    report = tool.render_markdown(tool.evaluate(tool.load_cases()))

    assert "context-delivery microbenchmarks" in report
    assert "No network, embeddings, or semantic provider" in report
