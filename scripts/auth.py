"""Log Google accounts in from the terminal.

Starts the server exactly as MCP Router does (reads mcp-router.local.json),
calls start_google_auth for each address, then either waits for the browser
tab or takes the redirect address you paste back. Works on a desktop and on
a headless box over SSH: open the URL on any machine, sign in, copy the
address of the page that fails to load, paste it here.

    uv run --frozen python scripts/auth.py                 # every address in allowed.txt
    uv run --frozen python scripts/auth.py me@gmail.com    # just these
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
ROUTER_JSON = ROOT / "mcp-router.local.json"
SERVER_LOG = Path(tempfile.gettempdir()) / "mcp-google-workspace-auth.log"


def link(url: str, label: str) -> str:
    """OSC 8 hyperlink: terminals that support it show a short clickable label."""
    return f"\033]8;;{url}\033\\{label}\033]8;;\033\\"


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


async def login(session, email: str) -> None:
    res = await session.call_tool(
        "start_google_auth", {"service_name": "gmail", "user_google_email": email}
    )
    text = res.content[0].text
    url = next((w for w in text.split() if w.startswith("http")), None)
    if url is None:
        print(f"[fail] {email}: {text}")
        return
    url = url.rstrip(").,")
    print()
    print(
        f"[auth] {email}: {link(url, 'click here to sign in')}, pick THIS account, accept."
    )
    print("       If the link is not clickable, Ctrl+click or copy this:")
    print(f"       {url}")
    print("       Then paste the address of the page you land on.")
    pasted = await asyncio.to_thread(
        input, "       Paste here (or press Enter if the tab finished by itself): "
    )
    if pasted.strip():
        res = await session.call_tool(
            "complete_google_auth", {"authorization_response": pasted.strip()}
        )
        print(f"       {res.content[0].text}")
        return
    for _ in range(90):  # 3 minutes for the browser tab
        if email in await stored_accounts(session):
            print(f"[ok]   {email} stored")
            return
        await asyncio.sleep(2)
    print(f"[skip] {email}: no login within 3 minutes")


async def main() -> int:
    params = load_server()
    emails = wanted_emails(params.env)
    print(f"Server log goes to {SERVER_LOG}")
    with SERVER_LOG.open("w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                done = await stored_accounts(session)
                for email in emails:
                    if email in done:
                        print(f"[ok]   {email} already stored")
                        continue
                    await login(session, email)
                print()
                res = await session.call_tool("list_gmail_accounts", {})
                print(res.content[0].text)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
