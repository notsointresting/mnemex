&#x20;mnemex Research Report



&#x20; Audit date: 2026-07-15

&#x20; Repository: C:\\Users\\Institue\\Mem

&#x20; Version: 0.1.0



&#x20; Executive summary



&#x20; mnemex already has a credible technical core:



&#x20; - Symbol-anchored decisions with content-hash freshness.

&#x20; - Fresh/stale/orphaned/unanchored lifecycle states.

&#x20; - BM25 plus optional sqlite-vec retrieval with RRF.

&#x20; - Hard token-governor logic.

&#x20; - A Python structural index and caller graph.

&#x20; - Ten registered MCP tools.

&#x20; - 145 passing tests.



&#x20; But the repository currently overstates several product-level capabilities:



&#x20; 1. No GPT-5.6 or OpenAI integration exists.

&#x20; 2. Security redaction is not connected to any persistence path. Secrets passed through remember\_decision are stored

&#x20; unchanged.

&#x20; 3. “JIT hooks” are Python functions, not installed runtime hooks.

&#x20; 4. context\_for(path) is not truly file-scoped. It searches memory text for the file stem.

&#x20; 5. \*\*The MCP why and generate\_agents\_md tools use preliminary implementations instead of the fuller implementations

&#x20; already present in agents\_md.py.

&#x20; 6. MCP-level token limits are not hard limits. Callers can request more than 400/800 tokens.

&#x20; 7. Distribution, cross-agent setup, HTTP transport, real embedding providers, and release benchmarking remain

&#x20; unimplemented.



&#x20; The best submission path is not “add GPT chat.” It is:



&#x20; │ Use GPT-5.6 as a semantic decision-constraint judge over deterministic, code-anchored candidates—warning Codex

&#x20; before it contradicts a fresh architectural decision.



&#x20; That creates a real division of labor:



&#x20; - SQLite/FTS/hash logic supplies deterministic evidence.

&#x20; - GPT-5.6 interprets semantic conflicts and proposed changes.

&#x20; - Codex remains the coding agent.

&#x20; - mnemex becomes the shared enforcement layer.



&#x20; ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────



&#x20; 1. Current State Audit



&#x20; 1.1 Verification performed



&#x20; I read all requested files:



&#x20; - All 10 src/mnemex/\*.py modules.

&#x20; - All 12 tests/\*.py modules.

&#x20; - DECISIONS.md

&#x20; - README.md

&#x20; - pyproject.toml

&#x20; - implementation-plan-opus48.md

&#x20; - codebase-mind-improved-plan.md

&#x20; - goal-command.md



&#x20; Validation results:



&#x20; python -m pytest -q

&#x20; 145 passed in 4.43s



&#x20; python -m ruff check .

&#x20; All checks passed!



&#x20; Static analysis also reported no errors.



&#x20; There are 106 explicit test functions; parametrization expands these to 145 collected test cases. The README’s “145

&#x20; tests” claim is therefore current.



&#x20; 1.2 Maturity definitions



&#x20; - Complete: Core behavior exists, is wired into its intended public path, and has meaningful tests.

&#x20; - Partial: Useful implementation exists, but important requirements, integrations, or correctness boundaries are

&#x20; missing.

&#x20; - Stub: Registered/API-visible, but deliberately preliminary or placeholder behavior remains.



&#x20; 1.3 Implemented feature inventory



&#x20; ┌────────────┬──────────────────────────────┬─────────────────────────────────────────────────────────────────────┐

&#x20; │ Feature    │ Maturity                     │ Evidence and assessment                                             │

&#x20; ├────────────┼──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤

&#x20; │ SQLite     │ Complete                     │ Opens/closes one SQLite database, supports context-manager use, and │

&#x20; │ storage    │                              │ rejects unsupported schema versions. src/mnemex/storage.py:131–321. │

&#x20; │ lifecycle  │                              │                                                                     │

&#x20; ├────────────┼──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤

&#x20; │ Nodes,     │ Complete for v1              │ Tables are created at storage.py:24–60; FTS5 at :63; optional vec0  │

&#x20; │ edges,     │                              │ table at :97. Schema is versioned, but there is no migration        │

&#x20; │ memories   │                              │ mechanism beyond accepting versions 0/1.                            │

&#x20; │ schema     │                              │                                                                     │

&#x20; ├────────────┼──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤

&#x20; │ Memory     │ Complete                     │ agent-private, project-shared, and user-global are validated in     │

&#x20; │ scopes     │                              │ Python and by a SQL CHECK. Scope-isolation tests cover all          │

&#x20; │            │                              │ non-empty subsets.                                                  │

&#x20; ├────────────┼──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤

&#x20; │ Node CRUD  │ Complete                     │ Deterministic upsert/get/find/delete behavior. Lookup is exact      │

&#x20; │ and exact  │                              │ (file, name), not fuzzy.                                            │

&#x20; │ symbol     │                              │                                                                     │

&#x20; │ lookup     │                              │                                                                     │

&#x20; ├────────────┼──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤

&#x20; │ Memory     │ Complete                     │ Insert/get/list/delete with FTS insert/delete triggers. Tested for  │

&#x20; │ CRUD and   │                              │ rollback and index integrity.                                       │

&#x20; │ FTS        │                              │                                                                     │

&#x20; │ synchroniz │                              │                                                                     │

&#x20; │ ation      │                              │                                                                     │

&#x20; ├────────────┼──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤

&#x20; │ Optional   │ Complete                     │ Gracefully falls back to BM25 when extension loading fails or       │

&#x20; │ sqlite-vec │                              │ MNEMEX\_NO\_VEC is set. storage.py:253–322.                           │

&#x20; │ loading    │                              │                                                                     │

&#x20; ├────────────┼──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤

&#x20; │ Anchor     │ Complete                     │ Supports node ID or exact file+symbol, rejecting mixed/incomplete   │

&#x20; │ input      │                              │ references. anchors.py:13–42.                                       │

&#x20; │ model      │                              │                                                                     │

&#x20; ├────────────┼──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤

&#x20; │ Anchor     │ Complete                     │ Exact lookup with explicit not-found and ambiguity failures.        │

&#x20; │ resolution │                              │ anchors.py:70–101.                                                  │

&#x20; ├────────────┼──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤

&#x20; │ Remember/f │ Complete as storage logic    │ Hash-stamps the resolved node and persists immutable records.       │

&#x20; │ orget core │                              │ anchors.py:104–141. It does not sanitize writes.                    │

&#x20; ├────────────┼──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤

&#x20; │ Freshness  │ Complete                     │ Fresh, stale, orphaned, and unanchored states are deterministic.    │

&#x20; │ lifecycle  │                              │ anchors.py:53–67,144–207.                                           │

&#x20; ├────────────┼──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤

&#x20; │ BM25       │ Complete                     │ Safe FTS query construction, scope filtering, deterministic         │

&#x20; │ retrieval  │                              │ ranking. retrieval.py:178–228.                                      │

&#x20; ├────────────┼──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤

&#x20; │ Optional   │ Complete with scaling caveat │ Scope-filtered KNN over 384-dimensional injected embeddings.        │

&#x20; │ vector     │                              │ Current sqlite-vec workaround scans all embedded rows per query.    │

&#x20; │ retrieval  │                              │ retrieval.py:231–304.                                               │

&#x20; ├────────────┼──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤

&#x20; │ Lazy       │ Complete                     │ Idempotently fills missing vec rows and transactionally rejects     │

&#x20; │ embedding  │                              │ invalid dimensions. retrieval.py:130–175.                           │

&#x20; │ population │                              │                                                                     │

&#x20; ├────────────┼──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤

&#x20; │ RRF fusion │ Complete                     │ Deterministic reciprocal-rank fusion with tested tie-breaking.      │

&#x20; │            │                              │ retrieval.py:307–359.                                               │

&#x20; ├────────────┼──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤

&#x20; │ Token      │ Complete at core level       │ Drops whole memories rather than truncating and never exceeds the   │

&#x20; │ governor   │                              │ supplied cap. retrieval.py:362+. Token estimates are a              │

&#x20; │            │                              │ four-characters-per-token heuristic, not model-accurate.            │

&#x20; ├────────────┼──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤

&#x20; │ Python AST │ Partial                      │ Extracts modules/classes/functions and same-file calls/inheritance. │

&#x20; │ indexing   │                              │ indexer.py:65–167. No imports, cross-file calls, TypeScript,        │

&#x20; │            │                                     │ calls, TypeScript, tree-sitter, LSP, or external backend      │

&#x20; │            │                                     │ implementation.                                               │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ Pluggable  │ Partial                             │ BackendAdapter exists at indexer.py:53–61, but only the       │

&#x20; │ backend    │                                     │ built-in Python adapter is supplied and MCP does not expose   │

&#x20; │ protocol   │                                     │ backend choice.                                               │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ Incrementa │ Partial                             │ Reindexes one file and removes vanished node IDs.             │

&#x20; │ l file     │                                     │ indexer.py:321–368. Node IDs include line numbers, so moving  │

&#x20; │ reindex    │                                     │ a symbol can orphan its memories even if the symbol content   │

&#x20; │            │                                     │ is unchanged. Directory indexing is not incremental.          │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ Caller     │ Partial                             │ Correct over edges that exist, indexer.py:371+; graph         │

&#x20; │ tracing    │                                     │ coverage is limited to the Python adapter’s same-file         │

&#x20; │            │                                     │ extraction.                                                   │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ FastMCP    │ Partial                             │ Ten tools are registered in server.py:53–359. Tests call      │

&#x20; │ server and │                                     │ FastMCP directly, but there is no real subprocess JSON-RPC    │

&#x20; │ ten        │                                     │ handshake/conformance test.                                   │

&#x20; │ registrati │                                     │                                                               │

&#x20; │ ons        │                                     │                                                               │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ CLI serve  │ Complete for stdio                  │ Runs FastMCP over stdio. \_\_main\_\_.py:63–78. No HTTP           │

&#x20; │            │                                     │ transport.                                                    │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ CLI index  │ Complete for current Python adapter │ Handles a file or directory. \_\_main\_\_.py:81–101.              │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ CLI        │ Partial                             │ Produces a local token comparison. \_\_main\_\_.py:104+. It       │

&#x20; │ benchmark  │                                     │ simulates generic decisions, caps raw input at 20 files,      │

&#x20; │            │                                     │ measures only five JIT calls, and has no reproducibility or   │

&#x20; │            │                                     │ multi-repository gate.                                        │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ Session-st │ Partial                             │ hooks.session\_start clamps its upper bound to 800.            │

&#x20; │ art brief  │                                     │ hooks.py:30,45–92. It is not attached to a real client hook   │

&#x20; │ function   │                                     │ and does not use the embedder despite accepting one.          │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ PreToolUse │ Partial                             │ hooks.pre\_tool\_use clamps to 400. hooks.py:31,95–135. It      │

&#x20; │ function   │                                     │ searches by filename stem rather than selecting memories      │

&#x20; │            │                                     │ whose anchors belong to that file.                            │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ Stop       │ Partial                             │ Stores non-empty output as an unanchored memory.              │

&#x20; │ capture    │                                     │ hooks.py:138–162. It does not extract decisions, sanitize     │

&#x20; │ function   │                                     │ content, or run as an installed hook.                         │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ Full       │ Partial-to-complete                 │ agents\_md.why joins recall, freshness, and callers. It works  │

&#x20; │ library-le │                                     │ in tests, but the MCP tool does not call it.                  │

&#x20; │ vel why()  │                                     │                                                               │

&#x20; │ fusion     │                                     │                                                               │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ Library    │ Partial                             │ Produces deterministic grouped Markdown and stale/orphan      │

&#x20; │ AGENTS.md  │                                     │ markers. It does not write a file or regenerate only changed  │

&#x20; │ generation │                                     │ sections.                                                     │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ Git-diff   │ Partial                             │ Parses changed filenames and filters already-stale memories.  │

&#x20; │ staleness  │                                     │ agents\_md.py:238+. It does not reindex code, watch Git, or    │

&#x20; │ inspection │                                     │ operate automatically per commit.                             │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ Secret/PII │ Complete in isolation               │ Strong deterministic tests cover fake                         │

&#x20; │ pattern    │                                     │ AWS/GitHub/JWT/PEM/API/PII data. security.py:72–203,206+.     │

&#x20; │ sanitizer  │                                     │                                                               │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ Write-time │ Stub at product level               │ No call to sanitize() exists in anchors.remember,             │

&#x20; │ secret     │                                     │ server.remember\_decision, or hooks.stop\_capture. The README   │

&#x20; │ stripping  │                                     │ claim is currently false end-to-end.                          │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ Redaction  │ Partial                             │ In-memory RedactionLog exists and is tested. It is neither    │

&#x20; │ audit log  │                                     │ persisted nor returned by MCP.                                │

&#x20; ├────────────┼─────────────────────────────────────┼───────────────────────────────────────────────────────────────┤

&#x20; │ Cross-agen │ Partial                             │ Any MCP client can theoretically point at the same SQLite     │

&#x20; │ t shared   │                                     │ file, but there is no concurrency coordination, agent         │

&#x20; │ database   │                                     │ identity metadata, setup automation, or conflict detection.   │

&#x20; └────────────┴─────────────────────────────────────┴───────────────────────────────────────────────────────────────┘



&#x20; 1.4 Important implementation gaps hidden by passing tests



&#x20; The suite is healthy but mostly validates modules independently. It does not catch several integration failures:



&#x20; - Security tests call sanitize() directly; they never save a secret through remember() or MCP and inspect the

&#x20; database.

&#x20; - Hook tests directly invoke Python functions; they do not prove an agent runtime actually fires the hooks.

&#x20; - Server tests call server.mcp.call\_tool() in-process; they do not test stdio initialization and JSON-RPC transport.

&#x20; - agents\_md.py is well tested, but server tools duplicate older placeholder logic instead of calling it.

&#x20; - Server cap tests request exactly 400/800; they do not try max\_tokens=10\_000.

&#x20; - JIT tests use memories containing “auth”; they do not prove selection by anchor path.

&#x20; - Index tests cover only same-file Python edges.



&#x20; 1.5 Planned but unimplemented features



&#x20; Phase 0 / platform gate



&#x20; Planned in implementation-plan-opus48.md:47–52:



&#x20; - Verified install on macOS/Linux/Windows and Python 3.10–3.13.

&#x20; - Executed CI matrix evidence, not merely a workflow definition.

&#x20; - Fresh-environment package installation.



&#x20; Current status: local validation is on Windows; DECISIONS.md itself notes prior validation used Python 3.14, outside

&#x20; the declared support range.



&#x20; Phase 2 retrieval additions



&#x20; - A real embedding model/provider configuration.

&#x20; - Event-driven embedding on writes rather than lazy scans.

&#x20; - Real-model retrieval evaluation.

&#x20; - Model-specific tokenization.

&#x20; - Updating last\_accessed, reinforcement, and temporal decay.

&#x20; - Retrieval ranking using confidence/importance/freshness.

&#x20; - Surfacing provenance and anchor status with every result.



&#x20; The improved plan explicitly proposes provenance/confidence and decay/reinforcement around

&#x20; codebase-mind-improved-plan.md:75+. Fields exist in storage, but the behavior does not.



&#x20; Phase 3 structural backend



&#x20; Planned around implementation-plan-opus48.md:32,65–70:



&#x20; - codebase-memory-mcp, tree-sitter, or LSP adapter.

&#x20; - Python and TypeScript support.

&#x20; - Import/reference edges.

&#x20; - Backend choice through index(path, backend).

&#x20; - Incremental directory reindex touching only changed files.

&#x20; - Known multi-file caller chains.



&#x20; None is implemented. The repository instead contains a minimal Python AST parser.



&#x20; Phase 4 MCP and live JIT



&#x20; Planned around implementation-plan-opus48.md:33,71–77:



&#x20; - Stdio and HTTP transport.

&#x20; - Real MCP initialize/tools-list/tools-call subprocess conformance.

&#x20; - Live Claude Code SessionStart, PreToolUse, and Stop hook installation.

&#x20; - A recorded live transcript proving PreToolUse fires before an edit.

&#x20; - Truly file-scoped anchored context.

&#x20; - End-to-end hard caps.

&#x20; - Graceful cross-agent installation/configuration.



&#x20; Only stdio and in-process FastMCP calls currently exist.



&#x20; Phase 5 fusion and AGENTS.md



&#x20; Planned around implementation-plan-opus48.md:78–84:



&#x20; - MCP why() wired to decision+freshness+call-graph fusion.

&#x20; - Self-updating AGENTS.md on disk.

&#x20; - Regeneration of only affected sections with minimal diffs.

&#x20; - Automatic Git-diff/commit staleness watcher.

&#x20; - Scripted Git-history tests.



&#x20; The library contains approximations, but the MCP surface still labels Phase 5 functionality as preliminary

&#x20; (server.py:238–283,331–359).



&#x20; Phase 6 security



&#x20; Planned around implementation-plan-opus48.md:85–91:



&#x20; - Redaction before every persistence path.

&#x20; - Persisted or inspectable redaction audit.

&#x20; - Database-level assertion that seeded secrets never appear.

&#x20; - End-to-end private-scope adversarial retrieval.

&#x20; - Security-review release gate.



&#x20; The sanitizer exists, but none of these integration requirements is met.



&#x20; Phase 7 distribution and release



&#x20; Planned around implementation-plan-opus48.md:35,39,92–98 and the improved plan’s distribution section:



&#x20; - npx skills add package.

&#x20; - Claude/Codex/Cursor/Gemini/OpenClaw setup automation.

&#x20; - Prebuilt wheels and possibly a static binary.

&#x20; - One-command/zero-config initialization.

&#x20; - Cross-agent installation smoke tests.

&#x20; - Benchmarks on at least three real repositories.

&#x20; - Reproducibility tests and defensible published metrics.

&#x20; - Live JIT demonstration in Claude Code and one other agent.



&#x20; None is present.



&#x20; 1.6 GPT-5.6 / OpenAI integration points today



&#x20; Exact answer: none



&#x20; The requested files contain:



&#x20; - No openai dependency.

&#x20; - No OpenAI SDK import.

&#x20; - No Responses API or Chat Completions call.

&#x20; - No model identifier.

&#x20; - No OPENAI\_API\_KEY handling.

&#x20; - No GPT-5.6 prompt or structured-output schema.

&#x20; - No provider abstraction for generative models.



&#x20; pyproject.toml has only two runtime dependencies:



&#x20; fastmcp==3.4.4

&#x20; sqlite-vec==0.1.9



&#x20; Nearest existing extension point



&#x20; retrieval.py defines:



&#x20; Embedder = Callable\[\[str], Sequence\[float]]



&#x20; This is provider-neutral and requires exactly 384 dimensions. MnemexServer can receive an embedder constructor

&#x20; argument (server.py:42–51), but:



&#x20; - The CLI always calls create\_server(db\_path) without supplying one.

&#x20; - There is no OpenAI embedding adapter.

&#x20; - GPT-5.6 is a reasoning/generation model, not the current embedding implementation.

&#x20; - No API configuration reaches the server.



&#x20; Therefore even hybrid retrieval is inaccessible through the shipped CLI unless another Python program constructs the

&#x20; server itself.



&#x20; 1.7 Ten MCP tools: completeness map



&#x20; ┌────────────────────────┬─────────────────────┬───────────────────────────────────────────────────────────────────┐

&#x20; │ MCP tool               │ Maturity            │ Findings                                                          │

&#x20; ├────────────────────────┼─────────────────────────────┼───────────────────────────────────────────────────────────┤

&#x20; │ remember\_decision      │ Partial                     │ Correctly stores anchored/unanchored memories             │

&#x20; │                        │                             │ (server.py:58+). Does not sanitize. Supplying only        │

&#x20; │                        │                             │ anchor\_file or only anchor\_symbol silently creates an     │

&#x20; │                        │                             │ unanchored memory instead of failing.                     │

&#x20; ├────────────────────────┼─────────────────────────────┼───────────────────────────────────────────────────────────┤

&#x20; │ recall\_memories        │ Mostly complete             │ Correctly wraps core BM25/RRF/governor (server.py:88+).   │

&#x20; │                        │                             │ CLI never configures an embedder, so shipped usage is     │

&#x20; │                        │                             │ BM25-only. No freshness/provenance in results.            │

&#x20; ├────────────────────────┼─────────────────────────────┼───────────────────────────────────────────────────────────┤

&#x20; │ forget\_memory          │ Complete                    │ Thin, correct deletion wrapper (server.py:125+).          │

&#x20; ├────────────────────────┼─────────────────────────────┼───────────────────────────────────────────────────────────┤

&#x20; │ check\_memory\_freshness │ Complete                    │ Correctly exposes all four states and hashes.             │

&#x20; ├────────────────────────┼─────────────────────────────┼───────────────────────────────────────────────────────────┤

&#x20; │ context\_for            │ Partial                     │ Searches by Path(path).stem; it does not filter on anchor │

&#x20; │                        │                             │ node file. Its stated 400-token limit can be bypassed by  │

&#x20; │                        │                             │ requesting a larger value.                                │

&#x20; ├────────────────────────┼─────────────────────────────┼───────────────────────────────────────────────────────────┤

&#x20; │ get\_context\_brief      │ Partial                     │ Produces importance-ranked bullets, not a semantic        │

&#x20; │                        │                             │ summary. Its stated 800-token limit can be bypassed. It   │

&#x20; │                        │                             │ uses creation time ascending as its tie-break, favoring   │

&#x20; │                        │                             │ older records.                                            │

&#x20; ├────────────────────────┼─────────────────────────────┼───────────────────────────────────────────────────────────┤

&#x20; │ why                    │ Stub on MCP surface         │ Comment explicitly says the full Phase 5 fusion is        │

&#x20; │                        │                             │ pending; returns "callers": \[]. A better implementation   │

&#x20; │                        │                             │ already exists in agents\_md.py but is not used.           │

&#x20; ├────────────────────────┼─────────────────────────────┼───────────────────────────────────────────────────────────┤

&#x20; │ trace\_callers\_tool     │ Complete over partial graph │ Correctly exposes indexer.trace\_callers; usefulness is    │

&#x20; │                        │                             │ limited by Python/same-file edge extraction.              │

&#x20; ├────────────────────────┼─────────────────────────────┼───────────────────────────────────────────────────────────┤

&#x20; │ index\_path             │ Partial                     │ Indexes files/directories with the Python adapter. No     │

&#x20; │                        │                             │ backend parameter, no reindex route, no TypeScript, and   │

&#x20; │                        │                             │ repeat calls may accumulate duplicate edges.              │

&#x20; ├────────────────────────┼─────────────────────────────┼───────────────────────────────────────────────────────────┤

&#x20; │ generate\_agents\_md     │ Stub on MCP surface         │ Returns a basic memory list and does not use              │

&#x20; │                        │                             │ agents\_md.generate\_agents\_md, write a file, mark          │

&#x20; │                        │                             │ staleness, or preserve sections.                          │

&#x20; └────────────────────────┴─────────────────────────────┴───────────────────────────────────────────────────────────┘



&#x20; ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────



&#x20; 2. Innovation Opportunities



&#x20; Recommended judge-impact order



&#x20; 1. GPT-5.6 pre-edit contradiction guard

&#x20; 2. End-to-end secure write path

&#x20; 3. True anchor-path JIT retrieval with live Codex integration

&#x20; 4. Temporal supersession/provenance graph

&#x20; 5. Cross-agent decision ledger and conflict attribution

&#x20; 6. One-command setup and animated demo diagnostics

&#x20; 7. Consolidation/decay mechanisms



&#x20; A. GPT-5.6 Integration



&#x20; A1. Pre-edit Decision Constraint Judge — highest impact, 1–2 days



&#x20; Mechanism



&#x20; 1. Codex proposes an edit or supplies a patch summary.

&#x20; 2. mnemex deterministically selects:

&#x20;   - Memories anchored to the file/symbol.

&#x20;   - Caller-related decisions.

&#x20;   - Similar project decisions from BM25.

&#x20;   - Freshness and provenance metadata.



&#x20; 3. GPT-5.6 receives only this bounded evidence and emits structured output:



&#x20; {

&#x20;   "verdict": "compatible | contradiction | supersedes | uncertain",

&#x20;   "decision\_ids": \["..."],

&#x20;   "explanation": "...",

&#x20;   "recommended\_action": "...",

&#x20;   "confidence": 0.94

&#x20; }



&#x20; 4. A contradiction blocks or warns before Codex edits.



&#x20; Why GPT-5.6 genuinely matters



&#x20; BM25 and sqlite-vec can retrieve candidates, but cannot reliably determine that:



&#x20; - “Move session state into Redis” contradicts “authentication remains stateless.”

&#x20; - “Replace retry loop with broker redelivery” supersedes an older retry convention.

&#x20; - A changed implementation preserves a decision despite using different vocabulary.



&#x20; GPT-5.6 performs semantic adjudication; deterministic code handles evidence selection and validity.



&#x20; Why a judge cares



&#x20; This changes the product from “memory search” into active architectural governance for coding agents.



&#x20; A2. Stale-memory reconciliation — 1 day



&#x20; When an anchor hash changes, ask GPT-5.6 to compare:



&#x20; - Original decision and rationale.

&#x20; - Previous/current symbol source.

&#x20; - Git diff.

&#x20; - Callers.



&#x20; Return:



&#x20; - Still valid; refresh anchor hash.

&#x20; - Superseded by code.

&#x20; - Code appears accidental and contradicts the decision.

&#x20; - Human review needed.



&#x20; This solves mnemex’s current weakness: detecting staleness without explaining what it means.



&#x20; A3. Decision extraction and anchor suggestion — 1–2 days



&#x20; At Stop/session end, give GPT-5.6 the compact action summary and changed symbols. Ask it to extract only durable

&#x20; items:



&#x20; - Decision.

&#x20; - Rationale.

&#x20; - Candidate symbol.

&#x20; - Evidence line/diff.

&#x20; - Confidence.

&#x20; - Expiry/review condition.



&#x20; Require confirmation before persistence.



&#x20; This is better than saving the complete agent output as one unanchored blob.



&#x20; Minimal GPT-5.6 path



&#x20; - Optional openai extra.

&#x20; - One Responses API adapter.

&#x20; - One structured contradiction schema.

&#x20; - One new MCP tool such as check\_proposed\_change.

&#x20; - BM25/anchor candidate generation remains local.

&#x20; - No automatic writes from GPT output.

&#x20; - API use is opt-in; no-ML mode remains intact.



&#x20; Maximal GPT-5.6 path



&#x20; - Pre-edit contradiction/supersession judge.

&#x20; - Post-edit stale reconciliation.

&#x20; - Stop-hook decision extraction.

&#x20; - Episodic-to-semantic consolidation.

&#x20; - Provenance-linked GPT explanations.

&#x20; - Feedback storage for accepted/rejected warnings.

&#x20; - Evaluation fixture measuring contradiction precision/recall.



&#x20; Do not use GPT-5.6 to replace SQLite retrieval. That would weaken determinism, privacy, cost, and the core moat.



&#x20; B. Novel Technical Mechanisms



&#x20; Existing defensible moat



&#x20; Among the six requested competitors, mnemex’s strongest unique mechanism is:



&#x20; │ A durable decision is joined to a specific structural code node and its content hash, allowing source-derived

&#x20; fresh/stale/orphaned validity.



&#x20; The moat is not SQLite, MCP, BM25, RRF, or cross-agent memory. Competitors already have those or stronger variants.



&#x20; B1. Commit-aware supersession graph — 1–2 days



&#x20; Add:



&#x20; memory A --superseded\_by--> memory B

&#x20; memory C --contradicts--> memory D

&#x20; memory E --derived\_from--> episode/commit



&#x20; Store valid\_from, valid\_to, observed\_at, commit SHA, and authoring agent.



&#x20; This borrows a small, practical slice from temporal knowledge graphs without adopting Graphiti’s infrastructure.



&#x20; Judge value: Shows decisions evolving through code history rather than becoming stale debris.



&#x20; Relevant work:



&#x20; - Graphiti/Zep temporal validity: https://arxiv.org/abs/2501.13956 (https://arxiv.org/abs/2501.13956)

&#x20; - Allen’s temporal interval algebra: https://doi.org/10.1145/182.358434 (https://doi.org/10.1145/182.358434)

&#x20; - W3C provenance model: https://www.w3.org/TR/prov-o/ (https://www.w3.org/TR/prov-o/)



&#x20; B2. Anchored semantic constraint graph — 2–3 days



&#x20; Turn memories from passive text into typed constraints:



&#x20; scope: authentication

&#x20; constraint: must\_remain\_stateless

&#x20; applies\_to: authenticate

&#x20; evidence: decision-123



&#x20; A proposed patch is checked against constraints and callers. GPT-5.6 translates natural-language decisions into

&#x20; candidate constraints; deterministic checks enforce high-confidence cases.



&#x20; Why this becomes harder to copy: Over time mnemex accumulates a project-specific dataset of constraints, code changes,

&#x20; warnings, and accepted overrides.



&#x20; B3. Conservative reinforcement and review scheduling — 1 day



&#x20; Add access\_count, last\_recalled\_at, last\_confirmed\_at, and review\_after.



&#x20; Use decay only to prioritize reviews, never to delete or hide a fresh architectural decision. Repeatedly useful

&#x20; memories gain rank; stale and never-used memories enter a review queue.



&#x20; Relevant techniques:



&#x20; - Ebbinghaus-inspired decay: https://psychclassics.yorku.ca/Ebbinghaus/ (https://psychclassics.yorku.ca/Ebbinghaus/)

&#x20; - ACT-R base-level activation: https://act-r.psy.cmu.edu/ (https://act-r.psy.cmu.edu/)

&#x20; - MemoryBank application to LLM memory: https://arxiv.org/abs/2305.10250 (https://arxiv.org/abs/2305.10250)



&#x20; C. Developer Experience Innovation



&#x20; C1. mnemex init auto-setup — 1–2 days



&#x20; One command should:



&#x20; 1. Find the repository root.

&#x20; 2. Create .mnemex/memory.sqlite3.

&#x20; 3. Index supported files.

&#x20; 4. Detect Codex/Claude/Cursor/Kiro.

&#x20; 5. Write the appropriate MCP configuration.

&#x20; 6. Install supported hooks.

&#x20; 7. Run doctor.

&#x20; 8. Save one onboarding decision.



&#x20; Judge value: Removes the current manual JSON editing and makes the product demoable in under a minute.



&#x20; C2. “Time-travel why” command — 1–2 days



&#x20; Example:



&#x20; mnemex why authenticate



&#x20; Output:



&#x20; Decision: Keep sessions stateless

&#x20; Anchored: src/auth.py::authenticate

&#x20; Status: STALE — changed in commit abc123, 18 minutes ago

&#x20; Changed by: Codex

&#x20; Potential conflict: Redis-backed session state

&#x20; Callers affected: login, refresh\_token, logout



&#x20; This is the likely “holy shit” moment: a code-aware explanation with history, validity, and blast radius.



&#x20; C3. Doctor + self-contained demo mode — 1 day



&#x20; mnemex demo



&#x20; Creates a temporary fixture, launches MCP, stores a decision, makes a contradictory edit, and shows the warning.

&#x20; mnemex doctor checks:



&#x20; - SQLite/FTS/vec availability.

&#x20; - Writable DB.

&#x20; - MCP config.

&#x20; - Hook installation.

&#x20; - index freshness.

&#x20; - GPT-5.6 credentials when enabled.

&#x20; - Secret-redaction write probe.



&#x20; This prevents judging from being derailed by environment setup.



&#x20; D. Agentic Workflow Innovation



&#x20; D1. Past-mistake guard — 1–2 days



&#x20; Add a mistake memory type with:



&#x20; - Failed approach.

&#x20; - Error signature.

&#x20; - Root cause.

&#x20; - Affected symbol.

&#x20; - Corrective action.



&#x20; Before a patch or command, retrieve matching mistakes by anchor, error text, and caller graph. GPT-5.6 judges whether

&#x20; the agent is repeating one.



&#x20; Judge value: Demonstrates improved coding quality, not merely continuity.



&#x20; D2. Proactive contradiction warning — 1–2 days



&#x20; On a proposed patch:



&#x20; 1. Get modified symbols.

&#x20; 2. Resolve fresh anchored decisions.

&#x20; 3. Generate a concise semantic summary of the patch.

&#x20; 4. GPT-5.6 classifies compatibility.

&#x20; 5. Return warning with exact evidence and override option.



&#x20; Example:



&#x20; BLOCKED: This patch introduces server-side sessions.

&#x20; It conflicts with decision D-42: “Authentication must remain stateless.”

&#x20; Decision is fresh and anchored to authenticate().



&#x20; D3. Blast-radius-aware context — 1 day



&#x20; JIT context should include not only memories directly anchored to the file, but also a bounded set from:



&#x20; - Callees.

&#x20; - Callers.

&#x20; - Base classes.

&#x20; - Imported interfaces.



&#x20; This uses structural relevance rather than filename keyword coincidence.



&#x20; E. Multi-Agent / Cross-Tool Innovation



&#x20; E1. Agent-attributed shared decision ledger — 1 day



&#x20; Persist:



&#x20; - asserted\_by\_agent

&#x20; - client/tool name

&#x20; - session ID

&#x20; - commit/branch

&#x20; - source request

&#x20; - timestamp



&#x20; Then show:



&#x20; Claude Code decided X on Monday.

&#x20; Codex is proposing Y today.



&#x20; Judge value: Cross-agent support becomes a capability rather than a compatibility checkbox.



&#x20; E2. Cross-agent conflict inbox — 2 days



&#x20; When Agent B stores or proposes a decision, compare it with Agent A’s current decisions. Save possible conflicts for

&#x20; review rather than silently overwriting history.



&#x20; Engram now has general conflict surfacing, so mnemex must differentiate through code-anchor validity and symbol-level

&#x20; blast radius, not conflict detection alone.



&#x20; E3. Portable project brain bundle — 1–2 days



&#x20; Export/import:



&#x20; AGENTS.md

&#x20; memory.sqlite3 subset

&#x20; decision provenance

&#x20; anchor hashes

&#x20; repository commit



&#x20; This enables team handoff while preserving whether imported decisions match the recipient’s checkout.



&#x20; ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────



&#x20; 3. Competitive Gap Analysis



&#x20; This is a 2026-07-15 snapshot based primarily on official repositories and documentation. Hosted features and

&#x20; author-reported benchmarks should be attributed rather than treated as independently proven.



&#x20; 3.1 Comparison



&#x20; ┌────────────────┬────────────────────────────────────────────────────────┬───────────────────────────────────────┐

&#x20; │ Competitor     │ What it does that mnemex does not                      │ What mnemex does that it does not     │

&#x20; │                │                                                        │ clearly match                         │

&#x20; ├────────────────┼────────────────────────────────────────────────────────┼───────────────────────────────────────┤

&#x20; │ Mem0           │ LLM-driven memory extraction; user/session/agent       │ Exact code-symbol hash anchors;       │

&#x20; │                │ state; entity linking; temporal retrieval;             │ source-derived stale/orphaned state;  │

&#x20; │                │ semantic+BM25+entity fusion; hosted platform; CLI;     │ caller-aware explanations; strict     │

&#x20; │                │ agent skills; broad personalization. Current OSS       │ JIT/session budgets; useful no-model  │

&#x20; │                │ defaults use OpenAI models.                            │ baseline.                             │

&#x20; ├────────────────┼────────────────────────────────────────────────────────┼───────────────────────────────────────┤

&#x20; │ Zep / Graphiti │ Temporal context graph; validity windows; episodes and │ Much smaller local SQLite footprint;  │

&#x20; │                │ provenance; automatic fact invalidation;               │ no-model operation; exact             │

&#x20; │                │ prescribed/learned ontology; semantic+keyword+graph    │ source-symbol validity; direct code   │

&#x20; │                │ retrieval; historical queries.                         │ index/caller graph;                   │

&#x20; │                │                                                        │ coding-agent-specific JIT delivery.   │

&#x20; ├────────────────┼───────────────────────────────────────────────────────┼────────────────────────────────────────┤

&#x20; │ LangMem        │ Hot-path memory tools; background                     │ Code-content-hash validity, orphan     │

&#x20; │                │ extraction/consolidation/update; procedural memory    │ detection, structural callers, hard    │

&#x20; │                │ and prompt optimization; flexible LangGraph           │ injection limits, local standalone MCP │

&#x20; │                │ namespaces/stores.                                    │ focus.                                 │

&#x20; ├────────────────┼───────────────────────────────────────────────────────┼────────────────────────────────────────┤

&#x20; │ MemGPT / Letta │ Full stateful agent runtime; persistent personas and  │ Augments existing coding agents rather │

&#x20; │                │ agent state; context paging;                          │ than replacing them; code-symbol/hash  │

&#x20; │                │ autonomous/self-improving agents; skills and          │ anchors; deterministic code validity;  │

&#x20; │                │ subagents; local/cloud runtime.                       │ smaller and model-optional core.       │

&#x20; ├────────────────┼───────────────────────────────────────────────────────┼────────────────────────────────────────┤

&#x20; │ basic-memory   │ Human-readable Markdown source of truth;              │ Automatic code-derived                 │

&#x20; │                │ Obsidian-compatible graph; semantic search; schema    │ freshness/orphaning; exact             │

&#x20; │                │ inference/validation; progressive MCP tool            │ decision-to-symbol binding; code       │

&#x20; │                │ annotations; cloud/team collaboration; mature         │ caller fusion; strict bounded JIT      │

&#x20; │                │ plugins/hooks/skills.                                 │ context.                               │

&#x20; ├────────────────┼───────────────────────────────────────────────────────┼────────────────────────────────────────┤

&#x20; │ engram         │ Single Go binary; zero-dependency installation;       │ Exact symbol/content-hash validity;    │

&#x20; │                │ automated setup for many agents; CLI/TUI/HTTP/MCP;    │ stale/orphaned code-anchor semantics;  │

&#x20; │                │ session lifecycle; Git/cloud sync; soft deletion;     │ source graph and caller blast radius;  │

&#x20; │                │ mature diagnostics; conflict surfacing and optional   │ decision validity coupled to actual    │

&#x20; │                │ LLM judging.                                          │ code changes.                          │

&#x20; └────────────────┴───────────────────────────────────────────────────────┴────────────────────────────────────────┘



&#x20; Official sources:



&#x20; - Mem0: https://github.com/mem0ai/mem0 (https://github.com/mem0ai/mem0)

&#x20; - Zep/Graphiti: https://github.com/getzep/graphiti (https://github.com/getzep/graphiti)

&#x20; - LangMem: https://github.com/langchain-ai/langmem (https://github.com/langchain-ai/langmem)

&#x20; - Letta: https://github.com/letta-ai/letta (https://github.com/letta-ai/letta)

&#x20; - Basic Memory: https://github.com/basicmachines-co/basic-memory (https://github.com/basicmachines-co/basic-memory)

&#x20; - Engram: https://github.com/Gentleman-Programming/engram (https://github.com/Gentleman-Programming/engram)



&#x20; 3.2 Competitive warnings



&#x20; The README comparison table is no longer sufficiently accurate:



&#x20; - Mem0 now has multi-signal retrieval, skills, CLI, and broad agent integrations.

&#x20; - Basic Memory now has session hooks, shared/team memory, hybrid search, plugins, and skills.

&#x20; - Engram now has 20 MCP tools, automated agent setup, Git/cloud sync, review workflows, and conflict surfacing.

&#x20; - Graphiti has stronger temporal/provenance mechanics than mnemex.

&#x20; - LangMem and Letta make agents actively maintain or improve memory.



&#x20; Do not lead with “one SQLite file,” “MCP,” “cross-agent,” “hybrid retrieval,” or “conflict detection.” Those are no

&#x20; longer unique.



&#x20; 3.3 The one defensible claim



&#x20; Use a carefully qualified claim:



&#x20; │ Among the major memory systems reviewed, mnemex is the only one that makes a coding decision’s validity depend

&#x20; directly on the current content hash of the exact code symbol it governs—so it can deterministically report that

&#x20; decision as fresh, stale, or orphaned.



&#x20; Avoid “exact line” in marketing. The implementation anchors to a structural node identified partly by file/name/line

&#x20; and stores the symbol content hash; it does not maintain a stable line-level anchor across arbitrary refactors.



&#x20; ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────



&#x20; 4. Three-Minute Demo Script Storyboard



&#x20; This storyboard assumes the top recommended additions are implemented: secure writes, true file-scoped JIT, GPT-5.6

&#x20; contradiction judging, and Codex setup.



&#x20; 0:00–0:20 — Zero-config setup



&#x20; User sees



&#x20; cd demo-repo

&#x20; mnemex init --client codex --gpt-model gpt-5.6



&#x20; Output:



&#x20; ✓ Indexed 84 symbols

&#x20; ✓ Installed Codex MCP configuration

&#x20; ✓ Connected GPT-5.6 decision judge

&#x20; ✓ Local brain: .mnemex/memory.sqlite3

&#x20; ✓ No source code leaves the machine except bounded conflict evidence



&#x20; Technically



&#x20; - Repository root detected.

&#x20; - SQLite/FTS/index initialized.

&#x20; - Codex MCP config generated.

&#x20; - GPT-5.6 provider tested.

&#x20; - Redaction self-test run.



&#x20; Why a judge cares



&#x20; The product is usable in seconds, not a hand-configured research prototype.



&#x20; 0:20–0:45 — Store an anchored decision from Codex



&#x20; User asks Codex



&#x20; │ “Remember that authentication must stay stateless because requests are distributed across edge workers.”



&#x20; Codex calls:



&#x20; remember\_decision(

&#x20;   content="Authentication must remain stateless",

&#x20;   rationale="Requests run across distributed edge workers",

&#x20;   anchor\_file="src/auth.py",

&#x20;   anchor\_symbol="authenticate"

&#x20; )



&#x20; User sees



&#x20; Stored D-42

&#x20; Anchor: src/auth.py::authenticate

&#x20; Hash: 71bc…

&#x20; Status: fresh



&#x20; Technically



&#x20; - Symbol resolved through the structural index.

&#x20; - Current source hash stamped.

&#x20; - Input sanitized before persistence.

&#x20; - Agent/session/commit provenance recorded.



&#x20; Why a judge cares



&#x20; This demonstrates the core innovation: memory is attached to code validity, not just stored as text.



&#x20; 0:45–1:20 — Codex tries to contradict it



&#x20; User asks Codex



&#x20; │ “Add Redis-backed server sessions to authentication.”



&#x20; Before editing, Codex calls check\_proposed\_change.



&#x20; User sees



&#x20; ⚠ Proposed change conflicts with a fresh anchored decision



&#x20; Decision D-42:

&#x20; “Authentication must remain stateless.”



&#x20; Anchor: src/auth.py::authenticate

&#x20; Affected callers: login, refresh\_token, logout

&#x20; GPT-5.6 verdict: contradiction (0.96)



&#x20; Reason:

&#x20; Redis-backed sessions introduce server-side authentication state.



&#x20; Codex responds:



&#x20; │ “This conflicts with the current authentication constraint. Should I use signed short-lived tokens instead, or

&#x20; explicitly supersede D-42?”



&#x20; Technically



&#x20; - mnemex uses anchor and caller graph to generate a small candidate set.

&#x20; - GPT-5.6 judges semantic compatibility with structured output.

&#x20; - Codex receives the warning before editing.

&#x20; - GPT is not doing retrieval or storing unverified facts.



&#x20; Why a judge cares



&#x20; This is the central “holy shit” moment: the memory system prevents an architectural regression across sessions.



&#x20; 1:20–1:50 — Legitimate evolution and supersession



&#x20; User says



&#x20; │ “The deployment moved off edge workers. Supersede the decision and implement Redis sessions.”



&#x20; User sees



&#x20; D-43 supersedes D-42

&#x20; D-42 valid until commit abc123

&#x20; D-43 anchored to src/auth.py::authenticate



&#x20; Codex makes the edit.



&#x20; Technically



&#x20; - Temporal supersedes edge is written.

&#x20; - Old decision remains queryable historically.

&#x20; - New symbol hash is stamped after the edit.

&#x20; - Provenance records that Codex made the change with user approval.



&#x20; Why a judge cares



&#x20; mnemex does not merely detect contradiction; it models justified architectural evolution.



&#x20; 1:50–2:20 — Cross-agent continuity



&#x20; Open a second client, for example Claude Code or Kiro, against the same project brain.



&#x20; Ask:



&#x20; │ “Why does authenticate use Redis now?”



&#x20; User sees



&#x20; Current decision:

&#x20; Use Redis-backed sessions.



&#x20; Superseded:

&#x20; Authentication must remain stateless.



&#x20; Reason for change:

&#x20; Deployment moved from distributed edge workers.



&#x20; Changed by:

&#x20; Codex, 38 seconds ago.



&#x20; Callers:

&#x20; login, refresh\_token, logout



&#x20; Technically



&#x20; - The second agent reads the same project ledger.

&#x20; - It receives current and historical decisions with code-derived freshness.

&#x20; - No session transcript replay is required.



&#x20; Why a judge cares



&#x20; “Cross-agent” becomes a shared, attributable engineering brain.



&#x20; 2:20–2:45 — Stale-code detection



&#x20; Manually modify authenticate without updating the decision, then run:



&#x20; mnemex why authenticate



&#x20; User sees



&#x20; ⚠ D-43 is stale

&#x20; Anchor hash changed in the working tree.



&#x20; GPT-5.6 reconciliation:

&#x20; The implementation now stores sessions in-process rather than Redis.

&#x20; This likely contradicts D-43 and breaks multi-instance deployment.



&#x20; Technically



&#x20; - Hash mismatch is deterministic.

&#x20; - GPT-5.6 compares the diff to the stale decision.

&#x20; - The tool distinguishes “code changed” from “decision violated.”



&#x20; Why a judge cares



&#x20; It demonstrates both the original moat and GPT-5.6’s genuine contribution.



&#x20; 2:45–3:00 — Close with measurable value



&#x20; User sees



&#x20; Context delivered: 286 tokens

&#x20; Raw files avoided: 18,400 tokens

&#x20; Conflicts prevented: 1

&#x20; Decisions preserved: 2

&#x20; Secrets persisted: 0



&#x20; Closing line



&#x20; │ “mnemex doesn’t just remember what agents said. It knows which code each decision governs, whether that decision is

&#x20; still true, and warns every agent before they violate it.”



&#x20; ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────



&#x20; 5. Risk Assessment



&#x20; 5.1 Judging risks



&#x20; ┌─────────────────────────┬─────────────┬──────────┬──────────────────────────────────────────────────────────────┐

&#x20; │ Risk                    │ Probability │   Impact │ Mitigation                                                   │

&#x20; ├─────────────────────────┼─────────────┼──────────┼──────────────────────────────────────────────────────────────┤

&#x20; │ README claims redaction │        High │ Critical │ Wire sanitize() into the single remember() persistence       │

&#x20; │ while MCP stores raw    │             │          │ boundary and add DB-level tests before submission.           │

&#x20; │ secrets                 │             │          │                                                              │

&#x20; ├─────────────────────────┼─────────────┼──────────┼──────────────────────────────────────────────────────────────┤

&#x20; │ GPT-5.6 API/model       │      Medium │ Critical │ Preflight credentials; keep a recorded fixture response;     │

&#x20; │ access fails            │             │          │ provide deterministic offline demo mode clearly labeled as   │

&#x20; │                         │             │          │ replay.                                                      │

&#x20; ├─────────────────────────┼─────────────┼──────────┼──────────────────────────────────────────────────────────────┤

&#x20; │ Live Codex MCP          │      Medium │     High │ Add mnemex init --client codex and mnemex doctor; test from  │

&#x20; │ configuration fails     │             │          │ a clean profile.                                             │

&#x20; ├─────────────────────────┼─────────────┼──────────┼──────────────────────────────────────────────────────────────┤

&#x20; │ “PreToolUse hook” does  │  High today │     High │ Do not claim live hooks until an actual client adapter and   │

&#x20; │ not actually fire       │             │          │ transcript test exist. Codex can explicitly call the MCP     │

&#x20; │                         │             │          │ guard for the demo.                                          │

&#x20; ├─────────────────────────┼─────────────┼──────────┼──────────────────────────────────────────────────────────────┤

&#x20; │ context\_for returns     │  High today │     High │ Filter by nodes.file and anchor IDs before semantic          │

&#x20; │ irrelevant memories     │             │          │ expansion.                                                   │

&#x20; ├─────────────────────────┼─────────────┼──────────┼──────────────────────────────────────────────────────────────┤

&#x20; │ Caller graph misses     │        High │   Medium │ Keep demo fixture’s call graph within supported behavior or  │

&#x20; │ cross-file calls        │             │          │ implement a small import-aware resolver. Disclose            │

&#x20; │                         │             │          │ Python-only support.                                         │

&#x20; ├─────────────────────────┼─────────────┼──────────┼──────────────────────────────────────────────────────────────┤

&#x20; │ MCP cap can be bypassed │  High today │   Medium │ Clamp server values to 400/800 and reject negative values.   │

&#x20; ├─────────────────────────┼─────────────┼──────────┼──────────────────────────────────────────────────────────────┤

&#x20; │ Server shows            │  High today │     High │ Replace duplicate server logic with agents\_md.why.           │

&#x20; │ placeholder why output  │             │          │                                                              │

&#x20; ├─────────────────────────┼─────────────┼──────────┼──────────────────────────────────────────────────────────────┤

&#x20; │ Repeat indexing creates │      Medium │   Medium │ Add edge uniqueness or make indexing replace file edges      │

&#x20; │ duplicate edges         │             │          │ transactionally.                                             │

&#x20; ├─────────────────────────┼─────────────┼──────────┼──────────────────────────────────────────────────────────────┤

&#x20; │ Symbol line movement    │      Medium │     High │ Use stable IDs based on file + qualified symbol, not line    │

&#x20; │ changes node ID         │             │          │ number; use line only as metadata.                           │

&#x20; ├─────────────────────────┼─────────────┼──────────┼──────────────────────────────────────────────────────────────┤

&#x20; │ sqlite-vec unavailable  │      Medium │      Low │ BM25 fallback already works; demo should not depend on       │

&#x20; │ on judge machine        │             │          │ sqlite-vec.                                                  │

&#x20; ├─────────────────────────┼─────────────┼──────────┼──────────────────────────────────────────────────────────────┤

&#x20; │ Python/package setup    │      Medium │     High │ Ship uvx path or wheel and a doctor command.                 │

&#x20; │ friction                │             │          │                                                              │

&#x20; ├─────────────────────────┼─────────────┼──────────┼──────────────────────────────────────────────────────────────┤

&#x20; │ Competitor uniqueness   │        High │     High │ Make the claim specifically about symbol-hash validity, not  │

&#x20; │ challenge               │             │          │ generic memory/conflicts/MCP.                                │

&#x20; ├─────────────────────────┼─────────────┼──────────┼──────────────────────────────────────────────────────────────┤

&#x20; │ GPT judge false         │      Medium │   Medium │ Treat output as warning unless high-confidence and fresh;    │

&#x20; │ positive                │             │          │ show evidence and explicit override.                         │

&#x20; ├─────────────────────────┼─────────────┼──────────┼──────────────────────────────────────────────────────────────┤

&#x20; │ Source/privacy concern  │      Medium │     High │ Send bounded decision/diff excerpts only, redact first, make │

&#x20; │ with OpenAI             │             │          │ GPT opt-in, and display what is transmitted.                 │

&#x20; └─────────────────────────┴─────────────┴──────────┴──────────────────────────────────────────────────────────────┘



&#x20; 5.2 Minimum viable submission



&#x20; If time runs out, submit these four things:



&#x20; 1. Fix security integration

&#x20;   - Sanitize content, rationale, and tags before storage.

&#x20;   - Add an end-to-end database leak test.



&#x20; 2. Add one GPT-5.6 feature

&#x20;   - check\_proposed\_change.

&#x20;   - Deterministic candidate retrieval.

&#x20;   - Structured contradiction verdict.

&#x20;   - One Codex MCP workflow.



&#x20; 3. Fix true JIT selection

&#x20;   - Select memories by anchor file/symbol.

&#x20;   - Clamp 400/800-token limits at MCP boundaries.



&#x20; 4. Wire existing Phase 5 code

&#x20;   - MCP why calls agents\_md.why.

&#x20;   - MCP generate\_agents\_md calls the fuller generator.



&#x20; That is enough for a coherent submission: anchored memory, code validity, GPT semantic reasoning, and Codex usage.



&#x20; 5.3 Cut order



&#x20; Cut first



&#x20; 1. HTTP MCP transport.

&#x20; 2. TUI or dashboard.

&#x20; 3. Static binary.

&#x20; 4. TypeScript indexing.

&#x20; 5. Vector embeddings/OpenAI embedding integration.

&#x20; 6. Decay and reinforcement.

&#x20; 7. Automatic AGENTS.md partial-section rewriting.

&#x20; 8. Broad cross-agent installers beyond Codex plus one second client.

&#x20; 9. Multi-repository benchmark polish.



&#x20; Preserve until last



&#x20; 1. End-to-end write-time security.

&#x20; 2. Symbol anchor plus hash freshness.

&#x20; 3. True anchor-path context retrieval.

&#x20; 4. GPT-5.6 contradiction/reconciliation.

&#x20; 5. Codex MCP demonstration.

&#x20; 6. One-command setup/doctor.

&#x20; 7. Evidence-backed why output.

&#x20; 8. Deterministic no-ML fallback.



&#x20; 5.4 Recommended 72-hour sequence



&#x20; Day 1: Make current claims true



&#x20; - Wire sanitizer into remember().

&#x20; - Clamp MCP token caps.

&#x20; - Make context\_for file-anchor aware.

&#x20; - Wire agents\_md.why and generate\_agents\_md.

&#x20; - Add end-to-end integration tests.



&#x20; Day 2: GPT-5.6 submission feature



&#x20; - Add optional OpenAI provider.

&#x20; - Implement structured conflict verdict.

&#x20; - Add check\_proposed\_change.

&#x20; - Store provenance and supersession.

&#x20; - Build a deterministic evaluation fixture.



&#x20; Day 3: Demo reliability



&#x20; - Add Codex setup command.

&#x20; - Add doctor and demo.

&#x20; - Rehearse from a clean directory.

&#x20; - Capture a backup recording.

&#x20; - Update README claims to match verified behavior.

&#x20; - Run tests, Ruff, package install, and a real stdio MCP smoke test.



&#x20; ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────



&#x20; Final recommendation



&#x20; The repository should not compete as another generalized memory store. Mem0, Graphiti, Basic Memory, Letta, LangMem,

&#x20; and Engram are already broader and more operationally mature.



&#x20; mnemex should own a narrower category:



&#x20; │ Decision integrity for coding agents: code-anchored memory that knows when a decision is still valid and uses

&#x20; GPT-5.6 to stop agents from violating it.



&#x20; That framing aligns the strongest existing mechanism, the required GPT-5.6 integration, and the most compelling Codex

&#x20; demo into one defensible story.

