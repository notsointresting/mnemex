from __future__ import annotations

from pathlib import Path

from mnemex.codex_setup import install_codex_mcp


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
