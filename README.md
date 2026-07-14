# mnemex — Anchored Memory for AI Coding Agents

> **The MCP server that gives coding agents persistent, anchored memory — every decision bound to the exact line it's about, delivered just-in-time.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP Compatible](https://img.shields.io/badge/MCP-compatible-purple.svg)](https://modelcontextprotocol.io)

## What is mnemex?

**mnemex** is a local-first [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that solves the #1 pain point of AI coding agents: **they forget everything between sessions.**

Unlike simple memory stores, mnemex **anchors** every decision to a specific file and symbol in your codebase, stamps it with a content hash, and delivers relevant context **just-in-time** — the moment your agent touches that file — not as a wasteful session-start dump.

### The Problem

AI coding agents (Claude Code, Codex CLI, Cursor, Windsurf, Gemini CLI) suffer from two types of forgetting:

- **Fact forgetting** — "We decided to use JWT for auth" is lost between sessions
- **Shape forgetting** — "The auth module uses this specific pattern because..." vanishes every time

This leads to agents re-asking the same questions, contradicting past decisions, and wasting thousands of tokens re-reading files they already understood.

### The Solution

mnemex provides:

| Feature | What it does |
|---------|-------------|
| **Decision anchors** | Every memory is bound to a file + symbol + content hash |
| **Just-in-time delivery** | Context injected via PreToolUse hook at edit time (≤400 tokens) |
| **Automatic staleness** | When anchored code changes, memories are flagged stale |
| **Hybrid retrieval** | BM25 + vector (sqlite-vec) fused via RRF — no ML model required |
| **Token governor** | Hard caps (800 session / 400 JIT) — never exceeds budget |
| **Secret stripping** | PII and credentials stripped deterministically at write time |
| **Self-updating AGENTS.md** | Auto-generates and maintains the cross-agent config file |

## Quick Start

### Installation

```bash
pip install mnemex
```

No ML model download required. Works immediately in BM25-only mode.

### Run the MCP Server

```bash
python -m mnemex serve --db project.sqlite3
```

### Configure in Claude Code

Add to your MCP settings:

```json
{
  "mcpServers": {
    "mnemex": {
      "command": "python",
      "args": ["-m", "mnemex", "serve", "--db", "project.sqlite3"]
    }
  }
}
```

### Basic Usage

```python
from mnemex.storage import Storage
from mnemex.anchors import remember, check_freshness
from mnemex.retrieval import recall

# Open the brain
storage = Storage("project.sqlite3")

# Remember a decision, anchored to code
remember(storage, "Use signed cookies for auth sessions",
         anchor=Anchor(file="src/auth.py", symbol="authenticate"),
         rationale="Keeps request handling stateless")

# Recall relevant context (BM25, no ML needed)
result = recall(storage, "authentication sessions", max_tokens=400)

# Check what's gone stale
reports = check_freshness(storage)
for r in reports:
    if r.status == "stale":
        print(f"⚠️  {r.memory_id} — code changed since decision was made")
```

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                    mnemex (core)                       │
│                                                       │
│   ┌───────────────────────────────────────────────┐   │
│   │              ONE SQLite file                   │   │
│   │  ┌──────────────┐   ┌─────────────────────┐  │   │
│   │  │  structural  │◀──│  episodic memories   │  │   │
│   │  │  nodes/edges │ANCHOR decisions/conventions│  │   │
│   │  │  (hash)      │   │  + vec + fts5        │  │   │
│   │  └──────────────┘   └─────────────────────┘  │   │
│   └───────────────────────────────────────────────┘   │
│           │                                           │
│   Fusion engine (RRF) · Token governor · Staleness    │
│           │                                           │
│   ┌───────▼───────────────────────────────────────┐   │
│   │      MCP server (FastMCP) + HOOKS             │   │
│   │  SessionStart · PreToolUse · Stop             │   │
│   └───────────────────────────────────────────────┘   │
└───────────────────────┬───────────────────────────────┘
                        │ MCP (stdio)
        ┌───────────────┼───────────────┐
   Claude Code      Codex CLI       Cursor/Windsurf
```

**One SQLite file. No cloud. No ML required. Local-first.**

## MCP Tools (10 tools)

| Tool | Description |
|------|-------------|
| `remember_decision` | Store a decision, optionally anchored to code |
| `recall_memories` | Hybrid BM25+vector retrieval with token governor |
| `forget_memory` | Remove a memory by ID |
| `check_memory_freshness` | Report fresh/stale/orphaned status |
| `context_for` | JIT context for a file (≤400 tokens) |
| `get_context_brief` | Session-start brief (≤800 tokens) |
| `why` | Explain why a symbol is designed this way (decision + callers) |
| `trace_callers_tool` | Who calls/references this symbol |
| `index_path` | Index files into the structural graph |
| `generate_agents_md` | Auto-generate AGENTS.md |

## Key Concepts

### Decision Anchors

Every memory can be **anchored** to a structural location (file + symbol). When that code changes, the memory is automatically flagged stale:

- **Fresh** — the anchored code hasn't changed
- **Stale** — the code changed; the decision may need revisiting
- **Orphaned** — the anchored symbol was deleted
- **Unanchored** — a global fact not tied to specific code

### Token Governor

mnemex **guarantees** it never exceeds your token budget:

- Session start brief: ≤800 tokens
- JIT (PreToolUse) injection: ≤400 tokens
- Memories that don't fit are **dropped** (not truncated), and reported

### No-ML Mode

Works out of the box with **zero model download**. BM25 (FTS5) handles keyword retrieval. When you add an embedder, it upgrades to hybrid BM25+vector via Reciprocal Rank Fusion automatically.

### Security

Secrets and PII are stripped **at write time** before persistence:
- AWS keys, GitHub tokens, JWTs, PEM private keys
- Connection strings with passwords
- Email addresses, phone numbers, IP addresses
- `<private>` tagged blocks are removed entirely
- Every redaction is recorded in an audit log

## Benchmarks

On mnemex's own source (10 Python files, 120 nodes):

| Metric | Value |
|--------|-------|
| Baseline tokens (reading files) | 20,944 |
| JIT tokens (session + 5 contexts) | 370 |
| **Token savings** | **98.2%** |
| **Compression ratio** | **56.6×** |
| Index time | 0.07s |
| JIT latency | 0.9ms |

## CLI

```bash
# Run the MCP server
python -m mnemex serve --db project.sqlite3

# Index a codebase
python -m mnemex index ./src --db project.sqlite3

# Run benchmarks
python -m mnemex benchmark ./src
```

## Works With

mnemex is compatible with any MCP-supporting agent:

- **Claude Code** (hooks: SessionStart + PreToolUse)
- **Codex CLI** (MCP tools)
- **Cursor** (MCP tools)
- **Windsurf** (MCP tools)
- **Gemini CLI** (MCP tools)
- **Cline / Roo Code** (MCP tools)
- **Any MCP client** (stdio transport)

## Project Structure

```
src/mnemex/
├── storage.py      # SQLite + sqlite-vec + FTS5 schema
├── anchors.py      # Anchor resolution + hash stamping + staleness
├── retrieval.py    # BM25 + vector RRF fusion + token governor
├── indexer.py      # Structural backend adapter (Python AST)
├── server.py       # FastMCP server (10 tools)
├── hooks.py        # SessionStart / PreToolUse / Stop hooks
├── agents_md.py    # why() fusion + AGENTS.md generator + staleness watcher
├── security.py     # Secret stripping + audit log
└── __main__.py     # CLI entry point
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run linter
python -m ruff check .

# Run tests (145 tests)
python -m pytest -v
```

## Comparison with Alternatives

| Feature | mnemex | agentmemory | codebase-memory | engram |
|---------|--------|-------------|-----------------|--------|
| Anchored to code symbols | ✅ | ❌ | ❌ | ❌ |
| Automatic staleness detection | ✅ | ❌ | ❌ | ❌ |
| JIT hook injection | ✅ | ❌ | ❌ | ❌ |
| Token budget governor | ✅ | ❌ | ❌ | ❌ |
| No ML required | ✅ | ❌ | ✅ | ✅ |
| One SQLite file | ✅ | ❌ | ❌ | ✅ |
| Secret stripping at write | ✅ | ❌ | ❌ | ❌ |
| Self-updating AGENTS.md | ✅ | ❌ | ❌ | ❌ |
| MCP server | ✅ | ✅ | ✅ | ✅ |

## FAQ

### How is mnemex different from just using CLAUDE.md?

CLAUDE.md is static and manually maintained. mnemex:
- Automatically detects when decisions go stale (code changed)
- Delivers only relevant context per-file (not everything at session start)
- Respects token budgets (CLAUDE.md grows without bound)
- Works across all MCP-supporting agents (not just Claude)

### Does it need a GPU or embedding model?

No. mnemex works in **no-ML mode** (BM25 keyword search) by default. You can optionally provide an embedder for hybrid retrieval, but it's not required.

### How does the token governor work?

Every injection path takes a hard `max_tokens` budget. The governor ranks memories by relevance, includes them in order while the running sum fits, and **drops** (never truncates) memories that exceed the cap. It reports what was dropped so the agent can request more if needed.

### What happens when I refactor code?

Anchored memories are automatically flagged **stale** when their symbol's content hash changes, and **orphaned** when the symbol is deleted. The agent sees these flags and can reconcile (update or remove the decision).

## License

MIT

## Contributing

Contributions welcome. Please ensure:
- `python -m ruff check .` passes
- `python -m pytest` passes (145+ tests)
- New features include tests
- Security-sensitive changes require Phase 6 gate validation

---

**mnemex** — *Not another memory list. A brain that anchors every decision to the exact line it's about — and hands it back the instant your agent touches that line.*
