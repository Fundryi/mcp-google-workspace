# CLAUDE.md

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

`ruff check` is only half of CI. The Ruff workflow also runs
`uvx ruff@0.15.22 format --check`, and its autofix job is skipped on a direct
push to main, so a formatting slip fails the run with nothing to repair it.
Before pushing, run both at the pinned version:

    uvx ruff@0.15.22 check
    uvx ruff@0.15.22 format --check

A hand-resolved merge conflict is the usual way this breaks: deleting the
`<<<<<<<` markers also eats the blank lines the formatter wants.

On Windows, 4 upstream tests always fail (POSIX file modes and a HOME path).
They pass on Linux. Everything else must be green.

Match upstream's conventions in files we add. New tools go in
`*_extended_tools.py` modules with one import line at the bottom of the
upstream module, so upstream stays mergeable.
