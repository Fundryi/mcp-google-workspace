"""A guard, because a rule in a document does not enforce itself.

build() hands each tool one httplib2.Http. It is not thread safe: it keeps a
single TLS connection, and two threads writing to it garble the records. The
call fails with ssl.SSLError or a read timeout, every time, naming nothing
useful. This already broke three tools once.

So: no asyncio.gather over per-item Google calls. Batch, or go one at a time.
This test fails if one comes back.
"""

import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

SERVICE_MODULES = (
    "gmail",
    "gdrive",
    "gcalendar",
    "gchat",
    "gcontacts",
    "gtasks",
    "gdocs",
    "gsheets",
    "gslides",
    "gforms",
    "gsearch",
    "gappsscript",
)

# Upstream gather sites serialize Google requests with a semaphore of one.
# Keep the upstream structure, and pin those limits below.
KNOWN_UPSTREAM_FANOUTS = {
    ("gdrive/drive_tools.py", "_bounded_fetch_organizers"),
    ("gchat/chat_tools.py", "fetch_space_messages"),
}


def test_allowlisted_gather_sites_serialize_google_requests():
    from gdrive.drive_tools import SHARED_DRIVE_ORGANIZER_CONCURRENCY_LIMIT
    from gchat.chat_tools import _SEARCH_MESSAGES_MAX_CONCURRENT_SPACE_FETCHES

    assert SHARED_DRIVE_ORGANIZER_CONCURRENCY_LIMIT == 1
    assert _SEARCH_MESSAGES_MAX_CONCURRENT_SPACE_FETCHES == 1


GATHER = re.compile(r"asyncio\.gather\s*\(")


def _gather_sites():
    """Every asyncio.gather in a service module, as (relative path, context)."""
    found = []
    for package in SERVICE_MODULES:
        directory = os.path.join(REPO, package)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".py"):
                continue
            path = os.path.join(directory, name)
            with open(path, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
            for number, line in enumerate(lines):
                if not GATHER.search(line):
                    continue
                # Name the call by whatever it fans out over, which is either
                # on this line or the next one.
                context = " ".join(lines[number : number + 2])
                found.append(
                    (
                        f"{package}/{name}".replace(os.sep, "/"),
                        context,
                        number + 1,
                    )
                )
    return found


def test_no_new_parallel_fanout_over_a_google_service():
    offenders = []
    for relative_path, context, line_number in _gather_sites():
        allowed = any(
            relative_path == path and marker in context
            for path, marker in KNOWN_UPSTREAM_FANOUTS
        )
        if not allowed:
            offenders.append(f"{relative_path}:{line_number}")

    assert not offenders, (
        "asyncio.gather over a Google service is not thread safe and fails with "
        "an SSL error or a read timeout on every call. Use the Gmail batch "
        "endpoint, or one call at a time. See CLAUDE.md. Found at: "
        + ", ".join(offenders)
    )


def test_the_allowlist_still_describes_reality():
    """An allowlisted call that was fixed or moved must leave the list."""
    sites = _gather_sites()
    for path, marker in KNOWN_UPSTREAM_FANOUTS:
        assert any(
            relative_path == path and marker in context
            for relative_path, context, _ in sites
        ), (
            f"{path} no longer fans out over {marker}. Drop it from "
            "KNOWN_UPSTREAM_FANOUTS instead of carrying a stale exception."
        )
