# mnemex

**Local-first decision integrity for coding agents.**

Mnemex records a software decision against a code symbol and its content hash.
When that code moves, changes, or disappears, the decision becomes reviewable
instead of silently becoming stale context. MCP tools and the CLI retrieve only
the evidence needed for the current change.

It is not a generic chat-memory store. Its core unit is an auditable decision:

```text
decision -> code symbol -> content hash -> freshness -> evidence for an edit
```

## Why It Matters

Coding agents can remember a sentence such as "authentication is stateless" but
still lose track of the code it governed and whether that code has changed.
Mnemex keeps those facts connected:

- Decisions can be anchored to indexed Python or TypeScript/TSX symbols.
- Fresh, stale, and orphaned anchors are reported separately.
- `why` combines anchored decisions with caller context.
- The optional semantic guard records evidence, verdicts, and overrides. It
  blocks only a fresh, cited `contradiction` at confidence `>= 0.90`.

Core storage, indexing, retrieval, freshness, and deterministic constraints
stay local in SQLite. The OpenAI semantic judge is optional and disabled by
default.

## Architecture

```text
source files
    | index
    v
symbols + calls + imports -----------------------+
    | content hashes                             |
    v                                            |
anchored decisions in one SQLite database        |
    |                                             |
    +-- freshness / lifecycle / provenance       |
    +-- bounded retrieval / JIT context           |
    +-- optional semantic guard <-----------------+
    |
MCP (stdio or local HTTP) + CLI + project brain bundles
```

## Quick Start From This Checkout

Mnemex is installable from source and works without an embedding model, an
OpenAI key, or network access after dependencies are installed.

```bash
python -m pip install .
mnemex init . --db .mnemex/mnemex.sqlite3
mnemex doctor --db .mnemex/mnemex.sqlite3
```

For editable development:

```bash
python -m pip install -e ".[dev]"
python -m ruff check src tests tools
python -m pytest -q
```

The CI workflow exercises Python 3.10-3.13 on Linux, macOS, and Windows,
builds a wheel, and performs a clean-install smoke test.

## Demo Modes

### Local evidence demo

```bash
mnemex demo --offline
```

This no-network demo indexes `authenticate`, creates an explicitly tagged
stateless-authentication constraint, and proposes Redis-backed server sessions.
It deterministically reports **BLOCKED**, records an explicit override,
supersedes the decision, changes the anchor, and then reports staleness. Use
`--json` when a recording or test needs structured output.

### Optional semantic guard

Install the optional dependency and set credentials only for a semantic check:

```bash
python -m pip install ".[openai]"
set OPENAI_API_KEY=...
set MNEMEX_SEMANTIC_JUDGE_ENABLED=true
mnemex serve --db .mnemex/mnemex.sqlite3 --semantic-judge
mnemex demo --semantic --json
```

On PowerShell, use `$env:OPENAI_API_KEY` and
`$env:MNEMEX_SEMANTIC_JUDGE_ENABLED = "true"`. The provider uses the OpenAI
Responses API with the configured model. Missing credentials, a timeout, or
malformed provider output produces `unavailable` or `uncertain`; it never
blocks an edit. Every remote payload is sanitized, capped, and summarized in
the guard result.

## Codex MCP Setup

Run the server with stdio:

```bash
mnemex serve --db .mnemex/mnemex.sqlite3
```

Or create only the Mnemex entry in an explicit project config:

```bash
mnemex init . --db .mnemex/mnemex.sqlite3 --codex-config .codex/config.toml
```

The configured server entry is equivalent to:

```toml
[mcp_servers.mnemex]
command = "python"
args = ["-m", "mnemex", "serve", "--db", ".mnemex/mnemex.sqlite3"]
```

Mnemex speaks MCP over stdio, verified by an automated subprocess test that
performs the JSON-RPC initialize handshake, lists tools, and invokes them
(`tests/test_mcp_stdio_integration.py`). Codex is the intended primary client.

## Core Workflows

```bash
mnemex init . --db project.sqlite3
mnemex index ./src --db project.sqlite3
mnemex check src/auth.py "Move sessions to the server" --db project.sqlite3 --enforce-constraints
mnemex why authenticate --db project.sqlite3
mnemex review --db project.sqlite3
mnemex dashboard --db project.sqlite3
mnemex export project-brain.zip <memory-id> --db project.sqlite3
mnemex import project-brain.zip --db another-project.sqlite3
```

MCP exposes `remember_decision`, `check_proposed_change`,
`override_decision_guard`, `reconcile_stale_decision`, `why`,
`review_conflicts`, `context_for`, `export_brain`, and `import_brain`,
alongside retrieval, freshness, indexing, caller tracing, and AGENTS.md
generation.

### Deterministic constraints

An active decision becomes a local deterministic rule only when explicitly
tagged. For example, a decision stored through `remember_decision` with:

```text
tags: constraint:forbidden:server-side session
```

is reported by `check_proposed_change`. Add `--enforce-constraints` to the CLI
check workflow to block a fresh violation deterministically. Untagged decisions
remain advisory. This separation keeps local rules inspectable and prevents a
semantic provider from silently creating policy.

## Cross-Agent Continuity

The runnable walkthrough in
[examples/cross-agent-demo](examples/cross-agent-demo/README.md) demonstrates
two clients sharing a decision history through an explicit project-brain bundle:

1. Client A indexes `authenticate` and stores an anchored decision through MCP.
2. Client A exports the selected record from its SQLite brain.
3. Client B imports it into a separate SQLite brain.
4. Client B uses `why authenticate` and receives the same decision, anchor, and
   caller context, with immediate freshness validation.

The bundle contains selected records, anchors, hashes, provenance, audit data,
and optional AGENTS.md text. It does not copy a raw SQLite database.

## Security And Boundaries

- **Write-time redaction.** Secrets and common PII are stripped before
  storage: passwords and secret assignments, AWS/GitHub/OpenAI/Anthropic/
  Google/Stripe/Slack credentials, PEM private keys, JWTs, bearer tokens,
  connection strings, hex tokens, emails, phone numbers, and routable IP
  addresses. `<private>...</private>` sections are removed entirely.
  Loopback addresses and `commit <sha>` references are exempt so normal
  engineering text survives unmangled.
- **Zero telemetry.** Local mode makes no network call and never imports the
  `openai` package; the semantic judge is opt-in.
- **Inspectable remote payload.** `mnemex check ... --show-payload` prints the
  exact sanitized, bounded JSON that would be sent to a remote judge, its
  redaction count, and whether anything was actually sent. Every guard run
  records the payload hash and token count.
- **Bounded context.** Session briefs are capped at 800 estimated tokens; JIT
  context at 400; guard evidence at 800.
- **Honest self-check.** `mnemex doctor` probes the redaction pipeline with
  password, provider-key, and private-tag vectors before reporting ready.
- Local HTTP MCP has no built-in authentication. Bind it to `127.0.0.1` or use
  an authenticated gateway before exposing it outside the machine.
- Bundle import validates its contents and reports current anchor freshness.

## Agent Skill

Install the project skill from this checkout with Node 18 or later:

```bash
node npm/mnemex-skills/bin/mnemex-skills.cjs .agents/skills/mnemex
```

After the npm package is published, the same installer will be available as
`npx @mnemex/skills .agents/skills/mnemex`; the package remains private in
this checkout.

## Evidence And Benchmarks

The checked-in benchmark is a **context-delivery microbenchmark**, not a claim
about autonomous-agent quality or general token savings. It compares a bounded
raw-file exploration baseline with Mnemex's session brief plus JIT contexts on
three public repositories at recorded commits. Method, commands, and all
numbers are in
[benchmarks/2026-07-15-three-repositories.md](benchmarks/2026-07-15-three-repositories.md).

## Release Artifacts

The repository's CI builds a wheel and a portable source bundle. To create
local artifacts:

```bash
python -m pip install build
python -m build --wheel
python tools/build_release_bundle.py
```

External publishing to PyPI, npm, or GitHub Releases is a deployment action;
it is not performed by this repository.

## License

[MIT](LICENSE)
