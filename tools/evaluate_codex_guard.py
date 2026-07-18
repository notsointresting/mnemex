"""Validate and render Codex decision-guard control/treatment evaluation results.

This tool is deliberately a *result validator and report renderer only*. It does
NOT run Codex, makes no network call, and imports nothing from ``mnemex``. The
actual control (no guard) and treatment (Codex Guard Mode) runs are a manual
human step; this tool only checks that a recorded results file is internally
consistent with the checked-in fixtures and then computes bounded, honestly
scoped metrics.

Rejections (nonzero exit):
  * a run missing any required outcome label, or a non-boolean label;
  * duplicate run IDs;
  * a run whose fixture commit does not match its fixture definition;
  * an incomplete or unbalanced control/treatment pair for any fixture task;
  * structural / logical integrity errors (unknown task, bad arm, control-arm
    guard activity, contradictory block labels).

Metrics (computed only when the results validate):
  * seeded violations prevented / seeded violation opportunities;
  * false blocks / (compatible + evolution tasks);
  * successful task completions / total task runs.

The stale-decision task is reported separately (it must warn, not block), and
is intentionally excluded from the seeded-violation and false-block ratios.

Run ``python tools/evaluate_codex_guard.py --self-check`` for an assertion-based
demonstration that the shipped synthetic example validates and that deliberately
malformed variants are rejected.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = ROOT / "benchmarks" / "codex-guard-fixtures"
EXAMPLE_RESULTS = FIXTURES_DIR / "example-results.synthetic.json"

SCHEMA_VERSION = 1
PENDING_COMMIT_SENTINEL = "PENDING_FIXTURE_COMMIT"
ALLOWED_CATEGORIES = ("violation", "compatible", "evolution", "stale")
ALLOWED_ARMS = ("control", "treatment")
REQUIRED_LABELS = (
    "task_completed",
    "decision_preserved",
    "guard_intervened",
    "correct_block",
    "false_block",
    "override_used",
    "final_tests_passed",
)
# In the control arm Mnemex is absent, so no guard activity may be recorded.
CONTROL_FORBIDDEN_TRUE = (
    "guard_intervened",
    "correct_block",
    "false_block",
    "override_used",
)

LIMITS_TEXT = (
    "These metrics come from at most a few runs over five hand-built synthetic "
    "fixtures (six if the optional scoped-invariant task ships). They are a "
    "bounded, illustrative demonstration of the decision guard's effect on "
    "seeded decisions.\n\n"
    "- No statistical-significance claim is made or supported.\n"
    "- No production or real-world prevalence of these violations is implied.\n"
    "- The numbers measure these specific seeded decisions, not general "
    "coding-agent quality or universal token savings.\n"
    "- Control and treatment differ only by Codex Guard Mode and the pre-seeded "
    "brain; model, prompt, fixture commit, and stopping condition are pinned.\n"
    "- Every published number must trace to a checked-in fixture and a recorded "
    "run before it appears in the README or video."
)


@dataclass(frozen=True)
class Fixture:
    """A pre-labeled task definition loaded from ``task-*.json``."""

    task_id: str
    title: str
    category: str
    is_seeded_violation_opportunity: bool
    fixture_commit: str


class FixtureError(RuntimeError):
    """Raised when the checked-in fixture definitions are themselves invalid."""


def load_fixtures(fixtures_dir: Path = FIXTURES_DIR) -> tuple[Fixture, ...]:
    """Load and validate every ``task-*.json`` fixture in ``fixtures_dir``."""
    paths = sorted(fixtures_dir.glob("task-*.json"))
    if not paths:
        raise FixtureError(f"No task-*.json fixtures found in {fixtures_dir}")
    fixtures: list[Fixture] = []
    seen: set[str] = set()
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != SCHEMA_VERSION:
            raise FixtureError(f"{path.name}: unsupported schema_version")
        task_id = data.get("task_id")
        category = data.get("category")
        commit = (data.get("fixture") or {}).get("commit")
        opportunity = data.get("is_seeded_violation_opportunity")
        if not isinstance(task_id, str) or not task_id:
            raise FixtureError(f"{path.name}: task_id must be a non-empty string")
        if task_id in seen:
            raise FixtureError(f"duplicate fixture task_id: {task_id}")
        if category not in ALLOWED_CATEGORIES:
            raise FixtureError(f"{path.name}: category must be one of {ALLOWED_CATEGORIES}")
        if not isinstance(commit, str) or not commit:
            raise FixtureError(f"{path.name}: fixture.commit must be a non-empty string")
        if not isinstance(opportunity, bool):
            raise FixtureError(f"{path.name}: is_seeded_violation_opportunity must be a boolean")
        seen.add(task_id)
        fixtures.append(
            Fixture(
                task_id=task_id,
                title=str(data.get("title") or task_id),
                category=category,
                is_seeded_violation_opportunity=opportunity,
                fixture_commit=commit,
            )
        )
    return tuple(fixtures)


def load_results(path: Path) -> dict[str, Any]:
    """Read a results JSON file. Structural validation happens in ``validate``."""
    return json.loads(path.read_text(encoding="utf-8"))


def validate(
    results: dict[str, Any],
    fixtures: tuple[Fixture, ...],
    *,
    require_pinned_commits: bool = False,
) -> list[str]:
    """Return a list of rejection reasons; an empty list means the file is valid."""
    errors: list[str] = []
    by_id = {f.task_id: f for f in fixtures}

    if results.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must equal 1")
    model = results.get("model")
    if not isinstance(model, str) or not model.strip():
        errors.append("model must be a non-empty string")

    runs = results.get("runs")
    if not isinstance(runs, list) or not runs:
        errors.append("runs must be a non-empty list")
        return errors  # Nothing further is checkable without runs.

    seen_ids: set[str] = set()
    pair_counts: dict[str, dict[str, int]] = {
        f.task_id: {"control": 0, "treatment": 0} for f in fixtures
    }

    for i, run in enumerate(runs):
        where = f"runs[{i}]"
        if not isinstance(run, dict):
            errors.append(f"{where}: must be an object")
            continue

        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            errors.append(f"{where}: run_id must be a non-empty string")
        elif run_id in seen_ids:
            errors.append(f"duplicate run_id: {run_id}")
        else:
            seen_ids.add(run_id)

        task_id = run.get("task_id")
        fixture = by_id.get(task_id) if isinstance(task_id, str) else None
        if fixture is None:
            errors.append(f"{where}: task_id {task_id!r} is not a known fixture")

        arm = run.get("arm")
        if arm not in ALLOWED_ARMS:
            errors.append(f"{where}: arm must be one of {ALLOWED_ARMS}")

        commit = run.get("fixture_commit")
        if not isinstance(commit, str) or not commit:
            errors.append(f"{where}: fixture_commit must be a non-empty string")
        elif fixture is not None and commit != fixture.fixture_commit:
            errors.append(
                f"{where}: fixture_commit {commit!r} does not match fixture "
                f"{fixture.task_id} commit {fixture.fixture_commit!r}"
            )
        if require_pinned_commits and commit == PENDING_COMMIT_SENTINEL:
            errors.append(
                f"{where}: fixture_commit is still the {PENDING_COMMIT_SENTINEL} placeholder"
            )

        labels = run.get("labels")
        if not isinstance(labels, dict):
            errors.append(f"{where}: labels must be an object")
        else:
            for key in REQUIRED_LABELS:
                if key not in labels:
                    errors.append(f"{where}: missing label {key!r}")
                elif not isinstance(labels[key], bool):
                    errors.append(f"{where}: label {key!r} must be a boolean")
            errors.extend(_label_integrity_errors(where, arm, labels))

        if "observations" in run:
            errors.extend(_observation_errors(where, run["observations"]))

        if fixture is not None and arm in ALLOWED_ARMS:
            pair_counts[fixture.task_id][arm] += 1

    for task_id, counts in pair_counts.items():
        control, treatment = counts["control"], counts["treatment"]
        if control == 0 or treatment == 0:
            errors.append(
                f"task {task_id}: incomplete control/treatment pair "
                f"(control={control}, treatment={treatment})"
            )
        elif control != treatment:
            errors.append(
                f"task {task_id}: unbalanced control/treatment counts "
                f"(control={control}, treatment={treatment})"
            )

    return errors


def _label_integrity_errors(where: str, arm: Any, labels: dict[str, Any]) -> list[str]:
    """Flag logically impossible label combinations (not outcome-vs-expectation)."""
    errors: list[str] = []

    def flag(key: str) -> bool | None:
        value = labels.get(key)
        return value if isinstance(value, bool) else None

    guard = flag("guard_intervened")
    if flag("correct_block") and flag("false_block"):
        errors.append(f"{where}: correct_block and false_block cannot both be true")
    for key in ("correct_block", "false_block", "override_used"):
        if flag(key) and guard is False:
            errors.append(f"{where}: {key}=true requires guard_intervened=true")
    if arm == "control":
        for key in CONTROL_FORBIDDEN_TRUE:
            if flag(key):
                errors.append(
                    f"{where}: control arm cannot record {key}=true (no guard present)"
                )
    return errors


def _observation_errors(where: str, obs: Any) -> list[str]:
    """Optional observations must be non-negative numbers when present."""
    errors: list[str] = []
    if not isinstance(obs, dict):
        errors.append(f"{where}: observations must be an object when present")
        return errors
    for key in ("wall_clock_seconds", "context_tokens"):
        if key in obs:
            value = obs[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                errors.append(f"{where}: observation {key!r} must be a non-negative number")
    return errors


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "text": f"{numerator}/{denominator}",
        "rate": round(numerator / denominator, 4) if denominator else None,
    }


def compute_metrics(results: dict[str, Any], fixtures: tuple[Fixture, ...]) -> dict[str, Any]:
    """Compute the plan's bounded metrics. Assumes ``results`` already validated."""
    by_id = {f.task_id: f for f in fixtures}
    runs = results["runs"]
    control = [r for r in runs if r.get("arm") == "control"]
    treatment = [r for r in runs if r.get("arm") == "treatment"]

    def label(run: dict[str, Any], key: str) -> bool:
        return bool(run["labels"][key])

    opportunities = [r for r in treatment if by_id[r["task_id"]].is_seeded_violation_opportunity]
    prevented = [r for r in opportunities if label(r, "decision_preserved")]
    prevented_with_block = [
        r
        for r in opportunities
        if label(r, "decision_preserved") and label(r, "guard_intervened") and label(r, "correct_block")
    ]

    legitimate = [r for r in treatment if by_id[r["task_id"]].category in ("compatible", "evolution")]
    false_blocks = [r for r in legitimate if label(r, "false_block")]

    stale = [r for r in treatment if by_id[r["task_id"]].category == "stale"]
    stale_blocked = [r for r in stale if label(r, "correct_block") or label(r, "false_block")]

    return {
        "seeded_violations_prevented": _ratio(len(prevented), len(opportunities)),
        "seeded_violations_prevented_with_correct_block": _ratio(
            len(prevented_with_block), len(opportunities)
        ),
        "false_blocks_on_legitimate_change": _ratio(len(false_blocks), len(legitimate)),
        "stale_task_correctly_advisory": _ratio(len(stale) - len(stale_blocked), len(stale)),
        "task_completions_overall": _ratio(
            sum(label(r, "task_completed") for r in runs), len(runs)
        ),
        "task_completions_control": _ratio(
            sum(label(r, "task_completed") for r in control), len(control)
        ),
        "task_completions_treatment": _ratio(
            sum(label(r, "task_completed") for r in treatment), len(treatment)
        ),
    }


def _metric_row(name: str, metric: dict[str, Any]) -> str:
    rate = "—" if metric["rate"] is None else f"{metric['rate'] * 100:.1f}%"
    return f"| {name} | {metric['text']} | {rate} |"


def render_markdown(
    results: dict[str, Any], fixtures: tuple[Fixture, ...], metrics: dict[str, Any]
) -> str:
    """Render a stable Markdown report from validated results and metrics."""
    by_id = {f.task_id: f for f in fixtures}
    lines: list[str] = ["# Codex Decision-Guard Evaluation — Rendered Results", ""]
    if results.get("synthetic_example"):
        lines += [
            "> **SYNTHETIC EXAMPLE — NOT REAL OUTCOMES.** These numbers come from a",
            "> hand-written template used to exercise the validator. They must never",
            "> be published as measured Codex results.",
            "",
        ]
    lines += [
        f"- Model: `{results.get('model')}`",
        f"- Runs: {len(results['runs'])}",
        f"- Fixture tasks: {len(fixtures)}",
        "",
        "## Validation",
        "",
        "PASSED — results are internally consistent with the checked-in fixtures.",
        "",
        "## Primary metrics",
        "",
        "| Metric | Value | Rate |",
        "|---|---:|---:|",
        _metric_row("Seeded violations prevented / opportunities", metrics["seeded_violations_prevented"]),
        _metric_row(
            "…with a correct fresh-cited block",
            metrics["seeded_violations_prevented_with_correct_block"],
        ),
        _metric_row(
            "False blocks / (compatible + evolution)", metrics["false_blocks_on_legitimate_change"]
        ),
        _metric_row(
            "Stale task correctly advisory (not blocked)", metrics["stale_task_correctly_advisory"]
        ),
        _metric_row("Task completions / total runs", metrics["task_completions_overall"]),
        _metric_row("Task completions (control)", metrics["task_completions_control"]),
        _metric_row("Task completions (treatment)", metrics["task_completions_treatment"]),
        "",
        "## Runs",
        "",
        "| run_id | task | category | arm | completed | preserved | guard | correct block | false block | override | tests |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for run in results["runs"]:
        fixture = by_id[run["task_id"]]
        labels = run["labels"]

        def yn(key: str, _labels: dict[str, Any] = labels) -> str:
            return "yes" if _labels[key] else "no"

        lines.append(
            "| "
            + " | ".join(
                [
                    run["run_id"],
                    run["task_id"],
                    fixture.category,
                    str(run["arm"]),
                    yn("task_completed"),
                    yn("decision_preserved"),
                    yn("guard_intervened"),
                    yn("correct_block"),
                    yn("false_block"),
                    yn("override_used"),
                    yn("final_tests_passed"),
                ]
            )
            + " |"
        )
    lines += ["", "## Limits", "", LIMITS_TEXT, ""]
    return "\n".join(lines)


def render_json(
    results: dict[str, Any], fixtures: tuple[Fixture, ...], metrics: dict[str, Any]
) -> str:
    """Render a stable JSON summary from validated results and metrics."""
    payload = {
        "valid": True,
        "synthetic_example": bool(results.get("synthetic_example")),
        "model": results.get("model"),
        "run_count": len(results["runs"]),
        "fixture_count": len(fixtures),
        "metrics": metrics,
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _emit_rejection(errors: list[str], fmt: str) -> None:
    print(f"REJECTED: {len(errors)} problem(s) found.", file=sys.stderr)
    if fmt == "json":
        print(json.dumps({"valid": False, "errors": errors}, indent=2, sort_keys=True))
    else:
        print("# Codex Decision-Guard Evaluation — REJECTED\n")
        print(f"{len(errors)} problem(s) found:\n")
        for error in errors:
            print(f"- {error}")


def _self_check() -> int:
    """Assertion-based proof: the shipped example validates; malformed ones fail."""
    fixtures = load_fixtures()
    example = load_results(EXAMPLE_RESULTS)

    base_errors = validate(example, fixtures)
    assert not base_errors, f"shipped example must validate, got: {base_errors}"

    metrics = compute_metrics(example, fixtures)
    assert (
        metrics["seeded_violations_prevented"]["denominator"] >= 1
    ), "example must contain at least one seeded-violation opportunity"

    mutations: list[tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]] = [
        ("missing label", _mutate_drop_label),
        ("non-boolean label", _mutate_non_boolean_label),
        ("duplicate run_id", _mutate_duplicate_run_id),
        ("mismatched fixture commit", _mutate_break_commit),
        ("incomplete control/treatment pair", _mutate_drop_treatment_run),
        ("control-arm guard activity", _mutate_pollute_control),
        ("contradictory block labels", _mutate_contradict_block),
        ("unknown task_id", _mutate_unknown_task),
    ]
    for name, mutate in mutations:
        broken = mutate(copy.deepcopy(example))
        assert validate(broken, fixtures), f"expected rejection for: {name}"

    pinned_errors = validate(example, fixtures, require_pinned_commits=True)
    assert pinned_errors, "placeholder commits must be rejected under --require-pinned-commits"

    print("self-check: OK")
    print(f"  example validates:            {EXAMPLE_RESULTS.name}")
    print(f"  metrics computed:             {metrics['seeded_violations_prevented']['text']} seeded violations prevented")
    print(f"  malformed variants rejected:  {len(mutations)}")
    print("  placeholder-commit strictness: rejected under --require-pinned-commits")
    return 0


def _mutate_drop_label(data: dict[str, Any]) -> dict[str, Any]:
    del data["runs"][0]["labels"]["decision_preserved"]
    return data


def _mutate_non_boolean_label(data: dict[str, Any]) -> dict[str, Any]:
    data["runs"][0]["labels"]["task_completed"] = "yes"
    return data


def _mutate_duplicate_run_id(data: dict[str, Any]) -> dict[str, Any]:
    data["runs"][1]["run_id"] = data["runs"][0]["run_id"]
    return data


def _mutate_break_commit(data: dict[str, Any]) -> dict[str, Any]:
    data["runs"][0]["fixture_commit"] = "0000000000000000000000000000000000000000"
    return data


def _mutate_drop_treatment_run(data: dict[str, Any]) -> dict[str, Any]:
    index = next(i for i, run in enumerate(data["runs"]) if run["arm"] == "treatment")
    del data["runs"][index]
    return data


def _mutate_pollute_control(data: dict[str, Any]) -> dict[str, Any]:
    index = next(i for i, run in enumerate(data["runs"]) if run["arm"] == "control")
    data["runs"][index]["labels"]["guard_intervened"] = True
    return data


def _mutate_contradict_block(data: dict[str, Any]) -> dict[str, Any]:
    index = next(i for i, run in enumerate(data["runs"]) if run["arm"] == "treatment")
    data["runs"][index]["labels"]["correct_block"] = True
    data["runs"][index]["labels"]["false_block"] = True
    return data


def _mutate_unknown_task(data: dict[str, Any]) -> dict[str, Any]:
    data["runs"][0]["task_id"] = "no-such-task"
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "results",
        nargs="?",
        type=Path,
        help="Path to a results JSON file to validate and render.",
    )
    parser.add_argument("--fixtures", type=Path, default=FIXTURES_DIR, help="Fixture directory.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output", type=Path, help="Write the report here instead of stdout.")
    parser.add_argument(
        "--require-pinned-commits",
        action="store_true",
        help=f"Reject the {PENDING_COMMIT_SENTINEL} placeholder; use for the real submission run.",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Run an assertion-based self-test (validates the shipped example and "
        "proves malformed variants are rejected); ignores the results argument.",
    )
    args = parser.parse_args(argv)

    if args.self_check:
        return _self_check()
    if args.results is None:
        parser.error("a results file is required (or use --self-check)")

    fixtures = load_fixtures(args.fixtures)
    results = load_results(args.results)
    errors = validate(results, fixtures, require_pinned_commits=args.require_pinned_commits)
    if errors:
        _emit_rejection(errors, args.format)
        return 2

    metrics = compute_metrics(results, fixtures)
    if args.format == "json":
        output = render_json(results, fixtures, metrics)
    else:
        output = render_markdown(results, fixtures, metrics)
    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(f"Wrote {args.format} report to {args.output}")
    else:
        print(output, end="" if output.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
