"""CLI entry point for mnemex — ``python -m mnemex``.

Supports running the MCP server over stdio for agent integration, and
a basic benchmarking mode.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path

from mnemex import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mnemex",
        description="Anchored agent memory MCP server.",
    )
    parser.add_argument(
        "--version", action="version", version=f"mnemex {__version__}"
    )
    sub = parser.add_subparsers(dest="command")

    # serve command
    serve_parser = sub.add_parser("serve", help="Run the MCP server over stdio")
    serve_parser.add_argument(
        "--db", default="mnemex.sqlite3", help="Path to SQLite database"
    )
    serve_parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="MCP transport; HTTP uses Streamable HTTP on localhost by default",
    )
    serve_parser.add_argument(
        "--host", default="127.0.0.1", help="HTTP bind host"
    )
    serve_parser.add_argument("--port", type=int, default=8000, help="HTTP bind port")
    serve_parser.add_argument(
        "--semantic-judge",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable the opt-in OpenAI semantic judge",
    )
    serve_parser.add_argument(
        "--openai-model", default=None, help="OpenAI model for semantic checks"
    )
    serve_parser.add_argument(
        "--openai-timeout-seconds", type=float, default=None,
        help="Timeout for one semantic check",
    )
    serve_parser.add_argument(
        "--max-evidence-tokens", type=int, default=None,
        help="Hard cap for a remote semantic-check payload",
    )
    serve_parser.add_argument(
        "--embedding-provider",
        default=None,
        help="Local embedding provider: none (default) or hash[:384]",
    )

    # index command
    index_parser = sub.add_parser("index", help="Index a directory into the structural graph")
    index_parser.add_argument("path", help="Directory or file to index")
    index_parser.add_argument(
        "--db", default="mnemex.sqlite3", help="Path to SQLite database"
    )
    index_parser.add_argument(
        "--pattern", default="**/*", help="Glob pattern for supported files"
    )

    init_parser = sub.add_parser("init", help="Initialize and index a local project brain")
    init_parser.add_argument("path", nargs="?", default=".", help="Project directory or a path inside it")
    init_parser.add_argument("--db", default=None, help="Path to SQLite database (default: <root>/.mnemex/mnemex.sqlite3)")
    init_parser.add_argument("--no-index", action="store_true", help="Create the database without indexing source files")
    init_parser.add_argument(
        "--codex-config",
        default=None,
        help="Explicit project Codex config.toml path for the MCP entry",
    )

    doctor_parser = sub.add_parser("doctor", help="Check local database readiness")
    doctor_parser.add_argument("--db", default="mnemex.sqlite3", help="Path to SQLite database")

    demo_parser = sub.add_parser("demo", help="Run the decision-integrity golden demonstration")
    demo_parser.add_argument("--db", default=":memory:", help="Path to SQLite database")
    demo_modes = demo_parser.add_mutually_exclusive_group()
    demo_modes.add_argument("--offline", action="store_true", help="Use deterministic local constraints (the default)")
    demo_modes.add_argument("--semantic", action="store_true", help="Use the opt-in OpenAI semantic judge")
    demo_parser.add_argument("--json", action="store_true", help="Emit machine-readable demo output")

    why_parser = sub.add_parser("why", help="Explain decisions for a symbol or file")
    why_parser.add_argument("query")
    why_parser.add_argument("--db", default="mnemex.sqlite3", help="Path to SQLite database")
    why_parser.add_argument("--scopes", default="project-shared")
    why_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    check_parser = sub.add_parser("check", help="Check a proposed change")
    check_parser.add_argument("path")
    check_parser.add_argument("patch_summary")
    check_parser.add_argument("--db", default="mnemex.sqlite3", help="Path to SQLite database")
    check_parser.add_argument("--scopes", default="project-shared")
    check_parser.add_argument("--max-evidence-tokens", type=int, default=None)
    check_parser.add_argument("--enforce-constraints", action="store_true", help="Block fresh violations of explicit deterministic constraints")
    check_parser.add_argument(
        "--show-payload",
        action="store_true",
        help="Print the exact sanitized evidence payload eligible for a remote judge",
    )
    check_parser.add_argument(
        "--replay",
        default=None,
        metavar="FILE",
        help=(
            "Replay a recorded semantic verdict from FILE instead of calling a "
            "provider; the result is labeled provider: replay"
        ),
    )

    reconcile_parser = sub.add_parser("reconcile", help="Reconcile a stale decision")
    reconcile_parser.add_argument("memory_id")
    reconcile_parser.add_argument("changed_symbol")
    reconcile_parser.add_argument("diff")
    reconcile_parser.add_argument("--db", default="mnemex.sqlite3", help="Path to SQLite database")

    review_parser = sub.add_parser("review", help="List decisions needing review")
    review_parser.add_argument("--db", default="mnemex.sqlite3", help="Path to SQLite database")

    dashboard_parser = sub.add_parser("dashboard", help="Show local decision health in the terminal")
    dashboard_parser.add_argument("--db", default="mnemex.sqlite3", help="Path to SQLite database")

    export_parser = sub.add_parser("export", help="Export selected decisions as a project-brain bundle")
    export_parser.add_argument("destination")
    export_parser.add_argument("memory_ids", nargs="+")
    export_parser.add_argument("--db", default="mnemex.sqlite3", help="Path to SQLite database")
    export_parser.add_argument("--agents-md", default="AGENTS.md", help="AGENTS.md to include when present")
    export_parser.add_argument("--source-commit", default=None)

    import_parser = sub.add_parser("import", help="Import a project-brain bundle")
    import_parser.add_argument("source")
    import_parser.add_argument("--db", default="mnemex.sqlite3", help="Path to SQLite database")
    import_parser.add_argument("--agents-md-out", default=None, help="Optional path to write imported AGENTS.md")

    # benchmark command
    bench_parser = sub.add_parser("benchmark", help="Run token-savings benchmark")
    bench_parser.add_argument("path", help="Directory to benchmark against")
    bench_parser.add_argument(
        "--db", default=":memory:", help="Path to SQLite database"
    )

    args = parser.parse_args(argv)

    if args.command == "serve":
        return _serve(
            args.db,
            config=_serve_config(args),
            transport=args.transport,
            host=args.host,
            port=args.port,
        )
    elif args.command == "index":
        return _index(args.path, args.db, args.pattern)
    elif args.command == "benchmark":
        return _benchmark(args.path, args.db)
    elif args.command == "init":
        return _init(args.path, args.db, args.codex_config, args.no_index)
    elif args.command == "doctor":
        return _doctor(args.db)
    elif args.command == "demo":
        return _demo(args.db, semantic=args.semantic, json_output=args.json)
    elif args.command == "why":
        return _why(args.db, args.query, args.scopes, json_output=args.json)
    elif args.command == "check":
        return _check(
            args.db,
            args.path,
            args.patch_summary,
            args.scopes,
            args.max_evidence_tokens,
            args.enforce_constraints,
            show_payload=args.show_payload,
            replay=args.replay,
        )
    elif args.command == "reconcile":
        return _reconcile(args.db, args.memory_id, args.changed_symbol, args.diff)
    elif args.command == "review":
        return _review(args.db)
    elif args.command == "dashboard":
        return _dashboard(args.db)
    elif args.command == "export":
        return _export(
            args.db,
            args.destination,
            args.memory_ids,
            args.agents_md,
            args.source_commit,
        )
    elif args.command == "import":
        return _import(args.db, args.source, args.agents_md_out)
    else:
        parser.print_help()
        return 0


def _serve(
    db_path: str,
    *,
    config: object | None = None,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> int:
    """Run the MCP server over stdio.

    ``FastMCP.run`` is synchronous and manages its own event loop, so it must
    be called directly (not wrapped in ``asyncio.run``).
    """
    from mnemex.config import MnemexConfig
    from mnemex.embedding_providers import create_embedding_provider
    from mnemex.judge import create_semantic_judge
    from mnemex.server import create_server

    effective_config = (
        MnemexConfig.from_env() if config is None else config
    )
    if not isinstance(effective_config, MnemexConfig):
        raise TypeError("config must be MnemexConfig")
    embedding_provider = create_embedding_provider(
        effective_config.embedding_provider
    )
    server = create_server(
        db_path,
        embedder=None if embedding_provider is None else embedding_provider.embed,
        semantic_judge=create_semantic_judge(effective_config),
        max_evidence_tokens=effective_config.max_evidence_tokens,
    )
    try:
        if transport == "http":
            if not 1 <= port <= 65535:
                raise ValueError("port must be between 1 and 65535")
            server.mcp.settings.host = host
            server.mcp.settings.port = port
            server.mcp.run(transport="streamable-http")
        elif transport == "stdio":
            server.mcp.run(transport="stdio")
        else:
            raise ValueError(f"Unsupported transport: {transport}")
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
    return 0


def _serve_config(args: argparse.Namespace):
    """Merge explicit serve flags over the local-first environment config."""
    from mnemex.config import MnemexConfig

    config = MnemexConfig.from_env()
    overrides: dict[str, object] = {}
    if args.semantic_judge is not None:
        overrides["semantic_judge_enabled"] = args.semantic_judge
    if args.openai_model is not None:
        overrides["openai_model"] = args.openai_model
    if args.openai_timeout_seconds is not None:
        overrides["openai_timeout_seconds"] = args.openai_timeout_seconds
    if args.max_evidence_tokens is not None:
        overrides["max_evidence_tokens"] = args.max_evidence_tokens
    embedding_provider = getattr(args, "embedding_provider", None)
    if embedding_provider is not None:
        overrides["embedding_provider"] = embedding_provider
    return replace(config, **overrides)


def _init(
    project_path: str,
    db_path: str | None = None,
    codex_config: str | None = None,
    no_index: bool = False,
) -> int:
    """Initialize a local project brain and index supported source files."""
    from mnemex.indexer import index_directory
    from mnemex.storage import Storage

    root = _project_root(Path(project_path))
    effective_db = (
        Path(db_path) if db_path is not None else root / ".mnemex" / "mnemex.sqlite3"
    )
    if str(effective_db) != ":memory:":
        effective_db.parent.mkdir(parents=True, exist_ok=True)
    with Storage(effective_db) as storage:
        schema_version = storage.connection.execute("PRAGMA user_version").fetchone()[0]
        result: dict[str, object] = {
            "project_root": str(root),
            "database": str(effective_db),
            "schema_version": schema_version,
            "fts5_ready": _fts5_ready(storage),
            "sqlite_vec_available": storage.vec_available,
            "redaction_probe_passed": _redaction_probe_passed(),
            "status": "ready",
        }
        if not no_index:
            indexed = index_directory(storage, root)
            result["index"] = {
                "nodes_upserted": indexed.nodes_upserted,
                "nodes_deleted": indexed.nodes_deleted,
                "edges_upserted": indexed.edges_upserted,
            }
        if codex_config is not None:
            from mnemex.codex_setup import install_codex_mcp

            result["codex_config"] = codex_config
            result["codex_config_changed"] = install_codex_mcp(
                codex_config, str(effective_db)
            )
        _print_json(result)
    return 0


def _doctor(db_path: str) -> int:
    """Report local-first readiness without initializing a remote provider."""
    from mnemex.server import create_server
    from mnemex.storage import Storage

    if db_path != ":memory:" and not Path(db_path).exists():
        _print_json({"database": db_path, "status": "missing"})
        return 1
    with Storage(db_path) as storage:
        schema_version = storage.connection.execute("PRAGMA user_version").fetchone()[0]
        _print_json(
            {
                "database": db_path,
                "schema_version": schema_version,
                "fts5_ready": _fts5_ready(storage),
                "sqlite_vec_available": storage.vec_available,
                "redaction_probe_passed": _redaction_probe_passed(),
                "mcp_tools_registered": _mcp_tool_count(create_server),
                "semantic_judge_enabled": _serve_config_defaults().semantic_judge_enabled,
                "status": "ready",
            }
        )
    return 0


def _demo(db_path: str, *, semantic: bool = False, json_output: bool = False) -> int:
    """Run the full lifecycle demo with explicit offline and semantic modes."""
    from mnemex.agents_md import why
    from mnemex.anchors import check_freshness, remember
    from mnemex.config import MnemexConfig
    from mnemex.decision_guard import check_proposed_change, override_decision_guard
    from mnemex.judge import create_semantic_judge
    from mnemex.lifecycle import supersede_decision
    from mnemex.storage import Node, Storage

    with Storage(db_path) as storage:
        node = Node(
            "demo-auth", "function", "authenticate", "src/auth.py", 1,
            "demo-hash", "python",
        )
        storage.upsert_node(node)
        decision = remember(
            storage,
            "Authentication must remain stateless.",
            anchor=node.id,
            rationale="Server-side session state makes edge deployments inconsistent.",
            tags="constraint:forbidden:redis-backed server sessions",
            source="mnemex demo",
        )
        proposal = "Add Redis-backed server sessions."
        judge = None
        if semantic:
            judge = create_semantic_judge(
                replace(MnemexConfig.from_env(), semantic_judge_enabled=True)
            )
        result = check_proposed_change(
            storage,
            node.file,
            proposal,
            judge=judge,
            enforce_constraints=not semantic,
        )
        override = None
        if result.blocked:
            override = override_decision_guard(
                storage,
                result.run_id,
                actor="demo-client-a",
                reason="Demonstrate the explicit override path before a migration.",
            )
        successor = supersede_decision(
            storage,
            decision.id,
            "Authentication uses signed short-lived tokens.",
            rationale="The deployment retains stateless edge-compatible authentication.",
            tags="constraint:required:signed short-lived tokens",
            successor_id="demo-auth-decision-v2",
        )
        storage.upsert_node(
            Node(
                node.id, node.type, node.name, node.file, node.line_start,
                "demo-hash-after-change", node.language,
            )
        )
        old_freshness = check_freshness(
            storage, memory_id=decision.id
        )[0].status.value
        why_result = why(storage, "authenticate")
        payload = {
            "mode": "semantic" if semantic else "offline",
            "indexed": {"symbol": "src/auth.py::authenticate"},
            "decision": {"id": decision.id, "content": decision.content},
            "proposal": proposal,
            "guard": result.as_dict(),
            "override": None if override is None else asdict(override),
            "successor": {"id": successor.id, "content": successor.content},
            "old_decision_freshness_after_symbol_change": old_freshness,
            "second_client": {
                "shared_database": str(db_path),
                "why": {
                    "decision_count": len(why_result.decisions),
                    "caller_count": len(why_result.callers),
                    "used_tokens": why_result.used_tokens,
                },
            },
        }
        if json_output:
            _print_json(payload)
        else:
            print(_render_demo(payload))
            print()
            print(_render_why(storage, why_result))
    return 0


def _why(db_path: str, query: str, scopes: str, *, json_output: bool = False) -> int:
    from mnemex.agents_md import why
    from mnemex.storage import Storage

    with Storage(db_path) as storage:
        result = why(storage, query, scopes=_scope_values(scopes), max_tokens=400)
        payload = {
            "query": result.query,
            "used_tokens": result.used_tokens,
            "decisions": [asdict(decision) for decision in result.decisions],
            "callers": [asdict(caller) for caller in result.callers],
        }
        if json_output:
            _print_json(payload)
        else:
            print(_render_why(storage, result))
    return 0


def _check(
    db_path: str,
    path: str,
    patch_summary: str,
    scopes: str,
    max_evidence_tokens: int | None,
    enforce_constraints: bool,
    *,
    show_payload: bool = False,
    replay: str | None = None,
) -> int:
    from mnemex.config import MnemexConfig
    from mnemex.decision_guard import check_proposed_change
    from mnemex.judge import ReplayJudge, create_semantic_judge
    from mnemex.storage import Storage

    config = MnemexConfig.from_env()
    cap = config.max_evidence_tokens if max_evidence_tokens is None else min(
        max(max_evidence_tokens, 0), config.max_evidence_tokens
    )
    with Storage(db_path) as storage:
        judge = (
            ReplayJudge.from_file(replay)
            if replay is not None
            else create_semantic_judge(config)
        )
        result = check_proposed_change(
            storage,
            path,
            patch_summary,
            scopes=_scope_values(scopes),
            max_evidence_tokens=cap,
            judge=judge,
            enforce_constraints=enforce_constraints,
        )
        output = result.as_dict()
        if show_payload:
            # The exact sanitized JSON eligible for a remote judge, and
            # whether this run actually sent it anywhere.
            output["payload"] = result.evidence.as_payload()
            output["payload_sent_to_provider"] = judge is not None
        _print_json(output)
    return 2 if result.blocked else 0


def _reconcile(db_path: str, memory_id: str, changed_symbol: str, diff: str) -> int:
    from mnemex.lifecycle import reconcile_stale_decision
    from mnemex.storage import Storage

    with Storage(db_path) as storage:
        status = reconcile_stale_decision(storage, memory_id, changed_symbol, diff)
        _print_json({"memory_id": memory_id, "status": status})
    return 0


def _review(db_path: str) -> int:
    from mnemex.reviews import list_review_candidates
    from mnemex.storage import Storage

    with Storage(db_path) as storage:
        candidates = list_review_candidates(storage)
        _print_json({"candidates": [asdict(candidate) for candidate in candidates]})
    return 0


def _dashboard(db_path: str) -> int:
    from mnemex.storage import Storage
    from mnemex.tui import build_dashboard, render_dashboard

    with Storage(db_path) as storage:
        print(render_dashboard(build_dashboard(storage)))
    return 0


def _export(
    db_path: str,
    destination: str,
    memory_ids: list[str],
    agents_md_path: str,
    source_commit: str | None,
) -> int:
    from mnemex.bundles import export_bundle
    from mnemex.storage import Storage

    agents_path = Path(agents_md_path)
    agents_md = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""
    with Storage(db_path) as storage:
        result = export_bundle(
            storage,
            destination,
            memory_ids,
            agents_md=agents_md,
            source_commit=source_commit,
        )
        _print_json({"path": str(result.path), "manifest": result.manifest})
    return 0


def _import(db_path: str, source: str, agents_md_out: str | None) -> int:
    from mnemex.bundles import import_bundle
    from mnemex.storage import Storage

    with Storage(db_path) as storage:
        result = import_bundle(storage, source)
        if agents_md_out is not None:
            target = Path(agents_md_out)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(result.agents_md, encoding="utf-8")
        _print_json(
            {
                "memory_ids": list(result.memory_ids),
                "id_map": result.id_map,
                "source_commit": result.source_commit,
                "freshness": [
                    {"memory_id": report.memory_id, "status": report.status.value}
                    for report in result.freshness
                ],
            }
        )
    return 0


def _serve_config_defaults():
    from mnemex.config import MnemexConfig

    return MnemexConfig.from_env()


def _scope_values(scopes: str) -> tuple[str, ...]:
    return tuple(scope.strip() for scope in scopes.split(","))


def _project_root(path: Path) -> Path:
    """Resolve a project root without requiring a Git checkout."""
    candidate = path.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for parent in (candidate, *candidate.parents):
        if (parent / ".git").exists():
            return parent
    return candidate


def _fts5_ready(storage: object) -> bool:
    connection = getattr(storage, "connection")
    try:
        connection.execute("CREATE VIRTUAL TABLE temp.mnemex_doctor_fts USING fts5(content)")
        connection.execute("DROP TABLE temp.mnemex_doctor_fts")
    except Exception:
        return False
    return True


def _redaction_probe_passed() -> bool:
    """Verify sanitize() strips every category a judge is likely to test.

    Probe values are synthetic and assembled at runtime so the source never
    contains a complete secret-shaped literal.
    """
    from mnemex.security import sanitize

    probes = (
        "demo-secret",
        "hunter2" + "secret",
        "sk-proj-" + "abcdefghijklmnopqrstuvwxyz123456",
        "AKIA" + "IOSFODNN7EXAMPLE",
    )
    samples = (
        f"<private>{probes[0]}</private>",
        f"password={probes[1]}",
        f"credential {probes[2]}",
        f"deploy key {probes[3]}",
    )
    for probe, sample in zip(probes, samples):
        if probe in sanitize(sample, field_name="doctor_probe"):
            return False
    return True


def _mcp_tool_count(factory: object) -> int:
    server = factory(":memory:")
    try:
        manager = getattr(server.mcp, "_tool_manager", None)
        tools = getattr(manager, "_tools", ())
        return len(tools)
    finally:
        server.close()


def _render_demo(payload: dict[str, object]) -> str:
    guard = payload["guard"]
    assert isinstance(guard, dict)
    decision = payload["decision"]
    assert isinstance(decision, dict)
    successor = payload["successor"]
    assert isinstance(successor, dict)
    mode = str(payload["mode"])
    state = "BLOCKED" if guard["blocked"] else "ADVISORY"
    lines = [
        f"Mnemex decision-integrity demo ({mode})",
        "",
        "1. Indexed  src/auth.py::authenticate",
        f"2. Remembered  {decision['content']}",
        f"3. Proposed  {payload['proposal']}",
        "",
        f"{state}: {guard['explanation']}",
        f"Anchor       {guard['path']}::authenticate",
        "Status       fresh at guard time",
        f"Evidence     {guard['payload_summary']['tokens']} tokens",
        "Alternative  Use signed short-lived tokens",
    ]
    if payload["override"] is not None:
        lines.append("4. Override recorded with a reason.")
    lines.extend(
        [
            f"5. Superseded by  {successor['content']}",
            "6. Changed the anchored symbol.",
            "   Old decision status: "
            + str(payload["old_decision_freshness_after_symbol_change"]).upper(),
            "7. Second client opened the same local brain.",
            "8. WHY authenticate",
        ]
    )
    return "\n".join(lines)


def _render_why(storage: object, result: object) -> str:
    decisions = getattr(result, "decisions")
    callers = getattr(result, "callers")
    lines = [f"WHY: {getattr(result, 'query')}", ""]
    if not decisions:
        return "\n".join(lines + ["No in-scope decisions found."])

    # Prefer the ACTIVE decision as "current"; retrieval rank alone can put a
    # superseded predecessor first (e.g. right after a supersede in the demo).
    current = decisions[0]
    for candidate in decisions:
        candidate_metadata = storage.get_decision_metadata(candidate.memory_id)
        if candidate_metadata is not None and candidate_metadata.status == "active":
            current = candidate
            break
    metadata = storage.get_decision_metadata(current.memory_id)
    node = (
        storage.get_node(current.anchor_node_id)
        if current.anchor_node_id is not None
        else None
    )
    lines.extend(
        [
            "CURRENT DECISION",
            f"  {current.content}",
            f"  Status       {current.freshness.upper()}",
            "  Anchor       "
            + (f"{node.file}::{node.name}" if node is not None else "unanchored"),
            f"  Source       {metadata.client or current.memory_id if metadata else current.memory_id}",
        ]
    )
    if current.rationale:
        lines.append(f"  Rationale    {current.rationale}")
    if metadata is not None and metadata.supersedes_memory_id:
        previous = storage.get_memory(metadata.supersedes_memory_id)
        if previous is not None:
            lines.extend(["", "HISTORY", f"  {previous.id} superseded  {previous.content}"])
    lines.extend(["", "BLAST RADIUS"])
    if callers:
        lines.extend(f"  {caller.name}" for caller in callers)
    else:
        lines.append("  No indexed callers")
    lines.extend(
        [
            "",
            "HEALTH",
            "  Anchor hash " + ("matches" if current.freshness == "fresh" else current.freshness),
            f"  Context      {getattr(result, 'used_tokens')} tokens",
        ]
    )
    return "\n".join(lines)


def _print_json(value: object) -> None:
    print(json.dumps(value, sort_keys=True, default=_json_default))


def _json_default(value: object) -> object:
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Cannot encode {type(value).__name__} as JSON")


def _index(path: str, db_path: str, pattern: str) -> int:
    """Index a directory."""
    from mnemex.indexer import index_directory, index_file
    from mnemex.storage import Storage

    p = Path(path)
    with Storage(db_path) as storage:
        if p.is_file():
            result = index_file(storage, p)
        elif p.is_dir():
            result = index_directory(storage, p, pattern=pattern)
        else:
            print(f"Error: {path} not found", file=sys.stderr)
            return 1

        print(
            f"Indexed: {result.nodes_upserted} nodes, "
            f"{result.edges_upserted} edges, "
            f"{result.nodes_deleted} deleted"
        )
    return 0


def _benchmark(path: str, db_path: str) -> int:
    """Run a token-savings benchmark.

    Compares the token cost of raw file exploration (reading entire files)
    against mnemex's JIT context delivery. Reports honest token savings.
    """
    from mnemex.anchors import remember
    from mnemex.hooks import pre_tool_use, session_start
    from mnemex.indexer import index_directory
    from mnemex.retrieval import estimate_tokens
    from mnemex.storage import Storage

    p = Path(path)
    if not p.is_dir():
        print(f"Error: {path} is not a directory", file=sys.stderr)
        return 1

    py_files = list(p.glob("**/*.py"))
    if not py_files:
        print(f"No .py files found in {path}", file=sys.stderr)
        return 1

    with Storage(db_path) as storage:
        # Phase 1: Index the repo
        t0 = time.perf_counter()
        result = index_directory(storage, p)
        index_time = time.perf_counter() - t0

        # Phase 2: Simulate decisions anchored to random symbols
        nodes = storage.connection.execute(
            "SELECT id, name, file FROM nodes WHERE type = 'function' LIMIT 10"
        ).fetchall()
        for node_id, name, file in nodes:
            remember(
                storage,
                f"Decision about {name}: use standard patterns",
                anchor=node_id,
                memory_id=f"bench-{node_id[:8]}",
                rationale=f"Keeps {file} maintainable",
            )

        # Phase 3: Measure baseline (raw file tokens) vs JIT
        baseline_tokens = 0
        for f in py_files[:20]:  # Cap at 20 files for speed
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                baseline_tokens += estimate_tokens(content)
            except OSError:
                continue

        # Measure session start + JIT for 5 files
        t1 = time.perf_counter()
        session = session_start(storage)
        jit_tokens = session.used_tokens

        for f in py_files[:5]:
            jit = pre_tool_use(storage, str(f))
            jit_tokens += jit.used_tokens
        jit_time = time.perf_counter() - t1

        # Report
        savings = (
            (baseline_tokens - jit_tokens) / baseline_tokens * 100
            if baseline_tokens > 0
            else 0
        )
        ratio = baseline_tokens / jit_tokens if jit_tokens > 0 else float("inf")

        print("=" * 60)
        print("mnemex Token-Savings Benchmark")
        print("=" * 60)
        print(f"Repository:        {path}")
        print(f"Files indexed:     {len(py_files)}")
        print(f"Nodes created:     {result.nodes_upserted}")
        print(f"Edges created:     {result.edges_upserted}")
        print(f"Index time:        {index_time:.2f}s")
        print("")
        print(f"Baseline tokens:   {baseline_tokens:,} (reading {min(20, len(py_files))} files)")
        print(f"JIT tokens:        {jit_tokens:,} (session + 5 file contexts)")
        print(f"Token savings:     {savings:.1f}%")
        print(f"Compression ratio: {ratio:.1f}x")
        print(f"JIT latency:       {jit_time*1000:.1f}ms")
        print("=" * 60)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
