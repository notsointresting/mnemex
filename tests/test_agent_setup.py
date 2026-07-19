"""Tests for generalized per-agent MCP setup (`mnemex setup <agent>`)."""

from __future__ import annotations

import json

import pytest

from mnemex.__main__ import main
from mnemex.agent_setup import AGENTS, install_mcp_json, setup_agent


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_cursor_setup_creates_project_config(tmp_path):
    result = setup_agent("cursor", tmp_path, ".mnemex/mnemex.sqlite3")
    cfg = tmp_path / ".cursor" / "mcp.json"
    assert cfg.is_file()
    assert _read_json(cfg)["mcpServers"]["mnemex"] == {
        "command": "python",
        "args": ["-m", "mnemex", "serve", "--db", ".mnemex/mnemex.sqlite3"],
    }
    assert result["config_changed"] is True
    assert result["label"] == "Cursor"


def test_claude_code_writes_root_mcp_json(tmp_path):
    setup_agent("claude-code", tmp_path, "db.sqlite3")
    assert "mnemex" in _read_json(tmp_path / ".mcp.json")["mcpServers"]


def test_vscode_uses_servers_key_with_stdio_type(tmp_path):
    setup_agent("vscode", tmp_path, "db.sqlite3")
    entry = _read_json(tmp_path / ".vscode" / "mcp.json")["servers"]["mnemex"]
    assert entry["type"] == "stdio"
    assert entry["command"] == "python"


def test_codex_setup_writes_toml_section(tmp_path):
    setup_agent("codex", tmp_path, "db.sqlite3")
    text = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.mnemex]" in text


def test_setup_is_idempotent(tmp_path):
    assert setup_agent("cursor", tmp_path, "db.sqlite3")["config_changed"] is True
    second = setup_agent("cursor", tmp_path, "db.sqlite3")
    assert second["config_changed"] is False


def test_setup_preserves_existing_servers(tmp_path):
    cfg = tmp_path / ".cursor" / "mcp.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8"
    )
    setup_agent("cursor", tmp_path, "db.sqlite3")
    servers = _read_json(cfg)["mcpServers"]
    assert "other" in servers and "mnemex" in servers


def test_setup_replaces_stale_mnemex_entry(tmp_path):
    cfg = tmp_path / ".cursor" / "mcp.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps({"mcpServers": {"mnemex": {"command": "old"}}}), encoding="utf-8"
    )
    setup_agent("cursor", tmp_path, "new.sqlite3")
    assert _read_json(cfg)["mcpServers"]["mnemex"]["args"][-1] == "new.sqlite3"


def test_install_mcp_json_rejects_invalid_json(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        install_mcp_json(cfg, "db.sqlite3")


def test_unknown_agent_raises(tmp_path):
    with pytest.raises(ValueError):
        setup_agent("does-not-exist", tmp_path, "db.sqlite3")


def test_setup_guard_writes_agents_md(tmp_path):
    result = setup_agent("claude-code", tmp_path, "db.sqlite3", guard=True)
    assert (tmp_path / "AGENTS.md").is_file()
    assert result["guard_changed"] is True


def test_cli_setup_cursor_reports_ready(tmp_path, capsys):
    code = main(["setup", "cursor", str(tmp_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ready"
    assert (tmp_path / ".cursor" / "mcp.json").is_file()


def test_cli_setup_unknown_agent_lists_valid(tmp_path, capsys):
    code = main(["setup", "nope", str(tmp_path)])
    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "unknown-agent"
    assert set(out["valid_agents"]) == set(AGENTS)
