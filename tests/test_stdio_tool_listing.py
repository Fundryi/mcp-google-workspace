"""Real MCP startup/discovery with empty credentials; no tool calls or Google I/O."""

import asyncio
import json
import os
from pathlib import Path

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [False, True], ids=["gmail", "all-read-only"])
async def test_stdio_tool_listing_and_clean_exit(tmp_path, read_only):
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
    env.update(
        PYTHON_DOTENV_DISABLED="1",
        WORKSPACE_MCP_NO_BROWSER="1",
        WORKSPACE_MCP_CREDENTIALS_DIR=str(tmp_path / "credentials"),
        WORKSPACE_MCP_LOG_DIR=str(tmp_path / "logs"),
        GOOGLE_CLIENT_SECRET_PATH=str(tmp_path / "missing-client.json"),
    )
    args = ["--read-only"] if read_only else ["--tools", "gmail"]
    with (tmp_path / "stderr.log").open("w+") as stderr:
        process = await asyncio.create_subprocess_exec(
            "uv",
            "run",
            "--frozen",
            "python",
            "main.py",
            *args,
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr,
            limit=2**20,
        )

        async def request(message):
            process.stdin.write(
                (json.dumps({"jsonrpc": "2.0", **message}) + "\n").encode()
            )
            await process.stdin.drain()
            if "id" not in message:
                return None
            while True:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=30)
                assert line, "Server exited before responding"
                response = json.loads(line)
                if response.get("id") == message["id"]:
                    assert "error" not in response, response
                    return response["result"]

        try:
            initialized = await request(
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "discovery-smoke", "version": "1"},
                    },
                }
            )
            assert "preserve_context=True" in initialized["instructions"]
            await request({"method": "notifications/initialized"})
            listing = await request({"id": 2, "method": "tools/list", "params": {}})
            assert not listing.get("nextCursor")
            tools = {tool["name"]: tool for tool in listing["tools"]}
            assert len(tools) == len(listing["tools"])
            assert {"search_gmail_messages", "list_gmail_accounts"} <= tools.keys()
            if read_only:
                # These write local credentials/files, using only Google reads.
                local_writes = {
                    "start_google_auth",
                    "complete_google_auth",
                    "get_gmail_attachment_content",
                    "download_chat_attachment",
                }
                writes = [
                    name
                    for name, tool in tools.items()
                    if name not in local_writes
                    and not tool["annotations"]["readOnlyHint"]
                ]
                assert not writes, writes
                assert "send_gmail_message" not in tools
                assert "manage_event" not in tools
                assert (
                    "preserve_context"
                    in tools["get_doc_content"]["inputSchema"]["properties"]
                )
            else:
                from gmail.gmail_extended_tools import (
                    EXTENDED_TOOL_NAMES,
                    DELEGATED_TOOL_NAMES,
                )

                assert len(tools) == 50
                assert (
                    set(EXTENDED_TOOL_NAMES) - set(DELEGATED_TOOL_NAMES) <= tools.keys()
                )
                assert not set(DELEGATED_TOOL_NAMES) & tools.keys()
            process.stdin.close()
            assert await asyncio.wait_for(process.wait(), timeout=15) == 0
            print(
                f"{'All services read-only' if read_only else 'Gmail'}: {len(tools)} tools; initialize/list OK; exit 0"
            )
        finally:
            if process.returncode is None:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=15)
            stderr.seek(0)
            if process.returncode != 0:
                print(stderr.read())
