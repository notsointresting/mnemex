"""Phase 6 — Security & privacy (blocking gate).

Provides deterministic secret stripping at write time, ``<private>`` tag
handling, and a redaction audit log. The gate requires 100% strip rate on
known-bad patterns before persistence.

This module is designed to be called as a preprocessing step before any
``remember()`` invocation — it sanitizes content and rationale, logs every
redaction, and strips ``<private>`` tagged blocks entirely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

__all__ = [
    "RedactionEntry",
    "RedactionLog",
    "SecurityConfig",
    "sanitize",
    "strip_private_tags",
    "is_clean",
]


@dataclass(frozen=True, slots=True)
class RedactionEntry:
    """A single redaction event recorded in the audit log."""

    timestamp: str
    field: str  # "content" | "rationale" | "tags"
    pattern_name: str
    original_snippet: str  # first 20 chars of the matched text (masked)
    replacement: str


@dataclass(slots=True)
class RedactionLog:
    """Append-only audit log of all redaction actions."""

    entries: list[RedactionEntry] = field(default_factory=list)

    def record(
        self,
        field_name: str,
        pattern_name: str,
        matched_text: str,
        replacement: str,
    ) -> None:
        # Mask all but the first 4 and last 4 characters of matched text
        snippet = _mask_snippet(matched_text)
        self.entries.append(RedactionEntry(
            timestamp=_utc_timestamp(),
            field=field_name,
            pattern_name=pattern_name,
            original_snippet=snippet,
            replacement=replacement,
        ))

    def clear(self) -> None:
        self.entries.clear()

    @property
    def count(self) -> int:
        return len(self.entries)


# Secret patterns — ordered from most specific to broadest.
# Each is (name, compiled_regex, replacement_text).
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # AWS keys
    (
        "aws_access_key",
        re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
        "[REDACTED:aws_key]",
    ),
    # AWS secret keys (40 chars base64). Git-style references
    # ("commit <sha>") are exempt: a 40-hex SHA is also 40 base64 chars.
    (
        "aws_secret_key",
        re.compile(
            r"(?<![A-Za-z0-9+/])(?<!commit )(?<!Commit )"
            r"[A-Za-z0-9+/]{40}(?![A-Za-z0-9+/=])"
        ),
        "[REDACTED:aws_secret]",
    ),
    # GitHub tokens (classic PAT, fine-grained, oauth)
    (
        "github_token",
        re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,255}"),
        "[REDACTED:github_token]",
    ),
    # Password/secret assignments (password=..., pwd: ...)
    (
        "password_assignment",
        re.compile(
            r"(?i)\b(?:password|passwd|pwd|secret)\s*[:=]\s*['\"]?([^\s'\"]{4,})['\"]?"
        ),
        "[REDACTED:password]",
    ),
    # Anthropic API keys (must precede openai_key: shared "sk-" prefix)
    (
        "anthropic_key",
        re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"),
        "[REDACTED:anthropic_key]",
    ),
    # OpenAI API keys (sk-..., sk-proj-...)
    (
        "openai_key",
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b"),
        "[REDACTED:openai_key]",
    ),
    # Google API keys
    (
        "google_api_key",
        re.compile(r"\bAIza[A-Za-z0-9_\-]{30,40}\b"),
        "[REDACTED:google_key]",
    ),
    # Stripe secret/restricted keys
    (
        "stripe_key",
        re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b"),
        "[REDACTED:stripe_key]",
    ),
    # Slack tokens
    (
        "slack_token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
        "[REDACTED:slack_token]",
    ),
    # Generic API keys (key=... or key: ...)
    (
        "api_key_assignment",
        re.compile(
            r"(?i)(?:api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token"
            r"|auth[_-]?token|bearer)\s*[:=]\s*['\"]?([A-Za-z0-9_\-./+]{20,})['\"]?"
        ),
        "[REDACTED:api_key]",
    ),
    # Bearer tokens in headers
    (
        "bearer_token",
        re.compile(r"Bearer\s+[A-Za-z0-9_\-.~+/]+=*"),
        "[REDACTED:bearer]",
    ),
    # Private keys (PEM format)
    (
        "private_key",
        re.compile(
            r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----"
            r"[\s\S]*?"
            r"-----END\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----"
        ),
        "[REDACTED:private_key]",
    ),
    # JWT tokens (3 base64url segments)
    (
        "jwt_token",
        re.compile(
            r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
        ),
        "[REDACTED:jwt]",
    ),
    # Connection strings with passwords
    (
        "connection_string",
        re.compile(
            r"(?i)(?:postgres|mysql|mongodb|redis|amqp)(?:ql)?://[^:\s]+:[^@\s]+@[^\s]+"
        ),
        "[REDACTED:connection_string]",
    ),
    # Generic hex secrets (32+ hex chars that look like tokens).
    # Git-style references ("commit <sha>") are exempt so decisions can cite
    # commit hashes without being mangled.
    (
        "hex_secret",
        re.compile(
            r"(?<![a-fA-F0-9])(?<!commit )(?<!Commit )"
            r"[a-fA-F0-9]{32,64}(?![a-fA-F0-9])"
        ),
        "[REDACTED:hex_token]",
    ),
    # Email addresses (PII)
    (
        "email",
        re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
        "[REDACTED:email]",
    ),
    # Phone numbers (basic patterns)
    (
        "phone_number",
        re.compile(
            r"(?<!\d)(?:\+\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
        ),
        "[REDACTED:phone]",
    ),
    # IP addresses (IPv4). Loopback and the unspecified address are not PII
    # and appear in this project's own documentation, so they are exempt.
    (
        "ipv4_address",
        re.compile(
            r"(?<!\d)(?!127\.)(?!0\.0\.0\.0(?!\d))"
            r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?!\d)"
        ),
        "[REDACTED:ip]",
    ),
]

# <private> tag pattern — strips content between tags entirely
_PRIVATE_TAG_RE = re.compile(
    r"<private>[\s\S]*?</private>",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SecurityConfig:
    """Configuration for the security module.

    Allows disabling specific pattern categories (e.g. for testing) or
    adjusting the redaction replacement text.
    """

    strip_secrets: bool = True
    strip_pii: bool = True
    strip_private_tags: bool = True
    custom_patterns: tuple[tuple[str, re.Pattern[str], str], ...] = ()


def strip_private_tags(text: str, *, log: RedactionLog | None = None) -> str:
    """Remove all ``<private>...</private>`` blocks from text.

    These are intentionally marked by the user as never-persist content.
    Handles nested tags by repeating until no more matches remain.
    """
    result = text
    while True:
        new_result = _PRIVATE_TAG_RE.sub(
            lambda m: _log_private(m, log), result
        )
        if new_result == result:
            break
        result = new_result
    return result.strip()


def _log_private(match: re.Match[str], log: RedactionLog | None) -> str:
    if log is not None:
        log.record("content", "private_tag", match.group(0), "")
    return ""


def sanitize(
    text: str,
    *,
    field_name: str = "content",
    log: RedactionLog | None = None,
    config: SecurityConfig | None = None,
) -> str:
    """Sanitize text by stripping secrets, PII, and private-tagged blocks.

    This is the primary entry point. Call before persisting any user-provided
    content. Deterministic: same input always produces the same output.

    Returns the sanitized text. All redactions are recorded in ``log`` if
    provided.
    """
    if config is None:
        config = SecurityConfig()

    result = text

    # 1. Strip <private> tags first
    if config.strip_private_tags:
        result = strip_private_tags(result, log=log)

    # 2. Apply secret/PII patterns
    patterns = list(_SECRET_PATTERNS)
    if config.custom_patterns:
        patterns.extend(config.custom_patterns)

    for pattern_name, regex, replacement in patterns:
        # Skip PII patterns if strip_pii is disabled
        if not config.strip_pii and pattern_name in (
            "email", "phone_number", "ipv4_address"
        ):
            continue
        # Skip secret patterns if strip_secrets is disabled
        if not config.strip_secrets and pattern_name not in (
            "email", "phone_number", "ipv4_address"
        ):
            continue

        def _make_replacer(
            pname: str, repl: str
        ):
            def replacer(match: re.Match[str]) -> str:
                if log is not None:
                    log.record(field_name, pname, match.group(0), repl)
                return repl
            return replacer

        result = regex.sub(_make_replacer(pattern_name, replacement), result)

    return result


def is_clean(text: str, *, config: SecurityConfig | None = None) -> bool:
    """Check whether text contains any detectable secret or PII.

    Returns True if no patterns match (text is safe to persist as-is).
    """
    if config is None:
        config = SecurityConfig()

    if config.strip_private_tags and _PRIVATE_TAG_RE.search(text):
        return False

    for pattern_name, regex, _replacement in _SECRET_PATTERNS:
        if not config.strip_pii and pattern_name in (
            "email", "phone_number", "ipv4_address"
        ):
            continue
        if not config.strip_secrets and pattern_name not in (
            "email", "phone_number", "ipv4_address"
        ):
            continue
        if regex.search(text):
            return False

    return True


def _mask_snippet(text: str, max_len: int = 20) -> str:
    """Mask a matched secret for the audit log — show only structure."""
    if len(text) <= 8:
        return "*" * len(text)
    prefix = text[:4]
    suffix = text[-4:]
    middle_len = min(len(text) - 8, max_len - 8)
    return f"{prefix}{'*' * middle_len}{suffix}"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
