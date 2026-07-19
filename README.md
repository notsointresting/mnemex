# mnemex

Memory systems retrieve relevant history. ADR tools check written rules.
**Mnemex verifies whether a past decision still governs the code — by content
hash, not by vibes — then gives the agent the minimum evidence for the current
edit.**

## Judge path — no key, no vector extra (under 60 seconds)

Install the wheel, then run the deterministic offline demonstration. It needs
no API key, embedding model, vector extension, or network access after install.

```bash
python -m pip install dist/mnemex-*.whl
python -m mnemex demo --offline
```

Expected evidence includes a deterministic fresh-decision block:

```text
BLOCKED: Deterministic constraint violation: Forbidden phrase appears in the proposed change.
Status       fresh at guard time
```

The same demo then records an explicit override, supersedes the decision,
changes its anchor, and shows the resulting stale state. For structured output
suited to an automated check, use `python -m mnemex demo --offline --json`.

## The important distinction: violation vs. evolution

The same guard should reject a fresh decision violation and stand aside when a
legitimate change preserves the decision. The checked-in, deterministic replay
fixture demonstrates both cases; it is not a live provider claim.

| Fresh anchored decision | Proposed change | Result |
|---|---|---|
| Payment writes must be idempotent | Remove the idempotency check and retry after a ledger write | **BLOCKED** — `contradiction`, confidence `0.96` |
| Payment writes must be idempotent | Extract the same idempotency check into a helper before the ledger write | **Advisory / allowed** — `compatible` |

Run the fixture from [examples/violation-vs-evolution](examples/violation-vs-evolution/README.md).

## Recorded-fixture scorecard

<!-- codex-guard-scorecard:start -->
**Synthetic recorded-fixture replay, not a live-agent outcome claim.** The
numbers below are generated from
[the checked-in results JSON](benchmarks/results/codex-guard-scorecard.json)
and are locked against README drift by `tests/test_scorecard.py`.

| Metric | Recorded fixture replay |
|---|---:|
| Decision violations caught | 2/2 |
| False blocks on legitimate evolution | 0/2 |
| Stale decisions correctly advisory | 1/1 |
| Average recorded treatment context tokens / cap | 0/800 (1 observation) |

Reproduce:

```bash
python tools/evaluate_codex_guard.py benchmarks/codex-guard-fixtures/example-results.synthetic.json --format json
```
<!-- codex-guard-scorecard:end -->

## What it is

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

| Category | Recall | Enforcement | Knows when stale (content hash) | Local-only | Audited override |
|---|---|---|---|---|---|
| Mem0 / Zep-class memory | Yes | No | No | Varies | No |
| adr-kit-class enforcement | Manual rules | Yes | No | Yes | Varies |
| Mnemex | Bounded FTS5; optional vectors | Fresh, explicit decisions | Yes | Yes in core mode | Yes |

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
OpenAI key, or network access after dependencies are installed. The default
install is **core mode**: FastMCP plus SQLite/FTS5 keyword (BM25) retrieval. The
Mnemex core does not require or load the sqlite-vec native extension.

```bash
python -m pip install .                 # core: FTS5/BM25 retrieval
mnemex init . --db .mnemex/mnemex.sqlite3
mnemex doctor --db .mnemex/mnemex.sqlite3
```

Optional extras layer onto the same single SQLite brain; there is no second
database:

```bash
python -m pip install ".[vector]"      # optional hybrid vector retrieval (sqlite-vec)
python -m pip install ".[openai]"      # optional GPT-5.6 semantic judge
python -m pip install ".[vector,openai]"
```

`MNEMEX_NO_VEC=1` force-disables vector loading even when the extra is present.
In core mode `mnemex doctor` reports `retrieval_mode: bm25-only` with a stable
`sqlite_vec_status` such as `package-not-installed` or
`disabled-by-environment`; missing vector support is not a doctor failure.

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

## Agent Setup

One command wires the mnemex MCP server into a project-local agent config. It
writes **only** the `mnemex` entry, leaves every other setting untouched, and
is byte-identical on re-run:

```bash
mnemex setup cursor                 # or: claude-code | codex | vscode
mnemex setup claude-code --guard    # also write the decision-guard block to AGENTS.md
```

| Agent | Config written (project-local) |
|---|---|
| `claude-code` | `.mcp.json` |
| `cursor` | `.cursor/mcp.json` |
| `vscode` | `.vscode/mcp.json` |
| `codex` | `.codex/config.toml` |

Each writes the stdio launch entry `python -m mnemex serve --db
<root>/.mnemex/mnemex.sqlite3` and prints a JSON report of the exact path it
changed. An existing config that is not valid JSON is reported as an error and
left untouched rather than overwritten. Restart the agent afterward so it
reloads the MCP config.

Install without cloning — straight from the repository (verified end-to-end),
then run setup:

```bash
pip install git+https://github.com/notsointresting/mnemex
# ephemeral, no install:
#   uvx --from git+https://github.com/notsointresting/mnemex mnemex setup cursor
mnemex setup cursor
```

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

### Codex Guard Mode

Add `--codex-guard` to opt in to a managed guard block in the project-root
`AGENTS.md`:

```bash
mnemex init . --db .mnemex/mnemex.sqlite3 --codex-config .codex/config.toml --codex-guard
```

The write is explicit and idempotent. Mnemex inserts (or replaces) only the
region between `<!-- mnemex:codex-guard:start -->` and
`<!-- mnemex:codex-guard:end -->`, preserving all surrounding user-authored
content; re-running the command is byte-identical. The block instructs the
agent to call `context_for` before editing a path, `check_proposed_change`
before a material change, and to record an explicit `override_decision_guard`
rather than silently bypassing a block. It is an operating contract backed by
MCP calls, not an unverified editor hook, and it never creates an override
automatically.

Mnemex speaks MCP over stdio, verified by an automated subprocess test that
performs the JSON-RPC initialize handshake, lists tools, and invokes them
(`tests/test_mcp_stdio_integration.py`). Codex is the intended primary client.

## Core Workflows

```bash
mnemex init . --db project.sqlite3
mnemex index ./src --db project.sqlite3
mnemex check src/auth.py "Move sessions to the server" --db project.sqlite3 --enforce-constraints
mnemex check-diff --staged --db project.sqlite3 --enforce-constraints
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

### Staged-diff decision gate

`mnemex check-diff` runs the same proposed-change guard over a real unified diff
without executing project code. It takes either a staged diff
(`--staged`, captured through `git diff --cached` with no shell and a timeout)
or a file (`--diff-file change.diff` for deterministic input):

```bash
mnemex check-diff --staged --db project.sqlite3 --enforce-constraints
mnemex check-diff --diff-file change.diff --db project.sqlite3 --format markdown
```

The diff is **never reindexed before it is checked**, so a block still requires
a decision that is fresh in the already-indexed brain; every report is stamped
`freshness_basis: indexed-brain` with an explicit before-change warning. Checks
are file-scoped (the node schema stores `line_start`, so this does not claim
hunk-to-symbol precision), paths that escape the project root are rejected, and
binary diffs are skipped as advisory. The command exits `2` when a file is
blocked, `1` when the diff could not be acquired or a file could not be
evaluated, and `0` otherwise. The live Codex pre-edit MCP guard remains the
authoritative path; `check-diff` is a documented second line of defense.

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

`build_release_bundle.py` writes the portable source zip and a
`dist/SHA256SUMS.txt` covering the wheel and source zip (standard-library
`hashlib`, coreutils format). The release ships a wheel, a source zip, and that
checksum file only; it refuses to publish an unsigned standalone executable.

External publishing to PyPI, npm, or GitHub Releases is a deployment action;
it is not performed by this repository.

## Built With Codex

Mnemex was built in collaboration with OpenAI Codex using GPT-5.6, and GPT-5.6
also runs inside the product as the opt-in semantic judge.

- **Where Codex accelerated:** SQLite schema and migrations, MCP tool
  workflows, the structural indexer, cross-platform test coverage, the
  deterministic demo, and release checks.
- **Where the human made the key calls:** keeping the block deterministic by
  default and gating the LLM behind the anchor layer; the local-first
  constraint (no network call in local mode); anchoring decision validity to
  symbol content hashes; treating judge output as bounded evidence rather than
  policy.
- **The judge's own division of labor mirrors the build:** deterministic code
  selects and bounds the evidence; GPT-5.6 makes only the semantic call — see
  [examples/violation-vs-evolution](examples/violation-vs-evolution/README.md).

<!-- FILL before submission: Codex session ID for the core build thread, and
     one or two sentences citing a specific decision made in a Codex session
     (e.g. the deterministic-by-default tradeoff), per the submission rules. -->

## License

[MIT](LICENSE)
