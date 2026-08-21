"""
Email allowlist for account authentication.

Ported from mcp-gmail-multi. Only enforced once configured, so an install
that never sets it up behaves exactly like upstream.

Sources, first one found wins:

1. ``WORKSPACE_ALLOWED_EMAILS`` env var, comma separated addresses.
2. ``allowed.txt`` in the credentials directory, one address per line.
   Blank lines and ``#`` comments are ignored.

Once a source exists, any address not listed is rejected. A configured but
empty list rejects every address. That is intentional: fail closed.
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ALLOWED_EMAILS_ENV = "WORKSPACE_ALLOWED_EMAILS"
ALLOWLIST_FILENAME = "allowed.txt"


class EmailNotAllowedError(PermissionError):
    """Raised when an account is not on the allowlist."""


def _allowlist_path() -> Path:
    # Imported lazily: google_auth imports this module.
    from auth.google_auth import get_default_credentials_dir

    return Path(get_default_credentials_dir()) / ALLOWLIST_FILENAME


def read_allowlist() -> Optional[set]:
    """Return the allowed addresses, lowercased, or None when nothing is configured."""
    raw = os.getenv(ALLOWED_EMAILS_ENV)
    if raw is None:
        path = _allowlist_path()
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
    entries = (entry.strip().lower() for entry in re.split(r"[,\r\n]+", raw))
    return {entry for entry in entries if entry and not entry.startswith("#")}


def is_allowed(email: str) -> bool:
    """True when the allowlist is not configured or lists this address."""
    allowed = read_allowlist()
    if allowed is None:
        return True
    return (email or "").strip().lower() in allowed


def enforce_allowlist(email: str) -> None:
    """Raise EmailNotAllowedError when the address may not authenticate."""
    if is_allowed(email):
        return
    logger.error("SECURITY: '%s' is not on the email allowlist; rejecting.", email)
    raise EmailNotAllowedError(
        f"{email} is not on the email allowlist "
        f"({ALLOWED_EMAILS_ENV} or {ALLOWLIST_FILENAME}). Nothing was saved."
    )
