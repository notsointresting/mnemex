"""Local-first memories of prior mistakes and deterministic preflight guards.

Mistakes use the existing ``memories`` table rather than a parallel store.  A
small versioned JSON envelope keeps the four operational fields queryable by
FTS while allowing this module to distinguish its records from other memories.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from mnemex.anchors import Anchor, remember
from mnemex.retrieval import estimate_tokens
from mnemex.security import RedactionLog, sanitize
from mnemex.storage import Memory, Storage

__all__ = [
    "MISTAKE_TOKEN_CAP",
    "MistakeRecord",
    "MistakeWarning",
    "PastMistakeGuard",
    "record_mistake",
    "read_mistake",
    "guard_against_past_mistakes",
]

MISTAKE_TOKEN_CAP = 400
_FORMAT_MARKER = "mnemex.mistake.v1"
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)


@dataclass(frozen=True, slots=True)
class MistakeRecord:
    """A structured mistake stored in an existing :class:`Memory` record."""

    memory: Memory
    failure_signature: str
    root_cause: str
    affected_symbol: str
    corrective_action: str


@dataclass(frozen=True, slots=True)
class MistakeWarning:
    """One past mistake relevant to a proposed edit or command."""

    memory_id: str
    failure_signature: str
    root_cause: str
    affected_symbol: str
    corrective_action: str
    score: int
    reason: str

    def format(self) -> str:
        return (
            f"Past mistake for {self.affected_symbol}: {self.failure_signature}. "
            f"Root cause: {self.root_cause}. "
            f"Corrective action: {self.corrective_action}."
        )


@dataclass(frozen=True, slots=True)
class PastMistakeGuard:
    """Deterministic, bounded warnings returned before an edit or command."""

    warnings: tuple[MistakeWarning, ...]
    checked_scopes: tuple[str, ...]
    used_tokens: int
    budget_tokens: int

    @property
    def should_warn(self) -> bool:
        return bool(self.warnings)


def record_mistake(
    storage: Storage,
    *,
    failure_signature: str,
    root_cause: str,
    affected_symbol: str,
    corrective_action: str,
    anchor: Anchor | str | None = None,
    scope: str = "project-shared",
    source: str = "agent",
    memory_id: str | None = None,
    confidence: float = 1.0,
    importance: float = 1.0,
) -> Memory:
    """Persist one redacted, local-first mistake record through ``remember``.

    All four fields are required so a future warning says what failed, why it
    failed, where it applies, and what should be done instead.
    """
    redactions = RedactionLog()
    fields = {
        "failure_signature": _clean_field(
            failure_signature, "failure_signature", redactions
        ),
        "root_cause": _clean_field(root_cause, "root_cause", redactions),
        "affected_symbol": _clean_field(
            affected_symbol, "affected_symbol", redactions
        ),
        "corrective_action": _clean_field(
            corrective_action, "corrective_action", redactions
        ),
    }
    content = f"{_FORMAT_MARKER}\n" + json.dumps(
        fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return remember(
        storage,
        content,
        anchor=anchor,
        scope=scope,
        memory_id=memory_id,
        type="mistake",
        rationale=fields["root_cause"],
        source=source,
        confidence=confidence,
        importance=importance,
        tags="mistake",
        redaction_log=redactions,
    )


def read_mistake(memory: Memory) -> MistakeRecord | None:
    """Decode a memory created by :func:`record_mistake`, if it is one."""
    if memory.type != "mistake":
        return None
    marker, separator, encoded = memory.content.partition("\n")
    if marker != _FORMAT_MARKER or not separator:
        return None
    try:
        fields = json.loads(encoded)
    except (TypeError, ValueError):
        return None
    if not isinstance(fields, dict) or set(fields) != {
        "affected_symbol",
        "corrective_action",
        "failure_signature",
        "root_cause",
    }:
        return None
    if not all(isinstance(value, str) and value for value in fields.values()):
        return None
    return MistakeRecord(memory=memory, **fields)


def guard_against_past_mistakes(
    storage: Storage,
    proposed_action: str,
    *,
    affected_symbol: str | None = None,
    path: str | None = None,
    scopes: Sequence[str] = ("project-shared",),
    max_tokens: int = MISTAKE_TOKEN_CAP,
) -> PastMistakeGuard:
    """Return matching local mistake warnings without any model or mutation.

    Matching is intentionally explainable and deterministic: an exact symbol
    match wins, otherwise meaningful word overlap with the proposed action
    creates a warning.  Scope filtering is delegated to ``Storage`` and no
    memory access or recall state is written as part of this preflight check.
    """
    cap = min(max(max_tokens, 0), MISTAKE_TOKEN_CAP)
    action_tokens = _tokens(proposed_action)
    requested_symbols = _requested_symbols(affected_symbol, path)
    matches: list[MistakeWarning] = []

    for memory in storage.list_memories(scopes):
        record = read_mistake(memory)
        if record is None:
            continue
        score, reason = _match_score(record, action_tokens, requested_symbols)
        if score:
            matches.append(
                MistakeWarning(
                    memory_id=memory.id,
                    failure_signature=record.failure_signature,
                    root_cause=record.root_cause,
                    affected_symbol=record.affected_symbol,
                    corrective_action=record.corrective_action,
                    score=score,
                    reason=reason,
                )
            )

    ordered = sorted(
        matches, key=lambda warning: (-warning.score, warning.memory_id)
    )
    included: list[MistakeWarning] = []
    used = 0
    for warning in ordered:
        cost = estimate_tokens(warning.format())
        if used + cost <= cap:
            included.append(warning)
            used += cost
    return PastMistakeGuard(
        warnings=tuple(included),
        checked_scopes=tuple(scopes),
        used_tokens=used,
        budget_tokens=cap,
    )


def _clean_field(value: str, field_name: str, redactions: RedactionLog) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    cleaned = sanitize(
        value.strip(), field_name=f"mistake.{field_name}", log=redactions
    )
    if not cleaned:
        raise ValueError(f"{field_name} is empty after redaction")
    return cleaned


def _requested_symbols(affected_symbol: str | None, path: str | None) -> set[str]:
    requested: set[str] = set()
    if affected_symbol:
        requested.add(_normalise_symbol(affected_symbol))
    if path:
        stem = path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if stem:
            requested.add(_normalise_symbol(stem))
    return requested


def _match_score(
    record: MistakeRecord,
    action_tokens: set[str],
    requested_symbols: set[str],
) -> tuple[int, str]:
    symbol = _normalise_symbol(record.affected_symbol)
    if symbol and symbol in requested_symbols:
        score = 100 + len(action_tokens.intersection(_record_tokens(record)))
        return score, "affected symbol matches"

    overlap = action_tokens.intersection(_record_tokens(record))
    if overlap:
        return len(overlap), "action overlaps past failure signature"
    return 0, ""


def _record_tokens(record: MistakeRecord) -> set[str]:
    return _tokens(
        " ".join(
            (
                record.failure_signature,
                record.root_cause,
                record.affected_symbol,
                record.corrective_action,
            )
        )
    )


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9_]+", value.casefold())
        if token not in _STOP_WORDS
    }


def _normalise_symbol(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9_]", value.casefold()))
