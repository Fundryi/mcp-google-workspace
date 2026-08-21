"""Log Google accounts in from the terminal.

Starts the server exactly as MCP Router does (reads mcp-router.local.json),
calls start_google_auth for each address, waits until the token is stored,
then moves on. The server opens the browser itself.

    uv run --frozen python scripts/auth.py                 # every address in allowed.txt
    uv run --frozen python scripts/auth.py me@gmail.com    # just these
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
ROUTER_JSON = ROOT / "mcp-router.local.json"


def load_server():
    cfg = json.loads(ROUTER_JSON.read_text(encoding="utf-8"))
    entry = next(iter(cfg["mcpServers"].values()))
    return StdioServerParameters(
        command=entry["command"],
        args=entry["args"],
        env={**os.environ, **entry.get("env", {})},
    )


def wanted_emails(env) -> list:
    if len(sys.argv) > 1:
        return sys.argv[1:]
    path = Path(env["WORKSPACE_MCP_CREDENTIALS_DIR"]) / "allowed.txt"
    return [
        line.strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


async def stored_accounts(session) -> set:
    res = await session.call_tool("list_gmail_accounts", {})
    return {
        line.split(" |")[0].strip().lower() for line in res.content[0].text.splitlines()
    }


async def main() -> int:
    params = load_server()
    emails = wanted_emails(params.env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            done = await stored_accounts(session)
            for email in emails:
                if email in done:
                    print(f"[ok]   {email} already stored")
                    continue
                print(
                    f"[auth] {email}: a browser tab opens. Pick THIS account and accept."
                )
                res = await session.call_tool(
                    "start_google_auth",
                    {"service_name": "gmail", "user_google_email": email},
                )
                text = res.content[0].text
                if "http" in text:
                    url = next(w for w in text.split() if w.startswith("http"))
                    print(f"       If no tab opened, use: {url.rstrip(').,')}")
                for _ in range(90):  # 3 minutes
                    await asyncio.sleep(2)
                    if email in await stored_accounts(session):
                        print(f"[ok]   {email} stored")
                        break
                else:
                    print(f"[skip] {email}: no login within 3 minutes")
            print()
            res = await session.call_tool("list_gmail_accounts", {})
            print(res.content[0].text)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
