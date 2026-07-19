"""Staged/file-diff decision gate built on the existing proposed-change guard.

This module turns a real unified diff into a set of file-scoped proposed-change
checks. It performs only four things and delegates every judgement to the
existing guard:

1. bounded diff acquisition (staged via git, or from a file, never executing
   project code);
2. unified-diff file splitting (git and plain diffs, add/delete/rename/binary);
3. per-file aggregation over :func:`mnemex.decision_guard.check_proposed_change`;
4. optional JSON / Markdown rendering of the aggregated result.

Critical ordering contract: the diff is **never reindexed** before it is
checked. Blocking still requires a decision that is fresh according to the
already-indexed brain, so every report is stamped ``freshness_basis:
indexed-brain`` and carries an explicit before-change warning. The live Codex
pre-edit MCP guard remains the authoritative enforcement path; this command is
a documented second line of defence.

Selection is deliberately **file-scoped** (the node schema stores ``line_start``
but not ``line_end``), so this module never claims exact hunk-to-symbol
precision.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Collection
from dataclasses import dataclass, field

from mnemex.decision_guard import check_proposed_change
from mnemex.evidence import DEFAULT_EVIDENCE_TOKEN_CAP
from mnemex.judge import SemanticJudge
from mnemex.security import RedactionLog, sanitize
from mnemex.storage import Storage

__all__ = [
    "MAX_TOTAL_DIFF_BYTES",
    "MAX_FILE_SUMMARY_BYTES",
    "DEFAULT_GIT_TIMEOUT_SECONDS",
    "FRESHNESS_BASIS",
    "FRESHNESS_WARNING",
    "DIFF_TRUNCATED_WARNING",
    "DiffSource",
    "FileDiff",
    "FileResult",
    "DiffGuardReport",
    "acquire_staged_diff",
    "read_diff_file",
    "split_unified_diff",
    "check_diff",
    "acquisition_failure_report",
]

# --- Explicit, tested safety bounds ---------------------------------------- #

#: Hard ceiling on the total captured diff before any further token bounding.
MAX_TOTAL_DIFF_BYTES = 1024 * 1024  # 1 MiB
#: Hard ceiling on one file's diff summary before evidence token bounding.
MAX_FILE_SUMMARY_BYTES = 256 * 1024  # 256 KiB
#: Default timeout for the ``git diff --cached`` subprocess.
DEFAULT_GIT_TIMEOUT_SECONDS = 15.0

FRESHNESS_BASIS = "indexed-brain"
FRESHNESS_WARNING = (
    "Assumes the brain was indexed before these edits; run the Codex pre-edit "
    "guard for an authoritative before-change check."
)
DIFF_TRUNCATED_WARNING = (
    "Diff exceeded the 1 MiB safety bound and was truncated before evaluation."
)

_GIT_STAGED_DIFF_ARGS: tuple[str, ...] = (
    "git",
    "diff",
    "--cached",
    "--no-ext-diff",
    "--unified=3",
)

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")

# Runner signature for dependency-injected subprocess execution in tests.
Runner = Callable[..., object]


# --- Data structures -------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DiffSource:
    """A bounded diff acquisition result.

    ``error`` is a stable, non-secret token when acquisition failed; otherwise
    ``text`` holds the (already bounded) unified diff.
    """

    text: str = ""
    warnings: tuple[str, ...] = ()
    error: str | None = None
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class FileDiff:
    """One file's segment of a unified diff.

    ``body`` is retained only in memory to derive a bounded, sanitized summary;
    it is never persisted or rendered.
    """

    old_path: str | None
    new_path: str | None
    diff_a: str | None
    diff_b: str | None
    rename_from: str | None
    rename_to: str | None
    is_binary: bool
    is_rename: bool
    is_delete: bool
    is_add: bool
    body: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FileResult:
    """The aggregated guard outcome for a single changed file."""

    path: str | None
    status: str  # checked | binary-skipped | path-rejected | parse-error | eval-error
    disposition: str  # blocked | advisory | error
    blocked: bool
    verdict: str | None = None
    confidence: float | None = None
    decision_ids: tuple[str, ...] = ()
    run_id: str | None = None
    recommended_action: str | None = None
    payload_summary: dict[str, object] | None = None
    error: str | None = None
    old_path: str | None = None
    new_path: str | None = None
    is_binary: bool = False
    is_rename: bool = False
    is_delete: bool = False
    is_add: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "status": self.status,
            "disposition": self.disposition,
            "blocked": self.blocked,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "decision_ids": list(self.decision_ids),
            "run_id": self.run_id,
            "recommended_action": self.recommended_action,
            "payload_summary": self.payload_summary,
            "error": self.error,
            "old_path": self.old_path,
            "new_path": self.new_path,
            "is_binary": self.is_binary,
            "is_rename": self.is_rename,
            "is_delete": self.is_delete,
            "is_add": self.is_add,
        }


@dataclass(frozen=True, slots=True)
class DiffGuardReport:
    """The complete, aggregated staged-diff decision result."""

    source: str
    results: tuple[FileResult, ...] = ()
    warnings: tuple[str, ...] = ()
    redaction_count: int = 0
    freshness_basis: str = FRESHNESS_BASIS
    source_acquisition_failed: bool = False
    acquisition_error: str | None = None

    @property
    def files_seen(self) -> int:
        return len(self.results)

    @property
    def files_checked(self) -> int:
        return sum(1 for item in self.results if item.status == "checked")

    @property
    def files_skipped(self) -> int:
        return sum(1 for item in self.results if item.status != "checked")

    @property
    def blocked(self) -> bool:
        return any(item.blocked for item in self.results)

    @property
    def blocked_files(self) -> tuple[str, ...]:
        return tuple(
            item.path or "(unknown)"
            for item in self.results
            if item.disposition == "blocked"
        )

    @property
    def advisory_files(self) -> tuple[str, ...]:
        return tuple(
            item.path or "(unknown)"
            for item in self.results
            if item.disposition == "advisory"
        )

    @property
    def error_files(self) -> tuple[str, ...]:
        return tuple(
            item.path or "(unknown)"
            for item in self.results
            if item.disposition == "error"
        )

    def exit_code(self) -> int:
        """Return the CLI exit code following the documented precedence.

        ``2`` when any file blocked (per-file errors are still reported);
        ``1`` when nothing blocked but the source failed or a file could not be
        evaluated reliably; ``0`` otherwise (including advisory/uncertain/
        unavailable results).
        """
        if self.blocked:
            return 2
        if self.source_acquisition_failed or self.error_files:
            return 1
        return 0

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "freshness_basis": self.freshness_basis,
            "warnings": list(self.warnings),
            "files_seen": self.files_seen,
            "files_checked": self.files_checked,
            "files_skipped": self.files_skipped,
            "blocked": self.blocked,
            "blocked_files": list(self.blocked_files),
            "advisory_files": list(self.advisory_files),
            "error_files": list(self.error_files),
            "redaction_count": self.redaction_count,
            "source_acquisition_failed": self.source_acquisition_failed,
            "acquisition_error": self.acquisition_error,
            "results": [item.as_dict() for item in self.results],
        }

    def render_markdown(self) -> str:
        lines = [
            "# Mnemex staged-diff decision check",
            "",
            f"- Source: {self.source}",
            f"- Freshness basis: {self.freshness_basis}",
            (
                f"- Files seen: {self.files_seen} "
                f"(checked {self.files_checked}, skipped {self.files_skipped})"
            ),
            f"- Blocked: {'yes' if self.blocked else 'no'}",
            f"- Redactions: {self.redaction_count}",
        ]
        if self.acquisition_error is not None:
            lines.append(f"- Acquisition error: {self.acquisition_error}")
        for warning in self.warnings:
            lines.append(f"- Warning: {warning}")
        lines.append("")
        if not self.results:
            lines.append("_No file changes to evaluate._")
            return "\n".join(lines) + "\n"
        lines.append("| File | Status | Verdict | Confidence | Blocked | Decisions |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for item in self.results:
            confidence = "" if item.confidence is None else f"{item.confidence:.2f}"
            decisions = ", ".join(item.decision_ids)
            lines.append(
                f"| {item.path or '(unknown)'} | {item.status} | "
                f"{item.verdict or ''} | {confidence} | "
                f"{'yes' if item.blocked else 'no'} | {decisions} |"
            )
        lines.append("")
        for item in self.results:
            if item.recommended_action:
                lines.append(f"- {item.path or '(unknown)'}: {item.recommended_action}")
        return "\n".join(lines) + "\n"


# --- Diff acquisition ------------------------------------------------------- #


def acquire_staged_diff(
    *,
    cwd: str | None = None,
    timeout: float = DEFAULT_GIT_TIMEOUT_SECONDS,
    runner: Runner | None = None,
) -> DiffSource:
    """Capture ``git diff --cached`` with a bounded, non-shell subprocess.

    The command is run with an explicit argument list, ``shell=False``, a
    timeout, and a byte-bounded capture. Binary patch bodies are never
    requested; the normal ``Binary files ... differ`` marker is enough for an
    advisory skip. Every failure mode yields a stable, non-secret ``error``
    token instead of raising.
    """
    run = subprocess.run if runner is None else runner
    try:
        completed = run(
            list(_GIT_STAGED_DIFF_ARGS),
            capture_output=True,
            timeout=timeout,
            shell=False,
            cwd=cwd,
        )
    except FileNotFoundError:
        return DiffSource(error="git-not-found")
    except subprocess.TimeoutExpired:
        return DiffSource(error="git-timeout")
    except OSError:
        return DiffSource(error="git-unavailable")

    returncode = getattr(completed, "returncode", 1)
    if returncode != 0:
        return DiffSource(error="git-failed")

    stdout = getattr(completed, "stdout", b"") or b""
    if isinstance(stdout, str):
        stdout = stdout.encode("utf-8", "replace")
    truncated = len(stdout) > MAX_TOTAL_DIFF_BYTES
    text = stdout[:MAX_TOTAL_DIFF_BYTES].decode("utf-8", "replace")
    warnings = (DIFF_TRUNCATED_WARNING,) if truncated else ()
    return DiffSource(text=text, warnings=warnings, truncated=truncated)


def read_diff_file(path: str) -> DiffSource:
    """Read a unified diff from a file with a hard byte bound and no git."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_TOTAL_DIFF_BYTES + 1)
    except OSError:
        return DiffSource(error="diff-file-unreadable")
    truncated = len(data) > MAX_TOTAL_DIFF_BYTES
    text = data[:MAX_TOTAL_DIFF_BYTES].decode("utf-8", "replace")
    warnings = (DIFF_TRUNCATED_WARNING,) if truncated else ()
    return DiffSource(text=text, warnings=warnings, truncated=truncated)


# --- Unified-diff splitting ------------------------------------------------- #


@dataclass(slots=True)
class _Segment:
    diff_a: str | None = None
    diff_b: str | None = None
    old_header: str | None = None
    new_header: str | None = None
    body: list[str] = field(default_factory=list)


def split_unified_diff(diff_text: str) -> list[FileDiff]:
    """Split a git or plain unified diff into per-file segments.

    Handles diffs with and without ``diff --git`` headers, additions,
    deletions, renames, ``/dev/null`` sides, CRLF endings, and quoted paths.
    A removed content line that happens to start with ``--- `` is not mistaken
    for a file header because a real header is always immediately followed by a
    ``+++ `` line.
    """
    normalized = diff_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    segments: list[_Segment] = []
    current: _Segment | None = None
    awaiting_git_header = False

    total = len(lines)
    for index, line in enumerate(lines):
        following = lines[index + 1] if index + 1 < total else ""
        if line.startswith("diff --git "):
            current = _Segment()
            current.diff_a, current.diff_b = _parse_diff_git_paths(line)
            awaiting_git_header = True
            segments.append(current)
            continue
        if line.startswith("--- ") and following.startswith("+++ "):
            if awaiting_git_header and current is not None:
                current.old_header = line
            else:
                current = _Segment()
                current.old_header = line
                segments.append(current)
            awaiting_git_header = False
            continue
        if (
            line.startswith("+++ ")
            and current is not None
            and current.old_header is not None
            and current.new_header is None
        ):
            current.new_header = line
            continue
        if current is not None:
            current.body.append(line)

    return [_finalize_segment(segment) for segment in segments]


def _finalize_segment(segment: _Segment) -> FileDiff:
    old_path = _header_path(segment.old_header)
    new_path = _header_path(segment.new_header)
    is_binary = any(_is_binary_marker(line) for line in segment.body)

    rename_from: str | None = None
    rename_to: str | None = None
    is_delete_marker = False
    is_add_marker = False
    for line in segment.body:
        if line.startswith("rename from "):
            rename_from = _strip_ab_prefix(line[len("rename from ") :].strip())
        elif line.startswith("rename to "):
            rename_to = _strip_ab_prefix(line[len("rename to ") :].strip())
        elif line.startswith("deleted file mode"):
            is_delete_marker = True
        elif line.startswith("new file mode"):
            is_add_marker = True

    is_rename = bool(rename_from or rename_to) or (
        segment.diff_a is not None
        and segment.diff_b is not None
        and segment.diff_a != segment.diff_b
    )
    is_delete = is_delete_marker or (
        segment.new_header is not None and new_path is None
    )
    is_add = is_add_marker or (segment.old_header is not None and old_path is None)

    return FileDiff(
        old_path=old_path,
        new_path=new_path,
        diff_a=segment.diff_a,
        diff_b=segment.diff_b,
        rename_from=rename_from,
        rename_to=rename_to,
        is_binary=is_binary,
        is_rename=is_rename,
        is_delete=is_delete,
        is_add=is_add,
        body=tuple(segment.body),
    )


def _is_binary_marker(line: str) -> bool:
    stripped = line.strip()
    if stripped == "GIT binary patch":
        return True
    return stripped.startswith("Binary files ") and stripped.endswith(" differ")


def _header_path(header_line: str | None) -> str | None:
    """Return the path from a ``--- ``/``+++ `` header, or None for /dev/null."""
    if header_line is None:
        return None
    rest = header_line[4:].split("\t", 1)[0].strip()
    if rest.startswith('"'):
        rest, _ = _read_quoted_token(rest)
    if not rest or rest == "/dev/null":
        return None
    return _strip_ab_prefix(rest)


def _parse_diff_git_paths(line: str) -> tuple[str | None, str | None]:
    rest = line[len("diff --git ") :].strip()
    if not rest:
        return None, None
    if rest.startswith('"'):
        first, remainder = _read_quoted_token(rest)
        remainder = remainder.strip()
        if remainder.startswith('"'):
            second, _ = _read_quoted_token(remainder)
        else:
            second = remainder.split(" ", 1)[0] if remainder else None
        return _strip_ab_prefix(first), _strip_ab_prefix(second)
    marker = rest.find(" b/")
    if marker != -1:
        return _strip_ab_prefix(rest[:marker]), _strip_ab_prefix(rest[marker + 1 :])
    parts = rest.split(" ")
    if len(parts) >= 2:
        return _strip_ab_prefix(parts[0]), _strip_ab_prefix(parts[-1])
    return _strip_ab_prefix(rest), None


def _read_quoted_token(text: str) -> tuple[str, str]:
    """Read one double-quoted, C-escaped token; return (value, remainder)."""
    out: list[str] = []
    index = 1  # skip the opening quote
    escapes = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            out.append(escapes.get(text[index + 1], text[index + 1]))
            index += 2
            continue
        if char == '"':
            return "".join(out), text[index + 1 :]
        out.append(char)
        index += 1
    return "".join(out), ""


def _strip_ab_prefix(path: str | None) -> str | None:
    if path is None:
        return None
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


# --- Aggregation ------------------------------------------------------------ #


def check_diff(
    storage: Storage,
    diff_text: str,
    *,
    source: str = "diff-file",
    scopes: Collection[str] = ("project-shared",),
    max_evidence_tokens: int = DEFAULT_EVIDENCE_TOKEN_CAP,
    enforce_constraints: bool = False,
    judge: SemanticJudge | None = None,
    extra_warnings: Collection[str] = (),
) -> DiffGuardReport:
    """Check every changed text file in ``diff_text`` against the guard.

    The diff is not reindexed first, so blocking still requires a decision that
    is fresh in the already-indexed brain. One file's operational failure never
    erases another file's valid result.
    """
    bounded_text, truncated = _bound_total_diff(diff_text or "")
    warnings: list[str] = [FRESHNESS_WARNING]
    for warning in extra_warnings:
        if warning not in warnings:
            warnings.append(warning)
    if truncated and DIFF_TRUNCATED_WARNING not in warnings:
        warnings.append(DIFF_TRUNCATED_WARNING)

    redactions = RedactionLog()
    scope_values = tuple(scopes)
    results = [
        _evaluate_file(
            storage,
            file_diff,
            scopes=scope_values,
            max_evidence_tokens=max_evidence_tokens,
            enforce_constraints=enforce_constraints,
            judge=judge,
            redactions=redactions,
        )
        for file_diff in split_unified_diff(bounded_text)
    ]
    return DiffGuardReport(
        source=source,
        results=tuple(results),
        warnings=tuple(warnings),
        redaction_count=redactions.count,
    )


def acquisition_failure_report(
    source: str,
    reason: str,
    *,
    extra_warnings: Collection[str] = (),
) -> DiffGuardReport:
    """Build a report for a source that could not be acquired (exit code 1)."""
    warnings: list[str] = [FRESHNESS_WARNING]
    for warning in extra_warnings:
        if warning not in warnings:
            warnings.append(warning)
    return DiffGuardReport(
        source=source,
        warnings=tuple(warnings),
        source_acquisition_failed=True,
        acquisition_error=reason,
    )


def _evaluate_file(
    storage: Storage,
    file_diff: FileDiff,
    *,
    scopes: tuple[str, ...],
    max_evidence_tokens: int,
    enforce_constraints: bool,
    judge: SemanticJudge | None,
    redactions: RedactionLog,
) -> FileResult:
    safe_old = _sanitize_path(file_diff.old_path, redactions)
    safe_new = _sanitize_path(file_diff.new_path, redactions)
    common = {
        "old_path": safe_old,
        "new_path": safe_new,
        "is_binary": file_diff.is_binary,
        "is_rename": file_diff.is_rename,
        "is_delete": file_diff.is_delete,
        "is_add": file_diff.is_add,
    }

    if file_diff.is_binary:
        return FileResult(
            path=_sanitize_path(_target_path(file_diff), redactions),
            status="binary-skipped",
            disposition="advisory",
            blocked=False,
            recommended_action=(
                "Binary change skipped; review manually if it affects a "
                "governed decision."
            ),
            error="binary-diff",
            **common,
        )

    raw_target = _target_path(file_diff)
    if raw_target is None:
        return FileResult(
            path=None,
            status="parse-error",
            disposition="error",
            blocked=False,
            recommended_action="This file header could not be parsed reliably.",
            error="unparseable-file-header",
            **common,
        )

    normalized, escape_reason = _normalize_and_validate(raw_target)
    if escape_reason is not None:
        return FileResult(
            path=_sanitize_path(raw_target, redactions),
            status="path-rejected",
            disposition="error",
            blocked=False,
            recommended_action="Path was rejected for safety and not evaluated.",
            error=escape_reason,
            **common,
        )

    summary = _summary_from_body(file_diff.body, redactions)
    try:
        result = check_proposed_change(
            storage,
            normalized,
            summary,
            judge=judge,
            scopes=scopes,
            max_evidence_tokens=max_evidence_tokens,
            enforce_constraints=enforce_constraints,
        )
    except Exception:  # noqa: BLE001 - isolate one file's failure from the rest
        return FileResult(
            path=normalized,
            status="eval-error",
            disposition="error",
            blocked=False,
            recommended_action="This file could not be evaluated reliably.",
            error="evaluation-failed",
            **common,
        )

    payload = result.as_dict()
    return FileResult(
        path=str(payload["path"]),
        status="checked",
        disposition="blocked" if result.blocked else "advisory",
        blocked=result.blocked,
        verdict=str(payload["verdict"]),
        confidence=float(payload["confidence"]),  # type: ignore[arg-type]
        decision_ids=tuple(payload["decision_ids"]),  # type: ignore[arg-type]
        run_id=str(payload["run_id"]),
        recommended_action=str(payload["recommended_action"]),
        payload_summary=payload["payload_summary"],  # type: ignore[arg-type]
        **common,
    )


def _target_path(file_diff: FileDiff) -> str | None:
    """Pick the single file path to evaluate for this segment.

    The indexed brain reflects the pre-edit state, so decisions are anchored to
    the old path. We therefore prefer the old side and only fall back to the new
    side for pure additions.
    """
    if file_diff.is_rename:
        candidates = (
            file_diff.rename_from,
            file_diff.old_path,
            file_diff.diff_a,
            file_diff.new_path,
            file_diff.rename_to,
            file_diff.diff_b,
        )
    else:
        candidates = (
            file_diff.old_path,
            file_diff.diff_a,
            file_diff.new_path,
            file_diff.diff_b,
        )
    for candidate in candidates:
        if candidate:
            return candidate
    return None


def _normalize_and_validate(path: str) -> tuple[str, str | None]:
    """Normalize to forward slashes and reject paths escaping the project root."""
    normalized = path.replace("\\", "/").strip()
    if not normalized:
        return "", "empty-path"
    if normalized.startswith("/") or _WINDOWS_DRIVE_RE.match(normalized):
        return normalized, "path-escapes-root"
    depth = 0
    for part in normalized.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            depth -= 1
            if depth < 0:
                return normalized, "path-escapes-root"
        else:
            depth += 1
    return normalized, None


def _summary_from_body(body: tuple[str, ...], redactions: RedactionLog) -> str:
    """Return a byte-bounded, sanitized summary of one file's diff body."""
    raw = "\n".join(body)
    encoded = raw.encode("utf-8", "replace")
    if len(encoded) > MAX_FILE_SUMMARY_BYTES:
        raw = encoded[:MAX_FILE_SUMMARY_BYTES].decode("utf-8", "replace")
    return sanitize(raw, field_name="diff_summary", log=redactions)


def _sanitize_path(path: str | None, redactions: RedactionLog) -> str | None:
    if path is None:
        return None
    return sanitize(path.replace("\\", "/"), field_name="path", log=redactions)


def _bound_total_diff(text: str) -> tuple[str, bool]:
    encoded = text.encode("utf-8", "replace")
    if len(encoded) <= MAX_TOTAL_DIFF_BYTES:
        return text, False
    return encoded[:MAX_TOTAL_DIFF_BYTES].decode("utf-8", "replace"), True
