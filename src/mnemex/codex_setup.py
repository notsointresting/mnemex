"""Idempotent project-local Codex MCP configuration installer."""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["install_codex_mcp"]

_SECTION = re.compile(r"(?ms)^\[mcp_servers\.mnemex\]\n.*?(?=^\[|\Z)")


def install_codex_mcp(config_path: str | Path, db_path: str) -> bool:
    """Add or replace only the mnemex section of an explicit config file."""
    target = Path(config_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    section = _section(db_path)
    if _SECTION.search(existing):
        updated = _SECTION.sub(section, existing).rstrip() + "\n"
    else:
        separator = "" if not existing or existing.endswith("\n\n") else "\n"
        updated = f"{existing}{separator}{section}"
    if updated == existing:
        return False
    target.write_text(updated, encoding="utf-8")
    return True


def _section(db_path: str) -> str:
    escaped = db_path.replace("\\", "\\\\").replace('"', '\\"')
    return (
        "[mcp_servers.mnemex]\n"
        'command = "python"\n'
        f'args = ["-m", "mnemex", "serve", "--db", "{escaped}"]\n'
    )
