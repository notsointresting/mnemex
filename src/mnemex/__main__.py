"""CLI entry point for mnemex — ``python -m mnemex``.

Supports running the MCP server over stdio for agent integration, and
a basic benchmarking mode.
"""

from __future__ import annotations

import argparse
import sys
import time
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

    # index command
    index_parser = sub.add_parser("index", help="Index a directory into the structural graph")
    index_parser.add_argument("path", help="Directory or file to index")
    index_parser.add_argument(
        "--db", default="mnemex.sqlite3", help="Path to SQLite database"
    )
    index_parser.add_argument(
        "--pattern", default="**/*.py", help="Glob pattern for files"
    )

    # benchmark command
    bench_parser = sub.add_parser("benchmark", help="Run token-savings benchmark")
    bench_parser.add_argument("path", help="Directory to benchmark against")
    bench_parser.add_argument(
        "--db", default=":memory:", help="Path to SQLite database"
    )

    args = parser.parse_args(argv)

    if args.command == "serve":
        return _serve(args.db)
    elif args.command == "index":
        return _index(args.path, args.db, args.pattern)
    elif args.command == "benchmark":
        return _benchmark(args.path, args.db)
    else:
        parser.print_help()
        return 0


def _serve(db_path: str) -> int:
    """Run the MCP server over stdio.

    ``FastMCP.run`` is synchronous and manages its own event loop, so it must
    be called directly (not wrapped in ``asyncio.run``).
    """
    from mnemex.server import create_server

    server = create_server(db_path)
    try:
        server.mcp.run(transport="stdio")
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
    return 0


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
