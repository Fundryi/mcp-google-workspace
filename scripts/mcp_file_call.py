#!/usr/bin/env python3
"""Call one MCPHub tool with local files, without the file passing through a model.

For agents whose MCP servers run behind MCPHub on another machine: a file path
on the agent's machine means nothing to the server, and pasting base64 into a
tool call puts the whole file into the model's context. This script reads the
file itself and sends it straight to the hub.

    python3 mcp_file_call.py <server> <tool> <args.json> [--out FILE]

args.json holds the tool arguments. Any string "@file:<path>" is replaced by
the file's standard base64. Set the attachment's filename (Gmail) or name
(Fastmail) yourself. With --out, the one base64 file in the result is saved to
FILE instead of printed.

Only use this when the server cannot see the file. A server on the same
machine takes a plain path, which is cheaper.

Hub address and key, first match wins:
  MCPHUB_URL and MCPHUB_KEY environment variables;
  the "mcphub" entry (any case) in ~/.codex/config.toml, then ~/.claude.json.

Exit codes: 0 any tool result (including a tool error), 1 network or HTTP
error, 2 bad usage or a request the hub would refuse. Never retries: a send
that timed out may still have happened.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

FILE_PREFIX = "@file:"
DEFAULT_MAX_BODY = 70 * 1024 * 1024  # MCPHub routing.jsonBodyLimit "70mb"
TIMEOUT_S = 330  # above MCPHub's per-server tool timeout of 300 s
OUTPUT_LIMIT = 20_000
B64_RUN = re.compile(r"[A-Za-z0-9+/]{1000,}={0,2}")


class UsageError(Exception):
    pass


def swap_files(value, warnings: list[str], parent: dict | None = None):
    """Return value with every "@file:<path>" string replaced by base64.

    Returns (new_value, total_file_bytes).
    """
    if isinstance(value, str) and value.startswith(FILE_PREFIX):
        path = Path(os.path.expanduser(value[len(FILE_PREFIX) :]))
        if not path.is_file():
            raise UsageError(f"not a readable file: {path}")
        data = path.read_bytes()
        if parent is not None and not (parent.get("filename") or parent.get("name")):
            warnings.append(
                f"{path.name}: no filename/name next to it; the server may drop it"
            )
        return base64.b64encode(data).decode("ascii"), len(data)
    if isinstance(value, dict):
        total = 0
        out = {}
        for key, item in value.items():
            out[key], size = swap_files(item, warnings, value)
            total += size
        return out, total
    if isinstance(value, list):
        total = 0
        out_list = []
        for item in value:
            new, size = swap_files(item, warnings, None)
            out_list.append(new)
            total += size
        return out_list, total
    return value, 0


def result_texts(body) -> tuple[bool, list[str]]:
    """(is_error, texts) from a hub reply. structuredContent is ignored on purpose."""
    if (
        isinstance(body, dict)
        and "content" not in body
        and isinstance(body.get("data"), dict)
    ):
        body = body["data"]
    if not isinstance(body, dict):
        return True, [json.dumps(body)[:OUTPUT_LIMIT]]
    if "content" not in body:
        return True, [str(body.get("message") or body.get("error") or json.dumps(body))]
    texts = [
        c.get("text", "")
        for c in body.get("content") or []
        if isinstance(c, dict) and c.get("type") == "text"
    ]
    return bool(body.get("isError")), texts


def strip_base64(text: str) -> str:
    def repl(m: re.Match) -> str:
        return f"[base64, {len(m.group(0)) * 3 // 4} bytes removed]"

    return B64_RUN.sub(repl, text)


def extract_one_base64(texts: list[str]) -> bytes:
    runs = {m.group(0) for t in texts for m in B64_RUN.finditer(t)}
    if len(runs) != 1:
        raise UsageError(
            f"--out needs exactly one base64 file in the result, found {len(runs)}"
        )
    try:
        return base64.b64decode(runs.pop(), validate=True)
    except binascii.Error as exc:
        raise UsageError(f"result base64 does not decode: {exc}") from exc


def _from_codex() -> tuple[str | None, str | None]:
    path = Path.home() / ".codex" / "config.toml"
    if not path.is_file():
        return None, None
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        return None, None
    servers = tomllib.loads(path.read_text(encoding="utf-8")).get("mcp_servers", {})
    for name, entry in servers.items():
        if name.lower() == "mcphub" and isinstance(entry, dict):
            headers = entry.get("http_headers") or entry.get("headers") or {}
            return entry.get("url"), headers.get("Authorization")
    return None, None


def _from_claude() -> tuple[str | None, str | None]:
    path = Path.home() / ".claude.json"
    if not path.is_file():
        return None, None
    servers = json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})
    for name, entry in servers.items():
        if name.lower() == "mcphub" and isinstance(entry, dict):
            return entry.get("url"), (entry.get("headers") or {}).get("Authorization")
    return None, None


def hub_settings() -> tuple[str, str]:
    url, key = os.environ.get("MCPHUB_URL"), os.environ.get("MCPHUB_KEY")
    for source in (_from_codex, _from_claude):
        if url and key:
            break
        found_url, found_key = source()
        url, key = url or found_url, key or found_key
    if not url or not key:
        raise UsageError("hub not found: set MCPHUB_URL and MCPHUB_KEY")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise UsageError("hub URL must be http(s)://host")
    key = key.strip()
    if not key.lower().startswith("bearer "):
        key = f"Bearer {key}"
    return f"{parts.scheme}://{parts.netloc}", key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("server")
    parser.add_argument("tool")
    parser.add_argument("args_file", help="JSON file with the tool arguments")
    parser.add_argument("--out", help="save the one base64 file in the result here")
    parser.add_argument(
        "--max-body",
        type=int,
        default=int(os.environ.get("MCPHUB_MAX_BODY", DEFAULT_MAX_BODY)),
        help="the hub's request size limit in bytes (default 70 MiB)",
    )
    opts = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    try:
        args = json.loads(Path(opts.args_file).read_text(encoding="utf-8"))
        if not isinstance(args, dict):
            raise UsageError("args file must hold a JSON object")
        warnings: list[str] = []
        args, file_bytes = swap_files(args, warnings)
        body = json.dumps(args).encode("utf-8")
        if len(body) > opts.max_body:
            raise UsageError(
                f"request is {len(body)} bytes ({file_bytes} bytes of files); "
                f"the hub accepts {opts.max_body}"
            )
        base, key = hub_settings()
    except (UsageError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    url = f"{base}/api/tools/{quote(opts.server, safe='')}/{quote(opts.tool, safe='')}"
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": key},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            reply = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        hint = (
            " (the hub refuses requests above its size limit with 500)"
            if exc.code == 500
            else ""
        )
        print(f"error: HTTP {exc.code} from hub{hint}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(
            f"error: hub unreachable or timed out: {exc}. A send may still have happened.",
            file=sys.stderr,
        )
        return 1
    except json.JSONDecodeError:
        print("error: hub reply is not JSON", file=sys.stderr)
        return 1

    is_error, texts = result_texts(reply)
    print("tool error" if is_error else "ok")
    if opts.out and not is_error:
        try:
            data = extract_one_base64(texts)
            out = Path(os.path.expanduser(opts.out))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            print(f"[saved {len(data)} bytes to {out}]")
        except (UsageError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    text = strip_base64("\n".join(texts))
    if len(text) > OUTPUT_LIMIT:
        text = text[:OUTPUT_LIMIT] + "\n[output cut]"
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
