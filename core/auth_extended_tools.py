"""
Fork addition: finish a Google sign-in from a pasted redirect.

start_google_auth prints a URL. On a headless server the browser that opens
it is on another machine, so Google's redirect to localhost dies there. The
user copies that dead address and hands it to complete_google_auth.
"""

import logging

from mcp.types import ToolAnnotations

from auth.google_auth import complete_auth
from auth.oauth_config import is_oauth21_enabled
from core.server import server

logger = logging.getLogger(__name__)


@server.tool(
    title="Complete Google Auth",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def complete_google_auth(authorization_response: str) -> str:
    """
    Finish a sign-in started by start_google_auth when the browser could not
    reach this server (headless host, remote server, no tunnel).

    After the user accepts on Google's page, the browser lands on a localhost
    address that does not load. Paste that full address here. A bare
    `code=...&state=...` fragment or the raw code alone also works.

    Codes work once and expire after 10 minutes. Nothing is stored unless the
    signed-in account passes the allowlist.
    """
    if is_oauth21_enabled():
        return "complete_google_auth is disabled when OAuth 2.1 is enabled."
    try:
        email = await complete_auth(authorization_response)
    except Exception as e:
        logger.error(f"complete_google_auth failed: {e}")
        return f"**Authentication failed:** {e}"
    return (
        f"Signed in as {email}. Credentials are stored. "
        f"Retry the original request with user_google_email='{email}'."
    )
