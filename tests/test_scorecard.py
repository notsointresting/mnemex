"""Keep README scorecard figures tied to checked-in evaluator replay."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SCORECARD = ROOT / "benchmarks" / "results" / "codex-guard-scorecard.json"
EVALUATOR_PATH = ROOT / "tools" / "evaluate_codex_guard.py"

_spec = importlib.util.spec_from_file_location("evaluate_codex_guard", EVALUATOR_PATH)
assert _spec is not None and _spec.loader is not None
evaluator = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = evaluator
_spec.loader.exec_module(evaluator)


def _scorecard_rows(readme: str) -> dict[str, str]:
    match = re.search(
        r"<!-- codex-guard-scorecard:start -->(.*?)<!-- codex-guard-scorecard:end -->",
        readme,
        flags=re.DOTALL,
    )
    assert match, "README must contain the bounded Codex Guard scorecard"
    return {
        name: value
        for name, value in re.findall(
            r"^\| ([^|]+) \| ([^|]+) \|$", match.group(1), re.MULTILINE
        )
        if name != "Metric"
    }


def test_readme_scorecard_matches_checked_in_replay() -> None:
    scorecard = json.loads(SCORECARD.read_text(encoding="utf-8"))
    readme = README.read_text(encoding="utf-8")
    rows = _scorecard_rows(readme)
    assert scorecard["valid"] is True
    assert scorecard["synthetic_example"] is True
    assert "not a live-agent outcome claim" in scorecard["disclaimer"]

    source = ROOT / scorecard["source_results"]
    source_results = evaluator.load_results(source)
    fixtures = evaluator.load_fixtures()
    assert evaluator.validate(source_results, fixtures) == []
    computed = evaluator.compute_metrics(source_results, fixtures)
    metrics = scorecard["metrics"]
    for name in (
        "seeded_violations_prevented",
        "false_blocks_on_legitimate_change",
        "stale_task_correctly_advisory",
    ):
        assert metrics[name] == computed[name]
    assert rows["Decision violations caught"] == computed["seeded_violations_prevented"]["text"]
    assert rows["False blocks on legitimate evolution"] == computed["false_blocks_on_legitimate_change"]["text"]
    assert rows["Stale decisions correctly advisory"] == computed["stale_task_correctly_advisory"]["text"]

    observed = [
        run["observations"]["context_tokens"]
        for run in source_results["runs"]
        if run["arm"] == "treatment"
        and isinstance(run.get("observations"), dict)
        and "context_tokens" in run["observations"]
    ]
    tokens = scorecard["observed_treatment_context_tokens"]
    assert tokens["observation_count"] == len(observed)
    assert tokens["average"] == sum(observed) / len(observed)
    from mnemex.evidence import DEFAULT_EVIDENCE_TOKEN_CAP

    assert tokens["cap"] == DEFAULT_EVIDENCE_TOKEN_CAP
    assert rows["Average recorded treatment context tokens / cap"] == f"{tokens['average']:g}/{tokens['cap']} ({tokens['observation_count']} observation)"
    assert scorecard["reproduce_command"] in readme
