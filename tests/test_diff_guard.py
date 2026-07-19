"""Tests for the staged/file-diff decision gate (``mnemex check-diff``).

These cover the Phase 3 contract in the final-sprint plan section 8.7:
empty/blocked/compatible diffs, multi-file aggregation, secret redaction,
bounded oversized diffs, robust parsing (binary/rename/delete/CRLF/quoted/
escaping paths), non-shell git acquisition, the no-reindex ordering rule, and
core/vector verdict parity. Every judgement is delegated to the existing
proposed-change guard; this module only bounds, splits, and aggregates.
"""

from __future__ import annotations

import json
import subprocess

from mnemex.anchors import remember
from mnemex.diff_guard import (
    DIFF_TRUNCATED_WARNING,
    FRESHNESS_WARNING,
    MAX_TOTAL_DIFF_BYTES,
    acquire_staged_diff,
    check_diff,
    split_unified_diff,
)
from mnemex.storage import Node, Storage

FORBIDDEN = "redis-backed server sessions"


def _seed_auth_constraint(storage: Storage, *, content_hash: str = "auth-hash") -> str:
    """Anchor a fresh forbidden-phrase constraint to ``src/auth.py``."""
    node = Node(
        id="auth-node",
        type="function",
        name="authenticate",
        file="src/auth.py",
        line_start=1,
        content_hash=content_hash,
        language="python",
    )
    storage.upsert_node(node)
    return remember(
        storage,
        "Authentication must remain stateless.",
        anchor=node.id,
        tags=f"constraint:forbidden:{FORBIDDEN}",
    ).id


def _diff_for(path: str, added_line: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "index 1111111..2222222 100644\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,2 +1,3 @@\n"
        " def authenticate(token):\n"
        f"+    {added_line}\n"
        "     return verify(token)\n"
    )


def test_empty_diff_returns_zero_and_empty_report() -> None:
    with Storage() as storage:
        report = check_diff(storage, "")
    assert report.exit_code() == 0
    assert report.files_seen == 0
    assert report.results == ()
    assert FRESHNESS_WARNING in report.warnings


def test_seeded_contradiction_returns_exit_two() -> None:
    with Storage() as storage:
        decision_id = _seed_auth_constraint(storage)
        report = check_diff(
            storage,
            _diff_for("src/auth.py", f"# introduce {FORBIDDEN}"),
            enforce_constraints=True,
        )
    assert report.exit_code() == 2
    assert report.blocked is True
    assert report.blocked_files == ("src/auth.py",)
    result = report.results[0]
    assert result.status == "checked"
    assert result.disposition == "blocked"
    assert result.verdict == "contradiction"
    assert decision_id in result.decision_ids


def test_compatible_diff_returns_zero() -> None:
    with Storage() as storage:
        _seed_auth_constraint(storage)
        report = check_diff(
            storage,
            _diff_for("src/auth.py", "# improve token verification only"),
            enforce_constraints=True,
        )
    assert report.exit_code() == 0
    assert report.blocked is False
    assert "src/auth.py" in report.advisory_files


def test_multi_file_aggregates_block_and_compatible() -> None:
    compatible = (
        "diff --git a/src/util.py b/src/util.py\n"
        "index 3333333..4444444 100644\n"
        "--- a/src/util.py\n"
        "+++ b/src/util.py\n"
        "@@ -1 +1,2 @@\n"
        " x = 1\n"
        "+y = 2\n"
    )
    with Storage() as storage:
        _seed_auth_constraint(storage)
        blocking = _diff_for("src/auth.py", f"# add {FORBIDDEN}")
        report = check_diff(storage, blocking + compatible, enforce_constraints=True)
    assert report.exit_code() == 2
    assert report.blocked is True
    assert report.files_seen == 2
    assert "src/auth.py" in report.blocked_files
    assert "src/util.py" in report.advisory_files


def test_secret_text_absent_from_report_and_payload() -> None:
    secret = "p8ssw0rd-not-a-real-secret-42"
    with Storage() as storage:
        _seed_auth_constraint(storage)
        report = check_diff(
            storage,
            _diff_for("src/auth.py", f'password = "{secret}"'),
            enforce_constraints=True,
        )
        rendered_json = json.dumps(report.as_dict())
        rendered_md = report.render_markdown()
    assert secret not in rendered_json
    assert secret not in rendered_md
    assert report.redaction_count >= 1


def test_oversized_diff_is_bounded_deterministically() -> None:
    header = (
        "diff --git a/src/big.py b/src/big.py\n"
        "index 5555555..6666666 100644\n"
        "--- a/src/big.py\n"
        "+++ b/src/big.py\n"
        "@@ -1 +1,200000 @@\n"
    )
    body = "\n".join(f"+filler line {index}" for index in range(200000))
    oversized = header + body
    assert len(oversized.encode("utf-8")) > MAX_TOTAL_DIFF_BYTES
    with Storage() as storage:
        report = check_diff(storage, oversized)
    assert DIFF_TRUNCATED_WARNING in report.warnings
    assert report.blocked is False


def test_binary_diff_is_advisory_skip() -> None:
    diff = (
        "diff --git a/assets/img.png b/assets/img.png\n"
        "index 1111111..2222222 100644\n"
        "Binary files a/assets/img.png and b/assets/img.png differ\n"
    )
    with Storage() as storage:
        report = check_diff(storage, diff)
    assert report.exit_code() == 0
    assert report.results[0].status == "binary-skipped"
    assert report.results[0].disposition == "advisory"


def test_rename_delete_crlf_quoted_paths_parse_safely() -> None:
    rename = (
        "diff --git a/old.py b/new.py\n"
        "similarity index 100%\n"
        "rename from old.py\n"
        "rename to new.py\n"
    )
    delete = (
        "diff --git a/gone.py b/gone.py\n"
        "deleted file mode 100644\n"
        "index 1111111..0000000\n"
        "--- a/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-x = 1\n"
    )
    crlf = _diff_for("src/auth.py", "# harmless comment").replace("\n", "\r\n")
    quoted = (
        'diff --git "a/space file.py" "b/space file.py"\n'
        '--- "a/space file.py"\n'
        '+++ "b/space file.py"\n'
        "@@ -1 +1,2 @@\n"
        " a = 1\n"
        "+b = 2\n"
    )
    files = split_unified_diff(rename + delete + crlf + quoted)
    assert any(item.is_rename for item in files)
    assert any(item.is_delete for item in files)
    assert any(
        "space file.py" in (item.old_path or "", item.new_path or "")
        for item in files
    )


def test_escaping_and_drive_paths_are_rejected_safely() -> None:
    traversal = (
        "diff --git a/../../etc/passwd b/../../etc/passwd\n"
        "--- a/../../etc/passwd\n"
        "+++ b/../../etc/passwd\n"
        "@@ -1 +1,2 @@\n"
        " root\n"
        "+injected\n"
    )
    drive = (
        "diff --git a/C:/Windows/secret.py b/C:/Windows/secret.py\n"
        "--- a/C:/Windows/secret.py\n"
        "+++ b/C:/Windows/secret.py\n"
        "@@ -1 +1,2 @@\n"
        " a = 1\n"
        "+b = 2\n"
    )
    with Storage() as storage:
        report = check_diff(storage, traversal + drive)
    assert report.exit_code() == 1
    assert {item.status for item in report.results} == {"path-rejected"}
    assert all(item.disposition == "error" for item in report.results)


def test_staged_acquisition_uses_no_shell_and_reports_missing_git() -> None:
    calls: dict[str, object] = {}

    def fake_run(args: object, **kwargs: object) -> object:
        calls["args"] = args
        calls["kwargs"] = kwargs
        raise FileNotFoundError

    source = acquire_staged_diff(runner=fake_run)
    assert source.error == "git-not-found"
    assert calls["kwargs"]["shell"] is False
    assert "timeout" in calls["kwargs"]
    assert list(calls["args"])[:3] == ["git", "diff", "--cached"]  # type: ignore[arg-type]


def test_staged_acquisition_reports_timeout() -> None:
    def fake_run(args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="git", timeout=1.0)

    assert acquire_staged_diff(runner=fake_run).error == "git-timeout"


def test_check_diff_never_reindexes_before_evaluation() -> None:
    with Storage() as storage:
        _seed_auth_constraint(storage, content_hash="auth-hash")
        before = storage.get_node("auth-node").content_hash
        report = check_diff(
            storage,
            _diff_for("src/auth.py", f"# add {FORBIDDEN}"),
            enforce_constraints=True,
        )
        after = storage.get_node("auth-node").content_hash
    # The anchor is untouched, so the fresh decision still blocks. Reindexing
    # first would change the hash, make the decision stale, and wrongly unblock.
    assert before == after == "auth-hash"
    assert report.blocked is True


def test_core_and_vector_modes_agree(monkeypatch) -> None:
    def run_once() -> tuple[int, str | None]:
        with Storage() as storage:
            _seed_auth_constraint(storage)
            report = check_diff(
                storage,
                _diff_for("src/auth.py", f"# add {FORBIDDEN}"),
                enforce_constraints=True,
            )
            return report.exit_code(), report.results[0].verdict

    vector_mode = run_once()
    monkeypatch.setenv("MNEMEX_NO_VEC", "1")
    core_mode = run_once()
    assert vector_mode == core_mode == (2, "contradiction")
