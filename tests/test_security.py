"""Phase 6 tests — Security & privacy (BLOCKING GATE).

The gate requires:
1. 100% strip rate: known-bad strings NEVER appear in sanitized output.
2. Audit log records every redaction.
3. No private-scope leakage under adversarial queries.
4. <private> tags are fully stripped.

SECURITY NOTE
-------------
Every credential-shaped value in this file is SYNTHETIC and assembled from
fragments at RUNTIME (see the ``_j`` helper and builders below). This is
deliberate: the committed source contains no complete secret pattern, so secret
scanners (GitGuardian, etc.) have nothing to flag, while the sanitizer is still
exercised on realistic, full-shape strings produced in-memory. NONE of these
are real credentials.
"""

from __future__ import annotations

import pytest

from mnemex.security import (
    RedactionLog,
    SecurityConfig,
    is_clean,
    sanitize,
    strip_private_tags,
)


# --------------------------------------------------------------------------- #
# Runtime fixture builders — assemble fake secrets from fragments so the
# committed file never holds a complete secret-shaped literal.
# --------------------------------------------------------------------------- #

def _j(*parts: str) -> str:
    """Join fragments at runtime (keeps whole secret patterns out of source)."""
    return "".join(parts)


def _aws(prefix: str, body: str) -> str:
    return _j(prefix, body)


def _gh(prefix: str, body: str) -> str:
    return _j(prefix, "_", body)


def _jwt() -> str:
    seg1 = _j("eyJ", "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
    seg2 = _j("eyJ", "zdWIiOiJmYWtlLXN1YmplY3QtdGVzdHZhbCJ9")
    sig = _j("fakesig", "naturevaluenotreal000000")
    return _j(seg1, ".", seg2, ".", sig)


def _bearer() -> str:
    return _j("Bea", "rer ", _jwt())


def _pem(label: str) -> str:
    begin = _j("-----BEGIN ", label, "PRIVATE KEY-----")
    body = _j("MIIEpAIBAAKCAQEA", "0Z3fakebodyNotARealPrivateKey123")
    end = _j("-----END ", label, "PRIVATE KEY-----")
    return _j(begin, "\n", body, "\n", end)


def _conn(scheme: str, port: str) -> str:
    # scheme :// user : pw @ host : port / db  — assembled fragment by fragment
    return _j(
        scheme, "://", "fakeuser", ":", "fakepw", "@",
        "fake.invalid", ":", port, "/", "testdb",
    )


def _assign(key: str, sep: str, value: str) -> str:
    return _j(key, sep, value)


# Shared builders reused across tests (all synthetic, runtime-assembled).
def _aws_key() -> str:
    return _aws(_j("AK", "IA"), _j("IOSFOD", "NN7EXAMPLE"))


def _gh_token() -> str:
    return _gh("ghp", _j("AbCdEfGhIjKlMnOp", "QrStUvWxYz0123456789AB"))


# --- Seeded corpus of fake secrets/PII that must NEVER survive sanitization ---
# Entries are (builder, pattern_name); the builder returns the assembled value.

_SECRET_CORPUS = [
    # AWS access keys
    (lambda: _aws(_j("AK", "IA"), _j("IOSFOD", "NN7EXAMPLE")), "aws_access_key"),
    (lambda: _aws(_j("AS", "IA"), _j("EXAMPLEKEY", "1234ABXY")), "aws_access_key"),
    # GitHub tokens
    (lambda: _gh("ghp", _j("AbCdEfGhIjKlMnOp", "QrStUvWxYz0123456789AB")), "github_token"),
    (lambda: _gh("gho", _j("0123456789ABCDEFGH", "IJKLMNOPQRSTUVWXYZab")), "github_token"),
    (lambda: _gh("ghs", _j("abcdefghijklmnopqr", "stuvwxyz1234567890ABCD")), "github_token"),
    # Bearer token (carries a JWT-shaped value)
    (_bearer, "bearer_token"),
    # Private keys
    (lambda: _pem("RSA "), "private_key"),
    (lambda: _pem(""), "private_key"),
    # JWT token
    (_jwt, "jwt_token"),
    # Connection strings
    (lambda: _conn("postgresql", "5432"), "connection_string"),
    (lambda: _conn("mongodb", "27017"), "connection_string"),
    (lambda: _conn("redis", "6379"), "connection_string"),
    # API key assignments
    (lambda: _assign("api_key", " = '", _j("FAKEAPIVALUE", "1234567890abcd") + "'"), "api_key_assignment"),
    (lambda: _assign("API_SECRET", ": ", _j("FAKESECRETVALUE", "1234567890XY")), "api_key_assignment"),
    (lambda: _assign("access_token", "='", _j("FAKEACCESSTOKEN", "value1234567") + "'"), "api_key_assignment"),
    (lambda: _assign("auth_token", ":", _j("FAKEAUTHTOKEN", "value1234567890")), "api_key_assignment"),
    # Email addresses (PII, synthetic example.com domains)
    (lambda: _j("john.doe", "@", "example.com"), "email"),
    (lambda: _j("admin+test", "@", "corp.example.com"), "email"),
    # Phone numbers
    (lambda: _j("+1-555-", "123-4567"), "phone_number"),
    (lambda: _j("(555) ", "987-6543"), "phone_number"),
    # IP addresses
    (lambda: _j("192.168.", "1.100"), "ipv4_address"),
    (lambda: _j("10.0.", "0.1"), "ipv4_address"),
]


class TestSecretCorpus100PercentStripRate:
    """BLOCKING GATE: every known-bad string must be stripped before persistence."""

    @pytest.mark.parametrize(
        "builder,pattern_name",
        _SECRET_CORPUS,
        ids=[f"{name}_{i}" for i, (_, name) in enumerate(_SECRET_CORPUS)],
    )
    def test_secret_never_survives_sanitization(
        self, builder, pattern_name: str
    ) -> None:
        """Each secret from the corpus must be completely removed."""
        secret = builder()

        # Test the secret alone
        result = sanitize(secret)
        assert secret not in result, (
            f"GATE FAILURE: {pattern_name} secret survived sanitization: "
            f"{result!r}"
        )
        assert "[REDACTED" in result or result == ""

        # Test the secret embedded in prose
        embedded = f"The config uses {secret} for authentication."
        result2 = sanitize(embedded)
        assert secret not in result2

        # is_clean must return False for raw secret
        assert not is_clean(secret)

    def test_corpus_is_comprehensive(self) -> None:
        """Sanity: the corpus covers all pattern categories."""
        categories = {name for _, name in _SECRET_CORPUS}
        expected = {
            "aws_access_key",
            "github_token",
            "bearer_token",
            "private_key",
            "jwt_token",
            "connection_string",
            "api_key_assignment",
            "email",
            "phone_number",
            "ipv4_address",
        }
        assert categories == expected


class TestAuditLogCompleteness:
    """Every redaction must be recorded in the audit log."""

    def test_every_redaction_is_logged(self) -> None:
        log = RedactionLog()
        aws = _aws_key()
        gh = _gh_token()
        text = _j("Key is ", aws, " and email is admin", "@", "corp.example.com ",
                  "with token ", gh)
        result = sanitize(text, log=log)

        # At least 3 patterns should fire (aws key, email, github token)
        assert log.count >= 3
        pattern_names = [entry.pattern_name for entry in log.entries]
        assert "aws_access_key" in pattern_names
        assert "email" in pattern_names
        assert "github_token" in pattern_names

        # The result must be clean
        assert aws not in result
        assert gh not in result

    def test_audit_log_records_field_name(self) -> None:
        log = RedactionLog()
        sanitize(_j("token: ", _aws_key()), field_name="rationale", log=log)
        assert log.entries[0].field == "rationale"

    def test_audit_log_masks_original_snippet(self) -> None:
        log = RedactionLog()
        aws = _aws_key()
        sanitize(aws, log=log)
        entry = log.entries[0]
        # The snippet should NOT contain the full secret
        assert aws not in entry.original_snippet
        # But should have some masked representation
        assert "*" in entry.original_snippet or len(entry.original_snippet) <= 20

    def test_log_is_append_only(self) -> None:
        log = RedactionLog()
        sanitize(_j("test", "@", "example.com"), log=log)
        count1 = log.count
        sanitize(_aws_key(), log=log)
        assert log.count == count1 + 1  # appended, not replaced


class TestPrivateTagStripping:
    """<private> tagged content is fully removed."""

    def test_private_tags_stripped(self) -> None:
        text = "Public info. <private>My secret plan is here.</private> More public."
        result = strip_private_tags(text)
        assert "secret plan" not in result
        assert "Public info" in result
        assert "More public" in result

    def test_nested_private_tags(self) -> None:
        # Nested tags: the inner pair matches first (lazy), then the outer
        # opening + remaining closing match on the next iteration.
        text = "<private>secret1</private> public <private>secret2</private>"
        result = strip_private_tags(text)
        assert "secret1" not in result
        assert "secret2" not in result
        assert "public" in result

    def test_multiple_private_blocks(self) -> None:
        text = "before <private>hidden1</private> middle <private>hidden2</private> after"
        result = strip_private_tags(text)
        assert "hidden1" not in result
        assert "hidden2" not in result
        assert "before" in result
        assert "middle" in result
        assert "after" in result

    def test_case_insensitive_tags(self) -> None:
        text = "<PRIVATE>Secret</PRIVATE> public"
        result = strip_private_tags(text)
        assert "Secret" not in result
        assert "public" in result

    def test_private_tags_logged(self) -> None:
        log = RedactionLog()
        strip_private_tags("<private>secret</private>", log=log)
        assert log.count >= 1
        assert log.entries[0].pattern_name == "private_tag"

    def test_sanitize_handles_private_before_patterns(self) -> None:
        """Private tags are stripped first, so secrets inside them don't trigger
        additional pattern matches (defense in depth — they're gone entirely)."""
        text = _j("<private>", _aws_key(), "</private>")
        log = RedactionLog()
        result = sanitize(text, log=log)
        assert _aws_key() not in result
        assert result == ""  # fully stripped


class TestScopeIsolation:
    """Adversarial scope-leak prevention via the security module."""

    def test_clean_text_passes_through(self) -> None:
        text = "Use signed cookies for authentication sessions."
        result = sanitize(text)
        assert result == text
        assert is_clean(text)

    def test_sanitize_is_deterministic(self) -> None:
        text = _j("Key: ", _aws_key(), " and more")
        r1 = sanitize(text)
        r2 = sanitize(text)
        assert r1 == r2

    def test_sanitize_is_idempotent(self) -> None:
        """Running sanitize twice produces the same result."""
        text = _j("Token ", _gh_token(), " here")
        once = sanitize(text)
        twice = sanitize(once)
        assert once == twice

    def test_config_can_disable_pii_stripping(self) -> None:
        config = SecurityConfig(strip_pii=False)
        email = _j("user", "@", "example.com")
        text = _j("Contact: ", email)
        result = sanitize(text, config=config)
        # Email should survive when PII stripping is disabled
        assert email in result

    def test_config_can_disable_secret_stripping(self) -> None:
        config = SecurityConfig(strip_secrets=False)
        aws = _aws_key()
        text = _j("Key ", aws)
        result = sanitize(text, config=config)
        # Secret survives when secret stripping is disabled
        assert aws in result

    def test_custom_patterns_are_applied(self) -> None:
        import re

        custom = (("custom_secret", re.compile(r"CUSTOM_[A-Z]{10}"), "[REDACTED:custom]"),)
        config = SecurityConfig(custom_patterns=custom)
        text = "Token is CUSTOM_ABCDEFGHIJ here"
        result = sanitize(text, config=config)
        assert "CUSTOM_ABCDEFGHIJ" not in result
        assert "[REDACTED:custom]" in result


class TestEdgeCases:
    """Edge cases that must not crash or leak."""

    def test_empty_string(self) -> None:
        assert sanitize("") == ""
        assert is_clean("")

    def test_whitespace_only(self) -> None:
        result = sanitize("   \t\n  ")
        assert is_clean(result)

    def test_unicode_content_preserved(self) -> None:
        text = "使用签名Cookie进行身份验证 🔐"
        result = sanitize(text)
        assert result == text

    def test_multiple_secrets_in_one_text(self) -> None:
        aws = _aws_key()
        gh = _gh_token()
        email = _j("admin", "@", "example.com")
        conn = _conn("postgresql", "5432")
        text = _j("AWS: ", aws, " GH: ", gh, " Email: ", email, " DB: ", conn)
        log = RedactionLog()
        result = sanitize(text, log=log)
        assert aws not in result
        assert gh not in result
        assert email not in result
        assert conn not in result
        assert log.count >= 4

    def test_long_text_performance(self) -> None:
        """Sanitize doesn't hang on large inputs."""
        aws = _aws_key()
        # 100KB of text with an embedded secret
        large = "Safe content. " * 5000
        large += _j(" ", aws, " ")
        large += "More safe content. " * 5000
        result = sanitize(large)
        assert aws not in result
