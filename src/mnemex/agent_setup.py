"""Generalized, idempotent per-agent MCP setup for mnemex.

``mnemex setup <agent>`` writes only the mnemex MCP server entry into the
target agent's project-local config, preserving all other content and staying
byte-identical on re-run. It reuses the Codex TOML writer and the ``AGENTS.md``
guard writer from :mod:`mnemex.codex_setup`, so there is one implementation of
the careful merge/atomic-write behavior.

Only agents whose project-local MCP config format is known and stable are
listed. Adding another agent is a single registry entry once its config path
and shape are verified; the command never guesses a format and never clobbers
a user's existing config.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mnemex.codex_setup import _atomic_write, install_codex_guard, install_codex_mcp

__all__ = ["AGENTS", "AgentTarget", "install_mcp_json", "setup_agent"]


@dataclass(frozen=True)
class AgentTarget:
    """A supported agent and where its project-local MCP config lives."""

    key: str
    label: str
    config_relpath: str
    fmt: str  # "toml" or "json"
    top_level_key: str = "mcpServers"
    include_stdio_type: bool = False


# Project-local config targets. Paths are relative to the project root; the
# agent launches ``python -m mnemex serve`` as a stdio subprocess.
AGENTS: dict[str, AgentTarget] = {
    "codex": AgentTarget("codex", "Codex", ".codex/config.toml", "toml"),
    "claude-code": AgentTarget("claude-code", "Claude Code", ".mcp.json", "json"),
    "cursor": AgentTarget("cursor", "Cursor", ".cursor/mcp.json", "json"),
    "vscode": AgentTarget(
        "vscode",
        "VS Code (Copilot)",
        ".vscode/mcp.json",
        "json",
        top_level_key="servers",
        include_stdio_type=True,
    ),
}


def install_mcp_json(
    config_path: str | Path,
    db_path: str,
    *,
    top_level_key: str = "mcpServers",
    include_stdio_type: bool = False,
) -> bool:
    """Merge only the mnemex entry into a JSON MCP config, idempotently.

    Every other key and server is preserved. Returns ``True`` when the file
    changed and ``False`` when it was already current. Raises ``ValueError``
    when an existing file is present but is not a JSON object with a compatible
    server map, so user data is never overwritten.
    """
    target = Path(config_path)
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if existing.strip():
        try:
            data = json.loads(existing)
        except json.JSONDecodeError as exc:
            raise ValueError("existing-config-invalid-json") from exc
        if not isinstance(data, dict):
            raise ValueError("existing-config-not-object")
    else:
        data = {}

    servers = data.get(top_level_key, {})
    if not isinstance(servers, dict):
        raise ValueError("existing-config-server-map-invalid")

    entry: dict[str, object] = {
        "command": "python",
        "args": ["-m", "mnemex", "serve", "--db", db_path],
    }
    if include_stdio_type:
        entry = {"type": "stdio", **entry}

    servers = {**servers, "mnemex": entry}
    data = {**data, top_level_key: servers}
    updated = json.dumps(data, indent=2) + "\n"
    if updated == existing:
        return False
    _atomic_write(target, updated)
    return True


def setup_agent(
    agent: str,
    root: str | Path,
    db_path: str,
    *,
    guard: bool = False,
) -> dict[str, object]:
    """Write the mnemex MCP entry for one agent under an explicit project root.

    Returns a JSON-serializable report of what was written. Raises
    ``ValueError("unknown-agent")`` for an unsupported agent so the caller can
    surface a stable error without leaking internals.
    """
    try:
        target = AGENTS[agent]
    except KeyError as exc:
        raise ValueError("unknown-agent") from exc

    root_path = Path(root)
    config_path = root_path / target.config_relpath
    if target.fmt == "toml":
        changed = install_codex_mcp(config_path, db_path)
    elif target.fmt == "json":
        changed = install_mcp_json(
            config_path,
            db_path,
            top_level_key=target.top_level_key,
            include_stdio_type=target.include_stdio_type,
        )
    else:  # pragma: no cover - registry is the only source of fmt
        raise ValueError("unsupported-config-format")

    result: dict[str, object] = {
        "agent": target.key,
        "label": target.label,
        "config_path": str(config_path),
        "config_changed": changed,
    }
    if guard:
        agents_md = root_path / "AGENTS.md"
        result["guard_path"] = str(agents_md)
        result["guard_changed"] = install_codex_guard(agents_md)
    return result
