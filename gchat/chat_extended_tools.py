"""
Chat tools added by this fork.

Upstream can list spaces and read and send messages. It cannot say who is in a
space, and it cannot correct a message once sent. These do.

Google Chat is a Workspace product. A private gmail.com account has no Chat
API access at all, so these tools say that plainly instead of letting Google
answer with a bare 403.
"""

import asyncio
import logging
from typing import Any, Dict, List

from mcp.types import ToolAnnotations

from auth.account_capabilities import is_private_account
from auth.service_decorator import require_google_service
from core.server import server
from core.utils import handle_http_errors

logger = logging.getLogger(__name__)

_READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)


def _require_workspace_account(email: str, tool: str) -> None:
    """Stops a private account before Google answers with a bare 403."""
    if is_private_account(email):
        raise Exception(
            f"{tool} needs a Google Workspace account. The Chat API is not open to "
            f"private accounts, and {email} is one. Use a Workspace address."
        )


async def _all_pages(request_for, key: str) -> List[Dict[str, Any]]:
    """Collects every page of a Chat list call."""
    items: List[Dict[str, Any]] = []
    page_token = None
    while True:
        response = await asyncio.to_thread(request_for(page_token).execute)
        items += response.get(key, [])
        page_token = response.get("nextPageToken")
        if not page_token:
            return items


@server.tool(title="List Chat Members", annotations=_READ)
@handle_http_errors("list_chat_members", is_read_only=True, service_type="chat")
@require_google_service("chat", "chat_memberships_read")
async def list_chat_members(
    service,
    user_google_email: str,
    space_name: str,
    include_groups: bool = True,
    show_invited: bool = True,
) -> str:
    """
    Lists who is in a Chat space, with their role and membership state.
    Workspace accounts only.

    Args:
        user_google_email (str): The user's Google email address. Required.
        space_name (str): The space, as "spaces/AAAA..." or just the id.
        include_groups (bool): Include Google Group memberships, not only people. Defaults to True.
        show_invited (bool): Include people who were invited but have not joined. Defaults to True.

    Returns:
        str: One line per member.
    """
    logger.info(
        f"[list_chat_members] Email: '{user_google_email}', Space: '{space_name}'"
    )
    _require_workspace_account(user_google_email, "list_chat_members")

    parent = space_name if space_name.startswith("spaces/") else f"spaces/{space_name}"
    members = await _all_pages(
        lambda token: (
            service.spaces()
            .members()
            .list(
                parent=parent,
                pageSize=100,
                pageToken=token,
                showGroups=include_groups,
                showInvited=show_invited,
            )
        ),
        "memberships",
    )
    if not members:
        return f"No members returned for {parent}."

    lines = [f"{len(members)} members of {parent}:", ""]
    for member in members:
        who = (
            (member.get("member") or {}).get("displayName")
            or (member.get("member") or {}).get("name")
            or (member.get("groupMember") or {}).get("name")
            or "(unknown)"
        )
        details = [
            part
            for part in (
                member.get("role"),
                member.get("state"),
                "group" if member.get("groupMember") else None,
            )
            if part
        ]
        lines.append(f"  • {who}: {', '.join(details) or '(no details)'}")
    return "\n".join(lines)


@server.tool(title="Update Chat Message", annotations=_WRITE)
@handle_http_errors("update_chat_message", service_type="chat")
@require_google_service("chat", "chat_write")
async def update_chat_message(
    service,
    user_google_email: str,
    message_name: str,
    text: str,
) -> str:
    """
    Rewrites the text of a message you already sent, instead of posting a
    correction underneath it. Only messages sent by this account can be
    changed, and the space sees the edit. Workspace accounts only.

    Args:
        user_google_email (str): The user's Google email address. Required.
        message_name (str): The message, as "spaces/AAA/messages/BBB".
        text (str): The new text. It replaces the old text completely.

    Returns:
        str: The message as it now stands.
    """
    logger.info(
        f"[update_chat_message] Email: '{user_google_email}', Message: '{message_name}'"
    )
    _require_workspace_account(user_google_email, "update_chat_message")

    if not message_name.startswith("spaces/"):
        raise Exception(
            'message_name must look like "spaces/AAA/messages/BBB". '
            "get_messages returns it on each message."
        )

    # updateMask is what keeps this a text edit. Without it Chat would clear
    # every field left out of the body, cards included.
    updated = await asyncio.to_thread(
        service.spaces()
        .messages()
        .patch(
            name=message_name,
            updateMask="text",
            allowMissing=False,
            body={"text": text},
        )
        .execute
    )
    return "\n".join(
        [
            f"Message updated: {updated.get('name', message_name)}",
            f"  Text now: {updated.get('text', text)}",
            f"  Last update: {updated.get('lastUpdateTime', '(not reported)')}",
        ]
    )


EXTENDED_CHAT_TOOL_NAMES = ["list_chat_members", "update_chat_message"]
