from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemex.__main__ import _init
from mnemex.codex_setup import (
    GUARD_END,
    GUARD_START,
    guard_block,
    install_codex_guard,
    install_codex_mcp,
)


# --- Existing project-local Codex config behavior ------------------------------


def test_installer_preserves_other_sections_and_is_idempotent(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[model]\nname = "existing"\n', encoding="utf-8")

    assert install_codex_mcp(config, ".mnemex/mnemex.sqlite3")
    assert not install_codex_mcp(config, ".mnemex/mnemex.sqlite3")
    content = config.read_text(encoding="utf-8")
    assert '[model]\nname = "existing"' in content
    assert content.count("[mcp_servers.mnemex]") == 1


def test_installer_replaces_only_existing_mnemex_section(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.mnemex]\ncommand = "old"\n\n[other]\nvalue = 1\n',
        encoding="utf-8",
    )
    assert install_codex_mcp(config, "project.sqlite3")
    content = config.read_text(encoding="utf-8")
    assert 'command = "old"' not in content
    assert "[other]\nvalue = 1" in content


def test_windows_paths_and_quotes_are_escaped_in_codex_config(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    # A Windows path containing backslashes and an embedded double quote.
    db_path = 'C:\\Users\\dev\\a"b\\.mnemex\\mnemex.sqlite3'

    assert install_codex_mcp(config, db_path)
    content = config.read_text(encoding="utf-8")

    # Backslashes are doubled and the embedded quote is escaped so the emitted
    # TOML string literal stays valid on Windows.
    assert "\\\\Users\\\\dev\\\\" in content
    assert 'a\\"b' in content
    # The single-quoted stdio command and localhost-only stdio transport remain.
    assert 'command = "python"' in content
    assert '"serve", "--db"' in content
    assert "http" not in content


# --- Managed AGENTS.md decision-guard block ------------------------------------


def _block_count(text: str) -> int:
    assert text.count(GUARD_START) == text.count(GUARD_END)
    return text.count(GUARD_START)


def test_guard_creates_titled_file_and_is_byte_identical_on_rerun(
    tmp_path: Path,
) -> None:
    agents = tmp_path / "AGENTS.md"

    assert install_codex_guard(agents) is True
    first = agents.read_text(encoding="utf-8")
    assert first.startswith("# ")  # short title
    assert _block_count(first) == 1
    assert guard_block() in first

    # Re-running is a no-op and leaves the file byte-identical.
    assert install_codex_guard(agents) is False
    assert agents.read_text(encoding="utf-8") == first


def test_guard_preserves_surrounding_user_prose(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    prose = "# My project\n\nHand-written build notes the user cares about.\n"
    agents.write_text(prose, encoding="utf-8")

    assert install_codex_guard(agents) is True
    content = agents.read_text(encoding="utf-8")

    assert prose in content
    assert content.index("Hand-written build notes") < content.index(GUARD_START)
    assert _block_count(content) == 1
    # Idempotent for a file that already had prose.
    assert install_codex_guard(agents) is False
    assert agents.read_text(encoding="utf-8") == content


def test_guard_replaces_obsolete_block_without_duplicating(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    obsolete = (
        "# Team guide\n\n"
        "Intro the user wrote.\n\n"
        f"{GUARD_START}\n"
        "## Old mnemex guidance\n\nStale instructions from a prior version.\n"
        f"{GUARD_END}\n\n"
        "Closing notes the user wrote.\n"
    )
    agents.write_text(obsolete, encoding="utf-8")

    assert install_codex_guard(agents) is True
    content = agents.read_text(encoding="utf-8")

    # Exactly one block, updated content, obsolete content gone, prose kept.
    assert _block_count(content) == 1
    assert "Stale instructions from a prior version." not in content
    assert "Treat unavailable or uncertain semantic judgment as advisory." in content
    assert "Intro the user wrote." in content
    assert "Closing notes the user wrote." in content
    assert content.index("Intro the user wrote.") < content.index(GUARD_START)
    assert content.index(GUARD_END) < content.index("Closing notes the user wrote.")
    assert install_codex_guard(agents) is False


def test_guard_block_requires_explicit_override_and_never_advises_bypass() -> None:
    block = guard_block()

    # The four MCP guard tools the contract is built on.
    for tool in (
        "context_for",
        "check_proposed_change",
        "override_decision_guard",
        "index_path",
    ):
        assert tool in block

    # A block must halt the edit and require a human-approved, recorded override.
    assert "do not apply the edit unless a human explicitly approves" in block
    assert "record the actor and reason with `override_decision_guard`" in block
    assert "advisory" in block

    # It never advises bypassing/ignoring/disabling the guard, never fabricates
    # an override, and never claims Codex has verified hooks.
    lowered = block.lower()
    for forbidden in (
        "bypass",
        "ignore",
        "disable",
        "skip",
        "without a human",
        "auto-override",
        "override automatically",
        "hook",
    ):
        assert forbidden not in lowered


def _follow_guard_policy(
    block: str,
    *,
    blocked: bool,
    human_approved: bool,
    actor: str | None,
    reason: str | None,
) -> str:
    """Minimal faithful reading of the managed block's operating contract."""
    # The block is the source of truth for these rules.
    assert "do not apply the edit unless a human explicitly approves" in block
    assert "override_decision_guard" in block
    if not blocked:
        return "apply"
    if human_approved and actor and reason:
        return "override_decision_guard"
    return "do-not-apply"


def test_simulated_block_requires_an_explicit_override_call() -> None:
    block = guard_block()

    # A blocked result with no human approval is never auto-applied nor
    # auto-overridden.
    assert (
        _follow_guard_policy(
            block, blocked=True, human_approved=False, actor=None, reason=None
        )
        == "do-not-apply"
    )

    # Only an explicit, human-approved decision with actor + reason leads to an
    # override, and it goes through override_decision_guard.
    assert (
        _follow_guard_policy(
            block,
            blocked=True,
            human_approved=True,
            actor="release-owner",
            reason="approved architecture change",
        )
        == "override_decision_guard"
    )

    # A non-blocked result proceeds normally.
    assert (
        _follow_guard_policy(
            block, blocked=False, human_approved=False, actor=None, reason=None
        )
        == "apply"
    )


def test_atomic_write_preserves_original_and_cleans_up_on_failure(
    tmp_path: Path, monkeypatch
) -> None:
    agents = tmp_path / "AGENTS.md"
    original = "# Keep me\n\nThe user's untouched content.\n"
    agents.write_text(original, encoding="utf-8")

    def _boom(*_args, **_kwargs):
        raise PermissionError("simulated read-only target")

    monkeypatch.setattr("mnemex.codex_setup.os.replace", _boom)

    with pytest.raises(OSError):
        install_codex_guard(agents)

    # Original untouched (no partial write) and no temp file left behind.
    assert agents.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("*.mnemex-tmp")) == []


# --- `mnemex init --codex-guard` wiring ----------------------------------------


def test_init_without_codex_guard_leaves_agents_md_untouched(
    tmp_path: Path, capsys
) -> None:
    (tmp_path / "service.py").write_text("def ready():\n    return True\n")
    agents = tmp_path / "AGENTS.md"
    prose = "# Existing\n\nUnrelated notes.\n"
    agents.write_text(prose, encoding="utf-8")

    assert _init(str(tmp_path), no_index=True) == 0
    payload = json.loads(capsys.readouterr().out)

    assert "codex_guard" not in payload
    assert agents.read_text(encoding="utf-8") == prose


def test_init_codex_guard_writes_block_and_is_idempotent(
    tmp_path: Path, capsys
) -> None:
    (tmp_path / "service.py").write_text("def ready():\n    return True\n")

    assert _init(str(tmp_path), no_index=True, codex_guard=True) == 0
    payload = json.loads(capsys.readouterr().out)

    agents = tmp_path / "AGENTS.md"
    assert payload["status"] == "ready"
    assert payload["codex_guard"].replace("\\", "/").endswith("AGENTS.md")
    assert payload["codex_guard_changed"] is True
    first = agents.read_text(encoding="utf-8")
    assert _block_count(first) == 1

    # Second init run is idempotent for AGENTS.md.
    assert _init(str(tmp_path), no_index=True, codex_guard=True) == 0
    payload2 = json.loads(capsys.readouterr().out)
    assert payload2["codex_guard_changed"] is False
    assert agents.read_text(encoding="utf-8") == first


def test_init_codex_guard_fails_clearly_with_no_partial_write(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    agents = tmp_path / "AGENTS.md"
    original = "# Protected\n\nContent that must not be corrupted.\n"
    agents.write_text(original, encoding="utf-8")

    # Simulate a non-writable target: the atomic rename fails.
    def _boom(*_args, **_kwargs):
        raise PermissionError("simulated non-writable root")

    monkeypatch.setattr("mnemex.codex_setup.os.replace", _boom)

    assert _init(str(tmp_path), no_index=True, codex_guard=True) == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "error"
    assert payload["error"] == "codex-guard-write-failed"
    # No partial or corrupt write: the original file is intact and no temp file
    # is left behind.
    assert agents.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("*.mnemex-tmp")) == []
