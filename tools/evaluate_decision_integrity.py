"""Run the deterministic, offline decision-integrity fixture evaluation.

This tool deliberately measures only the local mechanisms exercised by the
fixture set. It is not an evaluation of remote semantic judgment, real-world
architectural prevalence, or coding-agent task completion.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mnemex.anchors import check_freshness, remember  # noqa: E402
from mnemex.constraints import enforce_constraints  # noqa: E402
from mnemex.lifecycle import reconcile_stale_decision, supersede_decision  # noqa: E402
from mnemex.mistakes import guard_against_past_mistakes, record_mistake  # noqa: E402
from mnemex.storage import Node, Storage  # noqa: E402


DEFAULT_FIXTURES = ROOT / "benchmarks" / "decision-integrity-fixtures.json"


@dataclass(frozen=True)
class CaseResult:
    id: str
    category: str
    expected: str
    actual: str
    passed: bool


def load_cases(path: Path = DEFAULT_FIXTURES) -> tuple[dict[str, Any], ...]:
    """Load and validate the versioned, repository-local evaluation fixtures."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported fixture schema version")
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 25:
        raise ValueError("The evaluation requires exactly 25 labeled cases")
    ids = [case.get("id") for case in cases if isinstance(case, dict)]
    if len(ids) != len(cases) or len(set(ids)) != len(cases):
        raise ValueError("Every fixture case requires a unique id")
    return tuple(cases)


def evaluate(cases: tuple[dict[str, Any], ...]) -> tuple[CaseResult, ...]:
    """Evaluate every fixture in a fresh in-memory database."""
    return tuple(_evaluate_case(case) for case in cases)


def summarize(results: tuple[CaseResult, ...]) -> dict[str, Any]:
    """Return deterministic, fixture-bounded metrics suitable for release notes."""
    totals = Counter(result.category for result in results)
    passed = Counter(result.category for result in results if result.passed)
    return {
        "fixture_cases": len(results),
        "passed": sum(result.passed for result in results),
        "failed": sum(not result.passed for result in results),
        "by_category": {
            category: {"passed": passed[category], "total": totals[category]}
            for category in sorted(totals)
        },
        "scope": (
            "Offline synthetic fixtures exercising local constraints, append-only "
            "supersession, anchor freshness, and past-mistake warnings."
        ),
        "not_measured": (
            "Remote semantic-model precision/recall, production prevalence, "
            "agent task completion, and end-to-end user outcomes."
        ),
    }


def render_markdown(results: tuple[CaseResult, ...]) -> str:
    """Render a stable report; no timestamps keep repeated output byte-identical."""
    summary = summarize(results)
    lines = [
        "# Decision-Integrity Fixture Evaluation",
        "",
        "## Scope",
        "",
        summary["scope"],
        "",
        "Every case runs against a new in-memory SQLite database through the "
        "local Mnemex APIs. No network, embeddings, or semantic provider is used.",
        "",
        "## Result",
        "",
        f"**{summary['passed']}/{summary['fixture_cases']} cases passed** "
        f"({summary['failed']} failed).",
        "",
        "| Category | Passed | Total |",
        "|---|---:|---:|",
    ]
    for category, counts in summary["by_category"].items():
        lines.append(
            f"| {category.replace('_', ' ')} | {counts['passed']} | "
            f"{counts['total']} |"
        )
    lines.extend(["", "## Cases", "", "| ID | Category | Expected | Actual | Pass |", "|---|---|---|---|---|"])
    for result in results:
        lines.append(
            f"| {result.id} | {result.category.replace('_', ' ')} | "
            f"{result.expected} | {result.actual} | "
            f"{'yes' if result.passed else 'no'} |"
        )
    lines.extend(
        [
            "",
            "## Limits",
            "",
            summary["not_measured"],
            "",
            "The separate three-repository token figures are **context-delivery "
            "microbenchmarks**. They must not be read as decision-integrity "
            "accuracy, agent-quality, or universal token-savings claims.",
            "",
            "## Reproduce",
            "",
            "```text",
            "python tools/evaluate_decision_integrity.py --format markdown",
            "python tools/evaluate_decision_integrity.py --format json",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _evaluate_case(case: dict[str, Any]) -> CaseResult:
    category = _required_string(case, "category")
    if category in {"direct_contradiction", "compatible_change"}:
        return _constraint_case(case)
    if category == "legitimate_supersession":
        return _supersession_case(case)
    if category in {"freshness", "stale_orphaned"}:
        return _freshness_case(case)
    if category == "repeated_mistake":
        return _mistake_case(case)
    raise ValueError(f"Unsupported fixture category: {category}")


def _constraint_case(case: dict[str, Any]) -> CaseResult:
    case_id = _required_string(case, "id")
    expected_count = case.get("expected_violations")
    if not isinstance(expected_count, int):
        raise ValueError(f"{case_id}: expected_violations must be an integer")
    phrase = _required_string(case, "phrase")
    with Storage() as storage:
        remember(
            storage,
            f"Do not introduce {phrase}.",
            memory_id=f"{case_id}-decision",
            tags=f"constraint:forbidden:{phrase}",
        )
        actual_count = len(enforce_constraints(storage, _required_string(case, "patch")))
    return _result(case, str(expected_count), str(actual_count), actual_count == expected_count)


def _supersession_case(case: dict[str, Any]) -> CaseResult:
    case_id = _required_string(case, "id")
    with Storage() as storage:
        node = _node(case_id, "hash-1")
        storage.upsert_node(node)
        prior = remember(storage, _required_string(case, "old"), anchor=node.id, memory_id=f"{case_id}-prior")
        successor = supersede_decision(storage, prior.id, _required_string(case, "new"), successor_id=f"{case_id}-successor")
        prior_state = storage.get_decision_metadata(prior.id)
        successor_state = storage.get_decision_metadata(successor.id)
        actual = "superseded" if (
            prior_state is not None
            and successor_state is not None
            and prior_state.status == "superseded"
            and successor_state.status == "active"
            and successor_state.supersedes_memory_id == prior.id
            and reconcile_stale_decision(storage, prior.id, node.name, "- old\n+ new") == "superseded"
        ) else "not_superseded"
    return _result(case, "superseded", actual, actual == "superseded")


def _freshness_case(case: dict[str, Any]) -> CaseResult:
    case_id = _required_string(case, "id")
    expected = _required_string(case, "state")
    with Storage() as storage:
        node = _node(case_id, "hash-1")
        storage.upsert_node(node)
        memory = remember(storage, f"Decision anchored to {node.name}.", anchor=node.id, memory_id=f"{case_id}-decision")
        if expected == "stale":
            storage.upsert_node(_node(case_id, "hash-2"))
        elif expected == "orphaned":
            storage.delete_node(node.id)
        elif expected != "fresh":
            raise ValueError(f"{case_id}: unsupported freshness state {expected}")
        reports = check_freshness(storage, memory_id=memory.id)
        actual = reports[0].status.value if len(reports) == 1 else "missing"
    return _result(case, expected, actual, actual == expected)


def _mistake_case(case: dict[str, Any]) -> CaseResult:
    case_id = _required_string(case, "id")
    symbol = _required_string(case, "symbol")
    with Storage() as storage:
        record_mistake(
            storage,
            failure_signature=_required_string(case, "failure"),
            root_cause="A previous edit bypassed an established safeguard.",
            affected_symbol=symbol,
            corrective_action="Preserve the safeguard and add a regression test.",
            memory_id=f"{case_id}-mistake",
        )
        warning = guard_against_past_mistakes(storage, _required_string(case, "action"), affected_symbol=symbol)
        actual = "warned" if warning.should_warn else "not_warned"
    return _result(case, "warned", actual, actual == "warned")


def _node(case_id: str, content_hash: str) -> Node:
    return Node(
        id=f"{case_id}-node",
        type="function",
        name=f"symbol_{case_id.replace('-', '_')}",
        file=f"src/{case_id}.py",
        line_start=1,
        content_hash=content_hash,
        language="python",
    )


def _result(case: dict[str, Any], expected: str, actual: str, passed: bool) -> CaseResult:
    return CaseResult(_required_string(case, "id"), _required_string(case, "category"), expected, actual, passed)


def _required_string(case: dict[str, Any], key: str) -> str:
    value = case.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Fixture field {key!r} must be a non-empty string")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    results = evaluate(load_cases(args.fixtures))
    output = render_markdown(results) if args.format == "markdown" else json.dumps(
        {"summary": summarize(results), "cases": [asdict(item) for item in results]}, indent=2, sort_keys=True
    ) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
