# Changelog

All notable changes to Mnemex are documented here. This project follows
semantic versioning once release publishing begins.

## 0.1.0 - 2026-07-15

### Added

- Local SQLite persistence for anchored decisions, graph nodes, retrieval,
  provenance, review state, conflicts, guard runs, overrides, and redaction
  audits.
- Python and TypeScript/TSX structural indexing with anchors, caller tracing,
  freshness checks, and append-only decision lifecycle operations.
- MCP and CLI workflows for recall, `why`, change checks, reconciliation,
  review, project-brain import/export, dashboard health, initialization, and
  diagnostics.
- Optional OpenAI semantic judgment with bounded, redacted evidence and a
  no-network local default.
- Deterministic tagged constraints, mistake-memory checks, and confirmation-only
  stop-hook suggestions.
- Cross-platform CI, wheel smoke tests, a source release bundle, a private npm
  skill installer, benchmark evidence, and a cross-agent example.

### Security

- Sanitization occurs before persistence and records redaction audit entries.
- Guard failures and unavailable semantic judgment remain advisory.
- Remote evidence payloads are capped and inspectable.

### Notes

- Codex is the first verified live MCP client integration.
- Local HTTP transport is intentionally unauthenticated and must remain local
  or sit behind an authenticated gateway.
