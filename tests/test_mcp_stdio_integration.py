from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def test_stdio_mcp_initialize_list_and_call(tmp_path: Path) -> None:
    asyncio.run(_exercise_stdio_server(tmp_path / "mnemex.sqlite3"))


async def _exercise_stdio_server(database: Path) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mnemex", "serve", "--db", str(database)],
        cwd=str(Path.cwd()),
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "mnemex"

            tools = await session.list_tools()
            assert "check_proposed_change" in {tool.name for tool in tools.tools}

            stored = await session.call_tool(
                "remember_decision",
                {"content": "Keep authentication stateless."},
            )
            assert stored.isError is False

            checked = await session.call_tool(
                "check_proposed_change",
                {
                    "path": "src/auth.py",
                    "patch_summary": "Add server-side session state.",
                },
            )
            assert checked.isError is False
