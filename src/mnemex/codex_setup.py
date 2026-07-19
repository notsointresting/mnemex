"""Idempotent project-local Codex MCP configuration installer.

Two explicit, opt-in setup helpers:

* :func:`install_codex_mcp` writes only the ``[mcp_servers.mnemex]`` section of
  a project-local Codex ``config.toml`` (stdio command, no HTTP, no global
  config).
* :func:`install_codex_guard` writes a marker-delimited "decision guard" block
  to a project ``AGENTS.md``. It is an operating contract for the agent backed
  by real MCP tool calls; it never claims Codex has verified hooks and never
  creates an override automatically.

Both are byte-identical on re-run and preserve all user-authored content
outside the region they own.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

__all__ = ["install_codex_mcp", "install_codex_guard"]

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


# --- Codex Guard Mode: managed AGENTS.md block ---------------------------------

GUARD_START = "<!-- mnemex:codex-guard:start -->"
GUARD_END = "<!-- mnemex:codex-guard:end -->"

_GUARD_BLOCK_RE = re.compile(
    re.escape(GUARD_START) + r".*?" + re.escape(GUARD_END),
    re.DOTALL,
)

# Short title used only when creating a brand-new AGENTS.md.
_GUARD_TITLE = "# Agent guide"

# Body between the markers. This is an operating contract executed through the
# existing MCP tools; it deliberately never advises bypassing, ignoring, or
# disabling the guard, and never fabricates an override.
_GUARD_BODY = (
    "## Mnemex decision guard\n"
    "\n"
    "Before editing a file, call `context_for` for that path.\n"
    "Before applying a material change, call `check_proposed_change` with the "
    "path,\n"
    "a concise patch summary, and constraint enforcement enabled.\n"
    "If Mnemex blocks, do not apply the edit unless a human explicitly approves "
    "an\n"
    "override; record the actor and reason with `override_decision_guard`.\n"
    "After an accepted edit, call `index_path` for the changed path and "
    "reconcile any\n"
    "stale cited decision rather than silently rewriting it.\n"
    "Treat unavailable or uncertain semantic judgment as advisory."
)


def guard_block() -> str:
    """Return the canonical marker-delimited guard block (no trailing newline)."""
    return f"{GUARD_START}\n{_GUARD_BODY}\n{GUARD_END}"


def install_codex_guard(agents_md_path: str | Path) -> bool:
    """Write the idempotent decision-guard block to a project ``AGENTS.md``.

    * Preserves all user-authored content before and after the managed block.
    * Replaces only the marked block on update (no duplication).
    * Creates a titled file containing just the block when none exists.
    * Writes atomically so a failure never leaves a partial/corrupt file.

    Returns ``True`` when the file changed, ``False`` when already current.
    """
    target = Path(agents_md_path)
    block = guard_block()
    existing = target.read_text(encoding="utf-8") if target.is_file() else None

    if existing is None:
        updated = f"{_GUARD_TITLE}\n\n{block}\n"
    elif _GUARD_BLOCK_RE.search(existing):
        updated = _replace_guard_blocks(existing, block)
    elif existing == "":
        updated = f"{block}\n"
    else:
        if existing.endswith("\n\n"):
            separator = ""
        elif existing.endswith("\n"):
            separator = "\n"
        else:
            separator = "\n\n"
        updated = f"{existing}{separator}{block}\n"

    if updated == existing:
        return False
    _atomic_write(target, updated)
    return True


def _replace_guard_blocks(existing: str, block: str) -> str:
    """Replace the guard block in place; drop any duplicate marked blocks.

    Replacing in place keeps re-runs byte-identical. Extra marked blocks (a
    malformed file) are removed so the result always holds exactly one block.
    """
    matches = list(_GUARD_BLOCK_RE.finditer(existing))
    first_start = matches[0].start()
    stripped = existing
    for match in reversed(matches):
        stripped = stripped[: match.start()] + stripped[match.end() :]
    return stripped[:first_start] + block + stripped[first_start:]


def _atomic_write(target: Path, content: str) -> None:
    """Write ``content`` to ``target`` atomically via a same-directory temp file.

    On any OS error the partially written temp file is removed and the original
    ``target`` is left untouched, so callers never observe a partial write.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".mnemex-tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
