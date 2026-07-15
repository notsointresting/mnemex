# Cross-Agent Continuity Demo

This walkthrough uses two separate local SQLite brains. It proves portable
decision continuity without copying a database or requiring a cloud service.

## Prerequisites

From the repository root:

```bash
python -m pip install .
```

Use `mnemex` below on macOS/Linux. On Windows PowerShell, the commands are the
same after the package is installed.

## 1. Client A Creates An Anchored Decision

```bash
mnemex init examples/cross-agent-demo --db .mnemex/client-a.sqlite3
mnemex serve --db .mnemex/client-a.sqlite3
```

Connect an MCP client to that stdio server and call:

```json
{
  "name": "remember_decision",
  "arguments": {
    "content": "Authentication must remain stateless.",
    "rationale": "Requests should not require server-side session storage.",
    "anchor_file": "examples/cross-agent-demo/src/auth.py",
    "anchor_symbol": "authenticate",
    "tags": "auth"
  }
}
```

The result contains a generated `memory_id`. Save it as `<memory-id>` for the
next command. The decision is anchored to `authenticate` and stamped with that
symbol's current hash.

In Codex, the same operation is a normal MCP tool call after the project MCP
configuration points to this database.

## 2. Client A Exports Selected History

Stop the server, then export only the selected decision:

```bash
mnemex export .mnemex/auth-brain.zip <memory-id> --db .mnemex/client-a.sqlite3
```

The ZIP contains canonical records, relevant anchor nodes, decision metadata,
redaction audits, a manifest with hashes, and AGENTS.md when supplied. It does
not contain `client-a.sqlite3`.

## 3. Client B Imports And Inspects The Decision

```bash
mnemex init examples/cross-agent-demo --db .mnemex/client-b.sqlite3
mnemex import .mnemex/auth-brain.zip --db .mnemex/client-b.sqlite3
mnemex why authenticate --db .mnemex/client-b.sqlite3
```

The import result reports the imported ID mapping and immediate anchor
freshness. The `why` result includes the imported decision and the caller graph
(`authenticate` calls `validate_token`).

## 4. Observe Freshness After A Change

Edit `examples/cross-agent-demo/src/auth.py`, then re-index Client B's source:

```bash
mnemex index examples/cross-agent-demo/src --db .mnemex/client-b.sqlite3
```

The anchored decision is now eligible for stale-decision review because the
symbol content hash changed. Use the MCP `check_memory_freshness` tool or
`reconcile_stale_decision` to inspect and record the follow-up.

## Expected Result

Two clients can carry a selected, anchored decision between local brains with
provenance and freshness retained. This is continuity of a code decision, not a
shared transcript or a remote-memory synchronization service.
