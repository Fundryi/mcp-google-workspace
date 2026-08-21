"""Check whether upstream has new commits and write a hand-off prompt for an AI.

Runs on folder open through .vscode/tasks.json (open the folder or the
.code-workspace file in VS Code and allow automatic tasks once), or by hand:
    python scripts/check_upstream.py
Writes UPSTREAM-UPDATE.md (gitignored) when there is something to merge.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "UPSTREAM-UPDATE.md"

# Upstream files this fork edited. Additive lines only, but a conflict can still land here.
FORK_TOUCHED = [
    "auth/scopes.py",
    "auth/permissions.py",
    "auth/google_auth.py",
    "auth/service_decorator.py",
    "core/tool_tiers.yaml",
    "core/log_formatter.py",
    "gmail/gmail_tools.py",
    ".gitignore",
    "README.md",
]

PROMPT = """Merge the latest upstream into this fork without losing our additions.

Context: this repo is a private fork of taylorwilsdon/google_workspace_mcp.
Our additions are listed in README.md ("About this fork") and CLAUDE.local.md.
New tools live in gmail/gmail_extended_tools.py and auth/allowlist.py,
auth/account_capabilities.py. Upstream files only carry additive lines.

Steps:
1. git fetch upstream && git merge upstream/main
2. If a file conflicts, keep upstream's structure and re-attach our lines
   (the list of touched files is below).
3. uv sync --frozen --group test
4. uv run --frozen ruff check . && uv run --frozen pytest
   (on Windows 4 upstream tests about POSIX file modes and HOME always fail)
5. Check tests/gmail/test_gmail_extended_tools.py still pins 37 tools and
   start the server with --tools gmail: 45 tools without a service account.
6. Read the upstream commits below. If one adds a Gmail feature we also
   ported, prefer upstream's version and delete ours.
7. Commit and push.
"""


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def main() -> int:
    try:
        git("fetch", "upstream", "--quiet")
    except subprocess.CalledProcessError as exc:
        print(f"Could not fetch upstream: {exc.stderr.strip()}")
        return 1

    base = sys.argv[1] if len(sys.argv) > 1 else "main"  # override for testing
    log = git("log", "--oneline", f"{base}..upstream/main")
    if not log:
        print("Upstream: up to date.")
        if REPORT.exists():
            REPORT.unlink()
        return 0

    commits = log.splitlines()
    changed = git("diff", "--name-only", f"{base}...upstream/main").splitlines()
    overlap = [f for f in changed if f in FORK_TOUCHED]
    tags = git("tag", "--points-at", "upstream/main")

    lines = [
        f"# Upstream has {len(commits)} new commit(s)" + (f" ({tags})" if tags else ""),
        "",
        "Paste the block below into the AI chat.",
        "",
        "```",
        PROMPT.rstrip(),
        "",
        "Upstream files we also edited (watch these for conflicts):",
        *(f"- {f}" for f in overlap or ["none"]),
        "",
        "New upstream commits:",
        *(f"- {c}" for c in commits),
        "```",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")

    print(
        f"Upstream: {len(commits)} new commit(s). Hand-off written to UPSTREAM-UPDATE.md"
    )
    # Pop the hand-off into the editor so the update is hard to miss.
    subprocess.run(["code", "-r", str(REPORT)], shell=True, check=False)
    print("Files we also edited:", ", ".join(overlap) or "none")
    for c in commits[:15]:
        print(" ", c)
    if len(commits) > 15:
        print(f"  ... and {len(commits) - 15} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
