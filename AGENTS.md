# AGENTS.md

Rules for working in this repo. Private notes with local paths live in
CLAUDE.local.md, which is gitignored.

## The one that already bit us

**Never call one Google service object from two threads at once.**

`build()` hands a tool a single `httplib2.Http`. It is not thread safe: it
keeps one TLS connection, and two threads writing to it garble the records.
The caller gets `ssl.SSLError` or a read timeout, neither of which names the
cause, on every call rather than now and then.

That means no `asyncio.gather` over per-item API calls. To fetch or change many
items:

- Gmail: `service.new_batch_http_request()` in chunks of
  `GMAIL_SEARCH_HEADER_BATCH_SIZE`, with a sequential fallback and
  `GMAIL_REQUEST_DELAY` between chunks. Copy `_message_metadata` in
  `gmail/gmail_extended_tools.py` or `_fetch_search_result_headers` in
  `gmail/gmail_tools.py`.
- Anything else: one call at a time, catching per item so one failure does not
  hide what happened to the rest.

`tests/test_no_parallel_fanout.py` fails if a new `asyncio.gather` appears over
a service call. Two upstream fan-outs are allowlisted there with their reasons.

## Before you write a helper that calls Google N times

Grep the module you are editing first. Upstream has already solved batching,
retries, pagination and rate limits, and its helpers carry comments explaining
what they are avoiding. Reuse beats a fresh fan-out.

## Tests

Mock-based tests cannot see transport behaviour, thread safety, connection
reuse, or real API rejections. They prove the shape of a call, nothing more. A
green suite is not evidence a tool works.

A tool that calls Google more than once per invocation needs, on top of unit
tests:

1. A test that drives its batch or sequential path end to end, so the fan-out
   route itself is exercised.
2. A live smoke run against a real account before it is called done.

Say plainly which of the two has actually happened. "Tests pass" is not
"it works".

## Review prompts

When handing this repo to a review agent, ask about the runtime as well as the
contract. The checks that get skipped by default:

- Concurrency and transport: shared clients, threads, connection reuse
- What happens on partial failure, and whether the caller can see it
- Pagination defaults, and per-call caps that silently truncate
- Whether an existing helper already does this

A review that only covers schemas, scopes and API field names will pass code
that fails on the first real call. That has happened here.

## Safe writes

A tool must never change more than the caller asked for, and never hide how
much it destroys.

- Read, merge, write for a partial update. Google's `update` methods are
  usually a full replace; a missing field is a wiped field. Prefer `patch`.
- Echo back what was stored, not what was sent. Google rewrites some writes on
  the way in, and the caller cannot see it otherwise.
- Size the friction to the blast radius: `dry_run=True` for anything driven by
  a query, `confirm=True` for a named target whose cost is hidden, nothing for
  an ordinary single write.

## No permanent delete

No `delete_message`, no `batch_delete`, no `delete_thread`, and never request
the `https://mail.google.com/` scope. Trash and untrash cover the need and can
be undone. A test guards this.

## MCP protocol and SDK

One command serves MCP 2026-07-28 (stateless, `server/discover`, no
`initialize`) and the handshake revisions 2024-11-05 to 2025-11-25, on stdio
and HTTP. That takes FastMCP 4, built on MCP SDK 2. The mcp 1.x line stops at
2025-11-25, its newest release included.

- Keep `fastmcp>=4` in pyproject.toml. Upstream still pins 3.x, so a merge
  conflict there resolves to ours. Re-lock with `uvx uv@0.5.31 lock`, which
  writes the lock format upstream uses. A current uv rewrites every line. The
  header must keep `revision = 1` on line 2: 0.5.31 keeps that line but does
  not add it back once it is gone.
- Failures come back as a tool result with `isError: true` and a text body:
  bad input, Google errors, refusals. An unknown tool name is the one protocol
  error, -32602, raised by `core/unknown_tool_middleware.py` (FastMCP 4 alone
  would return `isError`).
- The caller reads error text. Build it from `safe_error` in
  `handle_http_errors`, which shows URL queries as `?<query-redacted>`: a query
  can carry an API key (`search_custom` sends `key=`).
- `readOnlyHint` states what the tool itself does. `--read-only` gates by
  scope and never reads the hint, so the hint has no safety net. A tool that
  writes local files or credentials says `false`.
- On 2026-07-28 each request is its own connection: `Middleware.on_initialize`
  never runs and `ctx.session_id` is new per HTTP request. Key state by
  `user_google_email` or token. Ask the caller for input through tool
  arguments; `ctx.elicit`, sampling and roots have no back-channel there.
- Read SDK fields in snake_case (`read_only_hint`, `is_error`,
  `input_schema`). The camelCase reads go through a FastMCP bridge that warns
  and is due for removal. camelCase keywords on construction stay valid.
- Verify a protocol change with a real client over stdio: FastMCP 4
  `Client(mode="legacy")`, `mode="2026-07-28"`, `mode="auto"`, an old
  `mcp==1.28.1` client, and the TypeScript `@modelcontextprotocol/sdk@1.25`
  client, which MCP Router 0.6.2 bundles. Expect 50 tools for `--tools gmail` without a service
  account, `isError` for a missing argument, -32602 for an unknown tool, and
  every stdout line valid JSON. The pinned mode skips `server/discover`, so it
  shows no server instructions; legacy and auto do.

## No GitHub workflows

This fork runs no CI. `.github/workflows/` was emptied on purpose: upstream's
Ruff, Pytest and maintainer-edit workflows fired on every push here and served
upstream's process, not ours. Run the checks locally instead, with the commands
below.

Upstream still ships those files, so every merge from upstream will try to add
them back. Delete them again, and do not treat a green or red badge on this
repo as meaningful.

## House style

Python, uv. `uv sync --frozen --group test`, `uv run --frozen pytest`,
`uv run --frozen ruff check .`. Always `--frozen`: a plain `uv sync` rewrites
uv.lock into a newer format and produces a 2000-line diff against upstream.

`ruff check` alone misses formatting. Upstream's CI runs
`uvx ruff@0.15.22 format --check` too, so a formatting slip here also breaks
the next upstream PR. Before pushing, run both at the pinned version:

    uvx ruff@0.15.22 check
    uvx ruff@0.15.22 format --check

A hand-resolved merge conflict is the usual way this breaks: deleting the
`<<<<<<<` markers also eats the blank lines the formatter wants.

On Windows, 7 upstream tests always fail: POSIX file modes and HOME paths in
`test_credential_security.py`, `test_attachment_storage.py`,
`test_startup_ui.py` and `test_oauth_config_client_secret_file.py`, and both
cases of `test_stdio_tool_listing.py`, whose child process gets no home
directory. They pass on Linux. Everything else must be green.

Match upstream's conventions in files we add. New tools go in
`*_extended_tools.py` modules with one import line at the bottom of the
upstream module, so upstream stays mergeable.

## Shared knowledge base

If the shared KB is configured and `../../knowledge-base/AGENTS.md` exists, search it before domain answers or code changes and follow its operating rules.

- Tool ownership: `../../knowledge-base/wiki/_global/Claude and Codex MCP Tool Ownership.md`
- Freshness: when a registered source path changes, follow `../../knowledge-base/wiki/meta/knowledge-freshness.md`.

If the shared KB is unavailable, state that access is unavailable and do not invent its contents.
