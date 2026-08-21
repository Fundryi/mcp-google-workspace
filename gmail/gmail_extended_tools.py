"""
Gmail management tools added by this fork.

Ported from Fundryi/mcp-gmail-multi (TypeScript, same Gmail v1 API). Kept in
a separate module so upstream's gmail_tools.py stays mergeable. Trash only,
never permanent delete: that decision carried over from mcp-gmail-multi.
"""

import asyncio
import base64
import logging
from email.message import EmailMessage
from email.policy import SMTP
import functools
import os
from typing import Annotated, Any, Dict, Literal, Optional

from mcp.types import ToolAnnotations
from pydantic import Field

from auth.account_capabilities import format_account_line, is_delegated
from auth.credential_store import get_credential_store
from auth.oauth_config import get_oauth_config, is_service_account_enabled
from auth.scopes import (
    GMAIL_COMPOSE_SCOPE,
    GMAIL_MODIFY_SCOPE,
    GMAIL_SEND_SCOPE,
    GMAIL_SETTINGS_BASIC_SCOPE,
    GMAIL_SETTINGS_SHARING_SCOPE,
)
from auth.service_decorator import require_google_service
from core.server import server
from core.utils import StringList, handle_http_errors
from gmail.gmail_helpers import html_to_text_preserving_breaks
from gmail.gmail_tools import _extract_headers, _extract_message_body

logger = logging.getLogger(__name__)

_READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)
_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)

_OptionalStringList = Annotated[
    Optional[StringList],
    Field(json_schema_extra={"type": "array", "items": {"type": "string"}}),
]


def _format_fields(data: Dict[str, Any], title: Optional[str] = None) -> str:
    """Render an API resource as 'key: value' lines."""
    lines = [title] if title else []
    for key, value in data.items():
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value) or "(none)"
        lines.append(f"{key}: {value}")
    return "\n".join(lines) if lines else "(empty)"


def _drop_none(**kwargs) -> Dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v is not None}


def _delegated_only(func):
    """Refuse, before any Google call, when the account is not delegated.

    gmail.settings.sharing writes only work through a service account with
    domain-wide delegation. Sits outside require_google_service so the
    refusal happens before authentication is even attempted.
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        email = kwargs.get("user_google_email") or os.getenv("USER_GOOGLE_EMAIL", "")
        if not is_delegated(email):
            raise Exception(
                f"{func.__name__} needs a Workspace account served by a delegated "
                f"service account. '{email}' is not one. Call list_gmail_accounts "
                "to see which accounts support this."
            )
        return await func(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


@server.tool(title="List Gmail Accounts", annotations=_READ)
async def list_gmail_accounts() -> str:
    """
    Lists the accounts this server can act on and what each one supports.
    Call this first. Use the email as user_google_email on every other tool.
    "core tools" means everything except the send-as, forwarding-address and
    auto-forwarding writes; those need "all tools" (Workspace, delegated).

    Returns:
        str: One line per account: email | type, auth | tools.
    """
    lines = []
    if is_service_account_enabled():
        domains = get_oauth_config().dwd_allowed_domains or ["(any domain)"]
        lines += [format_account_line(f"<any mailbox>@{d}") for d in domains]
    users = await asyncio.to_thread(get_credential_store().list_users)
    lines += [format_account_line(u) for u in sorted(users)]
    if not lines:
        return "No accounts are authenticated yet. Run start_google_auth first."
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Trash (reversible). No permanent delete tools exist in this fork.
# ---------------------------------------------------------------------------


@server.tool(title="Trash Gmail Message", annotations=_DESTRUCTIVE)
@handle_http_errors("trash_gmail_message", service_type="gmail")
@require_google_service("gmail", GMAIL_MODIFY_SCOPE)
async def trash_gmail_message(service, user_google_email: str, message_id: str) -> str:
    """
    Moves a message to the trash. Reversible with untrash_gmail_message.

    Args:
        user_google_email (str): The user's Google email address. Required.
        message_id (str): The ID of the message to trash.

    Returns:
        str: Confirmation with the message ID.
    """
    logger.info(
        f"[trash_gmail_message] Email: '{user_google_email}', ID: '{message_id}'"
    )
    await asyncio.to_thread(
        service.users().messages().trash(userId="me", id=message_id).execute
    )
    return f"Message moved to trash.\nMessage ID: {message_id}"


@server.tool(title="Untrash Gmail Message", annotations=_WRITE)
@handle_http_errors("untrash_gmail_message", service_type="gmail")
@require_google_service("gmail", GMAIL_MODIFY_SCOPE)
async def untrash_gmail_message(
    service, user_google_email: str, message_id: str
) -> str:
    """
    Removes a message from the trash.

    Args:
        user_google_email (str): The user's Google email address. Required.
        message_id (str): The ID of the message to restore.

    Returns:
        str: Confirmation with the message ID.
    """
    logger.info(
        f"[untrash_gmail_message] Email: '{user_google_email}', ID: '{message_id}'"
    )
    await asyncio.to_thread(
        service.users().messages().untrash(userId="me", id=message_id).execute
    )
    return f"Message restored from trash.\nMessage ID: {message_id}"


@server.tool(title="Trash Gmail Thread", annotations=_DESTRUCTIVE)
@handle_http_errors("trash_gmail_thread", service_type="gmail")
@require_google_service("gmail", GMAIL_MODIFY_SCOPE)
async def trash_gmail_thread(service, user_google_email: str, thread_id: str) -> str:
    """
    Moves a whole thread to the trash. Reversible with untrash_gmail_thread.

    Args:
        user_google_email (str): The user's Google email address. Required.
        thread_id (str): The ID of the thread to trash.

    Returns:
        str: Confirmation with the thread ID.
    """
    logger.info(f"[trash_gmail_thread] Email: '{user_google_email}', ID: '{thread_id}'")
    await asyncio.to_thread(
        service.users().threads().trash(userId="me", id=thread_id).execute
    )
    return f"Thread moved to trash.\nThread ID: {thread_id}"


@server.tool(title="Untrash Gmail Thread", annotations=_WRITE)
@handle_http_errors("untrash_gmail_thread", service_type="gmail")
@require_google_service("gmail", GMAIL_MODIFY_SCOPE)
async def untrash_gmail_thread(service, user_google_email: str, thread_id: str) -> str:
    """
    Removes a whole thread from the trash.

    Args:
        user_google_email (str): The user's Google email address. Required.
        thread_id (str): The ID of the thread to restore.

    Returns:
        str: Confirmation with the thread ID.
    """
    logger.info(
        f"[untrash_gmail_thread] Email: '{user_google_email}', ID: '{thread_id}'"
    )
    await asyncio.to_thread(
        service.users().threads().untrash(userId="me", id=thread_id).execute
    )
    return f"Thread restored from trash.\nThread ID: {thread_id}"


# ---------------------------------------------------------------------------
# Drafts (upstream only creates them)
# ---------------------------------------------------------------------------

_DRAFT_HEADERS = ["Subject", "From", "To", "Cc", "Bcc", "In-Reply-To", "References"]


@server.tool(title="List Gmail Drafts", annotations=_READ)
@handle_http_errors("list_gmail_drafts", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def list_gmail_drafts(
    service,
    user_google_email: str,
    query: Optional[str] = None,
    max_results: int = 20,
    include_spam_trash: bool = False,
) -> str:
    """
    Lists drafts in the user's mailbox with their subject and recipients.

    Args:
        user_google_email (str): The user's Google email address. Required.
        query (Optional[str]): Gmail search query to filter drafts.
        max_results (int): Maximum number of drafts to return (1-100). Defaults to 20.
        include_spam_trash (bool): Include drafts from SPAM and TRASH.

    Returns:
        str: One line per draft with draft ID, thread ID, subject, and To.
    """
    logger.info(f"[list_gmail_drafts] Email: '{user_google_email}', Query: '{query}'")
    params = _drop_none(
        userId="me",
        q=query,
        maxResults=max(1, min(max_results, 100)),
        includeSpamTrash=include_spam_trash or None,
    )
    response = await asyncio.to_thread(service.users().drafts().list(**params).execute)
    drafts = response.get("drafts", [])
    if not drafts:
        return "No drafts found."

    lines = [f"Found {len(drafts)} drafts:", ""]
    for draft in drafts:
        detail = await asyncio.to_thread(
            service.users()
            .drafts()
            .get(
                userId="me",
                id=draft["id"],
                format="metadata",
                metadataHeaders=["Subject", "To"],
            )
            .execute
        )
        message = detail.get("message", {})
        headers = _extract_headers(message.get("payload", {}), ["Subject", "To"])
        lines.append(
            f"- Draft ID: {draft['id']} | Thread ID: {message.get('threadId', '')} | "
            f"Subject: {headers.get('Subject', '(no subject)')} | "
            f"To: {headers.get('To', '(none)')}"
        )
    if response.get("nextPageToken"):
        lines.append("")
        lines.append("More drafts exist; narrow the query or raise max_results.")
    return "\n".join(lines)


@server.tool(title="Get Gmail Draft", annotations=_READ)
@handle_http_errors("get_gmail_draft", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def get_gmail_draft(service, user_google_email: str, draft_id: str) -> str:
    """
    Gets a draft's headers and plain text body.

    Args:
        user_google_email (str): The user's Google email address. Required.
        draft_id (str): The ID of the draft.

    Returns:
        str: Draft ID, thread ID, headers, and body.
    """
    logger.info(f"[get_gmail_draft] Email: '{user_google_email}', ID: '{draft_id}'")
    draft = await asyncio.to_thread(
        service.users().drafts().get(userId="me", id=draft_id, format="full").execute
    )
    message = draft.get("message", {})
    payload = message.get("payload", {})
    headers = _extract_headers(payload, _DRAFT_HEADERS)
    lines = [
        f"Draft ID: {draft.get('id')}",
        f"Message ID: {message.get('id', '')}",
        f"Thread ID: {message.get('threadId', '')}",
    ]
    lines += [f"{name}: {headers[name]}" for name in _DRAFT_HEADERS if name in headers]
    lines += ["", "--- BODY ---", _extract_message_body(payload) or "(empty)"]
    return "\n".join(lines)


def _build_draft_raw(
    subject: str,
    body: str,
    body_format: str,
    headers: Dict[str, str],
) -> str:
    """Build a base64url RFC 2822 message. The subject is used as given."""
    message = EmailMessage(policy=SMTP)
    message["Subject"] = subject
    for name in ("From", "To", "Cc", "Bcc", "In-Reply-To", "References"):
        if headers.get(name):
            message[name] = headers[name]
    if body_format == "html":
        message.set_content(html_to_text_preserving_breaks(body).strip())
        message.add_alternative(body, subtype="html")
    else:
        message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


@server.tool(title="Update Gmail Draft", annotations=_WRITE)
@handle_http_errors("update_gmail_draft", service_type="gmail")
@require_google_service("gmail", GMAIL_COMPOSE_SCOPE)
async def update_gmail_draft(
    service,
    user_google_email: str,
    draft_id: str,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    body_format: Literal["plain", "html"] = "plain",
    to: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> str:
    """
    Replaces a draft's content. Any field left out is copied from the current
    draft, including the thread and reply headers. An explicit subject is
    always used as given; it is never rewritten from the thread.

    Args:
        user_google_email (str): The user's Google email address. Required.
        draft_id (str): The ID of the draft to update.
        subject (Optional[str]): New subject. Defaults to the current subject.
        body (Optional[str]): New body. Defaults to the current plain text body.
        body_format (Literal["plain", "html"]): Format of body. Defaults to plain.
        to (Optional[str]): New To header. Defaults to the current one.
        cc (Optional[str]): New Cc header. Defaults to the current one.
        bcc (Optional[str]): New Bcc header. Defaults to the current one.
        thread_id (Optional[str]): Thread to attach to. Defaults to the current one.

    Returns:
        str: Confirmation with the draft ID.
    """
    logger.info(f"[update_gmail_draft] Email: '{user_google_email}', ID: '{draft_id}'")
    current = await asyncio.to_thread(
        service.users().drafts().get(userId="me", id=draft_id, format="full").execute
    )
    message = current.get("message", {})
    payload = message.get("payload", {})
    headers = _extract_headers(payload, _DRAFT_HEADERS)
    if to is not None:
        headers["To"] = to
    if cc is not None:
        headers["Cc"] = cc
    if bcc is not None:
        headers["Bcc"] = bcc

    final_subject = subject if subject is not None else headers.get("Subject", "")
    final_body = body if body is not None else _extract_message_body(payload)
    raw = _build_draft_raw(final_subject, final_body, body_format, headers)

    new_message: Dict[str, Any] = {"raw": raw}
    final_thread_id = thread_id or message.get("threadId")
    if final_thread_id:
        new_message["threadId"] = final_thread_id

    updated = await asyncio.to_thread(
        service.users()
        .drafts()
        .update(userId="me", id=draft_id, body={"message": new_message})
        .execute
    )
    return f"Draft updated.\nDraft ID: {updated.get('id', draft_id)}\nSubject: {final_subject}"


@server.tool(title="Delete Gmail Draft", annotations=_DESTRUCTIVE)
@handle_http_errors("delete_gmail_draft", service_type="gmail")
@require_google_service("gmail", GMAIL_COMPOSE_SCOPE)
async def delete_gmail_draft(service, user_google_email: str, draft_id: str) -> str:
    """
    Deletes a draft. Drafts have no trash; this cannot be undone.

    Args:
        user_google_email (str): The user's Google email address. Required.
        draft_id (str): The ID of the draft to delete.

    Returns:
        str: Confirmation with the draft ID.
    """
    logger.info(f"[delete_gmail_draft] Email: '{user_google_email}', ID: '{draft_id}'")
    await asyncio.to_thread(
        service.users().drafts().delete(userId="me", id=draft_id).execute
    )
    return f"Draft deleted.\nDraft ID: {draft_id}"


@server.tool(title="Send Gmail Draft", annotations=_WRITE)
@handle_http_errors("send_gmail_draft", service_type="gmail")
@require_google_service("gmail", GMAIL_SEND_SCOPE)
async def send_gmail_draft(service, user_google_email: str, draft_id: str) -> str:
    """
    Sends an existing draft.

    Args:
        user_google_email (str): The user's Google email address. Required.
        draft_id (str): The ID of the draft to send.

    Returns:
        str: Confirmation with the sent message ID.
    """
    logger.info(f"[send_gmail_draft] Email: '{user_google_email}', ID: '{draft_id}'")
    sent = await asyncio.to_thread(
        service.users().drafts().send(userId="me", body={"id": draft_id}).execute
    )
    return (
        f"Draft sent.\nMessage ID: {sent.get('id')}\nThread ID: {sent.get('threadId')}"
    )


# ---------------------------------------------------------------------------
# Threads and labels, small gaps
# ---------------------------------------------------------------------------


@server.tool(title="List Gmail Threads", annotations=_READ)
@handle_http_errors("list_gmail_threads", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def list_gmail_threads(
    service,
    user_google_email: str,
    query: Optional[str] = None,
    max_results: int = 20,
    label_ids: _OptionalStringList = None,
    include_spam_trash: bool = False,
    page_token: Optional[str] = None,
) -> str:
    """
    Lists threads with their snippets. Use get_gmail_thread_content for bodies.

    Args:
        user_google_email (str): The user's Google email address. Required.
        query (Optional[str]): Gmail search query.
        max_results (int): Maximum number of threads to return (1-100). Defaults to 20.
        label_ids (Optional[List[str]]): Only threads carrying all of these labels.
        include_spam_trash (bool): Include threads from SPAM and TRASH.
        page_token (Optional[str]): Token from a previous call for the next page.

    Returns:
        str: One line per thread with ID and snippet, plus the next page token.
    """
    logger.info(f"[list_gmail_threads] Email: '{user_google_email}', Query: '{query}'")
    params = _drop_none(
        userId="me",
        q=query,
        maxResults=max(1, min(max_results, 100)),
        labelIds=label_ids or None,
        includeSpamTrash=include_spam_trash or None,
        pageToken=page_token,
    )
    response = await asyncio.to_thread(service.users().threads().list(**params).execute)
    threads = response.get("threads", [])
    if not threads:
        return "No threads found."
    lines = [f"Found {len(threads)} threads:", ""]
    lines += [f"- Thread ID: {t['id']} | {t.get('snippet', '')}" for t in threads]
    if response.get("nextPageToken"):
        lines += ["", f"Next page token: {response['nextPageToken']}"]
    return "\n".join(lines)


@server.tool(title="Modify Gmail Thread Labels", annotations=_DESTRUCTIVE)
@handle_http_errors("modify_gmail_thread_labels", service_type="gmail")
@require_google_service("gmail", GMAIL_MODIFY_SCOPE)
async def modify_gmail_thread_labels(
    service,
    user_google_email: str,
    thread_id: str,
    add_label_ids: _OptionalStringList = None,
    remove_label_ids: _OptionalStringList = None,
) -> str:
    """
    Adds or removes labels on every message in a thread.
    To archive a thread, remove the INBOX label.

    Args:
        user_google_email (str): The user's Google email address. Required.
        thread_id (str): The ID of the thread to modify.
        add_label_ids (Optional[List[str]]): Label IDs to add.
        remove_label_ids (Optional[List[str]]): Label IDs to remove.

    Returns:
        str: Confirmation of the label changes.
    """
    logger.info(
        f"[modify_gmail_thread_labels] Email: '{user_google_email}', ID: '{thread_id}'"
    )
    if not add_label_ids and not remove_label_ids:
        raise Exception(
            "At least one of add_label_ids or remove_label_ids must be provided."
        )
    body = _drop_none(
        addLabelIds=add_label_ids or None, removeLabelIds=remove_label_ids or None
    )
    await asyncio.to_thread(
        service.users().threads().modify(userId="me", id=thread_id, body=body).execute
    )
    actions = []
    if add_label_ids:
        actions.append(f"Added labels: {', '.join(add_label_ids)}")
    if remove_label_ids:
        actions.append(f"Removed labels: {', '.join(remove_label_ids)}")
    return f"Thread labels updated.\nThread ID: {thread_id}\n{'; '.join(actions)}"


@server.tool(title="Get Gmail Label", annotations=_READ)
@handle_http_errors("get_gmail_label", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def get_gmail_label(service, user_google_email: str, label_id: str) -> str:
    """
    Gets one label with its visibility settings and message counts.

    Args:
        user_google_email (str): The user's Google email address. Required.
        label_id (str): The label ID (for example INBOX or Label_123).

    Returns:
        str: Label fields as key: value lines.
    """
    logger.info(f"[get_gmail_label] Email: '{user_google_email}', ID: '{label_id}'")
    label = await asyncio.to_thread(
        service.users().labels().get(userId="me", id=label_id).execute
    )
    return _format_fields(label)


@server.tool(title="Get Gmail Filter", annotations=_READ)
@handle_http_errors("get_gmail_filter", is_read_only=True, service_type="gmail")
@require_google_service("gmail", GMAIL_SETTINGS_BASIC_SCOPE)
async def get_gmail_filter(service, user_google_email: str, filter_id: str) -> str:
    """
    Gets one filter's criteria and action.

    Args:
        user_google_email (str): The user's Google email address. Required.
        filter_id (str): The filter ID.

    Returns:
        str: Filter ID, criteria, and action.
    """
    logger.info(f"[get_gmail_filter] Email: '{user_google_email}', ID: '{filter_id}'")
    result = await asyncio.to_thread(
        service.users().settings().filters().get(userId="me", id=filter_id).execute
    )
    return (
        f"Filter ID: {result.get('id')}\n"
        f"Criteria: {result.get('criteria') or '(none)'}\n"
        f"Action: {result.get('action') or '(none)'}"
    )


# ---------------------------------------------------------------------------
# Profile and push notifications
# ---------------------------------------------------------------------------


@server.tool(title="Get Gmail Profile", annotations=_READ)
@handle_http_errors("get_gmail_profile", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def get_gmail_profile(service, user_google_email: str) -> str:
    """
    Gets the mailbox profile: address, message and thread totals, history ID.

    Args:
        user_google_email (str): The user's Google email address. Required.

    Returns:
        str: Profile fields as key: value lines.
    """
    logger.info(f"[get_gmail_profile] Email: '{user_google_email}'")
    profile = await asyncio.to_thread(service.users().getProfile(userId="me").execute)
    return _format_fields(profile)


@server.tool(title="Watch Gmail Mailbox", annotations=_WRITE)
@handle_http_errors("watch_gmail_mailbox", service_type="gmail")
@require_google_service("gmail", GMAIL_MODIFY_SCOPE)
async def watch_gmail_mailbox(
    service,
    user_google_email: str,
    topic_name: str,
    label_ids: _OptionalStringList = None,
    label_filter_behavior: Optional[Literal["include", "exclude"]] = None,
) -> str:
    """
    Starts push notifications to a Cloud Pub/Sub topic. Expires after 7 days.

    Args:
        user_google_email (str): The user's Google email address. Required.
        topic_name (str): Full topic name, projects/<project>/topics/<topic>.
        label_ids (Optional[List[str]]): Restrict notifications to these labels.
        label_filter_behavior (Optional[str]): "include" or "exclude" the labels.

    Returns:
        str: History ID and expiration of the watch.
    """
    logger.info(f"[watch_gmail_mailbox] Email: '{user_google_email}'")
    body = _drop_none(
        topicName=topic_name,
        labelIds=label_ids or None,
        labelFilterBehavior=label_filter_behavior,
    )
    result = await asyncio.to_thread(
        service.users().watch(userId="me", body=body).execute
    )
    return _format_fields(result, "Mailbox watch started.")


@server.tool(title="Stop Gmail Mailbox Watch", annotations=_WRITE)
@handle_http_errors("stop_gmail_mailbox_watch", service_type="gmail")
@require_google_service("gmail", GMAIL_MODIFY_SCOPE)
async def stop_gmail_mailbox_watch(service, user_google_email: str) -> str:
    """
    Stops push notifications for the mailbox.

    Args:
        user_google_email (str): The user's Google email address. Required.

    Returns:
        str: Confirmation.
    """
    logger.info(f"[stop_gmail_mailbox_watch] Email: '{user_google_email}'")
    await asyncio.to_thread(service.users().stop(userId="me").execute)
    return "Mailbox watch stopped."


# ---------------------------------------------------------------------------
# Settings: vacation, IMAP, POP, language (gmail.settings.basic)
# ---------------------------------------------------------------------------


@server.tool(title="Get Gmail Vacation Settings", annotations=_READ)
@handle_http_errors(
    "get_gmail_vacation_settings", is_read_only=True, service_type="gmail"
)
@require_google_service("gmail", GMAIL_SETTINGS_BASIC_SCOPE)
async def get_gmail_vacation_settings(service, user_google_email: str) -> str:
    """
    Gets the vacation responder (auto reply) settings.

    Args:
        user_google_email (str): The user's Google email address. Required.

    Returns:
        str: Vacation settings as key: value lines.
    """
    logger.info(f"[get_gmail_vacation_settings] Email: '{user_google_email}'")
    result = await asyncio.to_thread(
        service.users().settings().getVacation(userId="me").execute
    )
    return _format_fields(result)


@server.tool(title="Update Gmail Vacation Settings", annotations=_WRITE)
@handle_http_errors("update_gmail_vacation_settings", service_type="gmail")
@require_google_service("gmail", GMAIL_SETTINGS_BASIC_SCOPE)
async def update_gmail_vacation_settings(
    service,
    user_google_email: str,
    enable_auto_reply: bool,
    response_body_plain_text: Optional[str] = None,
    response_body_html: Optional[str] = None,
    response_subject: Optional[str] = None,
    restrict_to_contacts: Optional[bool] = None,
    restrict_to_domain: Optional[bool] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> str:
    """
    Updates the vacation responder. Fields left out are cleared by Gmail.

    Args:
        user_google_email (str): The user's Google email address. Required.
        enable_auto_reply (bool): Turn the responder on or off.
        response_body_plain_text (Optional[str]): Reply body in plain text.
        response_body_html (Optional[str]): Reply body in HTML.
        response_subject (Optional[str]): Reply subject.
        restrict_to_contacts (Optional[bool]): Only reply to contacts.
        restrict_to_domain (Optional[bool]): Only reply to the same domain.
        start_time (Optional[str]): Start, epoch milliseconds.
        end_time (Optional[str]): End, epoch milliseconds.

    Returns:
        str: The stored vacation settings.
    """
    logger.info(f"[update_gmail_vacation_settings] Email: '{user_google_email}'")
    body = _drop_none(
        enableAutoReply=enable_auto_reply,
        responseBodyPlainText=response_body_plain_text,
        responseBodyHtml=response_body_html,
        responseSubject=response_subject,
        restrictToContacts=restrict_to_contacts,
        restrictToDomain=restrict_to_domain,
        startTime=start_time,
        endTime=end_time,
    )
    result = await asyncio.to_thread(
        service.users().settings().updateVacation(userId="me", body=body).execute
    )
    return _format_fields(result, "Vacation settings updated.")


@server.tool(title="Get Gmail IMAP Settings", annotations=_READ)
@handle_http_errors("get_gmail_imap_settings", is_read_only=True, service_type="gmail")
@require_google_service("gmail", GMAIL_SETTINGS_BASIC_SCOPE)
async def get_gmail_imap_settings(service, user_google_email: str) -> str:
    """
    Gets the IMAP settings.

    Args:
        user_google_email (str): The user's Google email address. Required.

    Returns:
        str: IMAP settings as key: value lines.
    """
    logger.info(f"[get_gmail_imap_settings] Email: '{user_google_email}'")
    result = await asyncio.to_thread(
        service.users().settings().getImap(userId="me").execute
    )
    return _format_fields(result)


@server.tool(title="Update Gmail IMAP Settings", annotations=_WRITE)
@handle_http_errors("update_gmail_imap_settings", service_type="gmail")
@require_google_service("gmail", GMAIL_SETTINGS_BASIC_SCOPE)
async def update_gmail_imap_settings(
    service,
    user_google_email: str,
    enabled: bool,
    auto_expunge: Optional[bool] = None,
    expunge_behavior: Optional[Literal["archive", "trash", "deleteForever"]] = None,
    max_folder_size: Optional[int] = None,
) -> str:
    """
    Updates the IMAP settings.

    Args:
        user_google_email (str): The user's Google email address. Required.
        enabled (bool): Whether IMAP is enabled.
        auto_expunge (Optional[bool]): Expunge immediately on delete.
        expunge_behavior (Optional[str]): archive, trash, or deleteForever.
        max_folder_size (Optional[int]): Message cap per IMAP folder, 0 for none.

    Returns:
        str: The stored IMAP settings.
    """
    logger.info(f"[update_gmail_imap_settings] Email: '{user_google_email}'")
    body = _drop_none(
        enabled=enabled,
        autoExpunge=auto_expunge,
        expungeBehavior=expunge_behavior,
        maxFolderSize=max_folder_size,
    )
    result = await asyncio.to_thread(
        service.users().settings().updateImap(userId="me", body=body).execute
    )
    return _format_fields(result, "IMAP settings updated.")


@server.tool(title="Get Gmail POP Settings", annotations=_READ)
@handle_http_errors("get_gmail_pop_settings", is_read_only=True, service_type="gmail")
@require_google_service("gmail", GMAIL_SETTINGS_BASIC_SCOPE)
async def get_gmail_pop_settings(service, user_google_email: str) -> str:
    """
    Gets the POP settings.

    Args:
        user_google_email (str): The user's Google email address. Required.

    Returns:
        str: POP settings as key: value lines.
    """
    logger.info(f"[get_gmail_pop_settings] Email: '{user_google_email}'")
    result = await asyncio.to_thread(
        service.users().settings().getPop(userId="me").execute
    )
    return _format_fields(result)


@server.tool(title="Update Gmail POP Settings", annotations=_WRITE)
@handle_http_errors("update_gmail_pop_settings", service_type="gmail")
@require_google_service("gmail", GMAIL_SETTINGS_BASIC_SCOPE)
async def update_gmail_pop_settings(
    service,
    user_google_email: str,
    access_window: Literal["disabled", "fromNowOn", "allMail"],
    disposition: Literal["leaveInInbox", "archive", "trash", "markRead"],
) -> str:
    """
    Updates the POP settings.

    Args:
        user_google_email (str): The user's Google email address. Required.
        access_window (str): disabled, fromNowOn, or allMail.
        disposition (str): What happens to mail after POP fetch.

    Returns:
        str: The stored POP settings.
    """
    logger.info(f"[update_gmail_pop_settings] Email: '{user_google_email}'")
    body = {"accessWindow": access_window, "disposition": disposition}
    result = await asyncio.to_thread(
        service.users().settings().updatePop(userId="me", body=body).execute
    )
    return _format_fields(result, "POP settings updated.")


@server.tool(title="Get Gmail Language Settings", annotations=_READ)
@handle_http_errors(
    "get_gmail_language_settings", is_read_only=True, service_type="gmail"
)
@require_google_service("gmail", GMAIL_SETTINGS_BASIC_SCOPE)
async def get_gmail_language_settings(service, user_google_email: str) -> str:
    """
    Gets the display language setting.

    Args:
        user_google_email (str): The user's Google email address. Required.

    Returns:
        str: The display language as an RFC 3066 tag.
    """
    logger.info(f"[get_gmail_language_settings] Email: '{user_google_email}'")
    result = await asyncio.to_thread(
        service.users().settings().getLanguage(userId="me").execute
    )
    return _format_fields(result)


@server.tool(title="Update Gmail Language Settings", annotations=_WRITE)
@handle_http_errors("update_gmail_language_settings", service_type="gmail")
@require_google_service("gmail", GMAIL_SETTINGS_BASIC_SCOPE)
async def update_gmail_language_settings(
    service, user_google_email: str, display_language: str
) -> str:
    """
    Updates the display language.

    Args:
        user_google_email (str): The user's Google email address. Required.
        display_language (str): RFC 3066 language tag, for example en-GB or de.

    Returns:
        str: The stored language setting.
    """
    logger.info(f"[update_gmail_language_settings] Email: '{user_google_email}'")
    result = await asyncio.to_thread(
        service.users()
        .settings()
        .updateLanguage(userId="me", body={"displayLanguage": display_language})
        .execute
    )
    return _format_fields(result, "Language updated.")


# ---------------------------------------------------------------------------
# Settings: auto-forwarding, forwarding addresses, send-as aliases.
# Reads need gmail.settings.basic; writes need gmail.settings.sharing, which
# only works with domain-wide delegation on a Workspace tenant.
# ---------------------------------------------------------------------------


@server.tool(title="Get Gmail Auto Forwarding", annotations=_READ)
@handle_http_errors(
    "get_gmail_auto_forwarding", is_read_only=True, service_type="gmail"
)
@require_google_service("gmail", GMAIL_SETTINGS_BASIC_SCOPE)
async def get_gmail_auto_forwarding(service, user_google_email: str) -> str:
    """
    Gets the auto-forwarding setting for the whole mailbox.

    Args:
        user_google_email (str): The user's Google email address. Required.

    Returns:
        str: Auto-forwarding settings as key: value lines.
    """
    logger.info(f"[get_gmail_auto_forwarding] Email: '{user_google_email}'")
    result = await asyncio.to_thread(
        service.users().settings().getAutoForwarding(userId="me").execute
    )
    return _format_fields(result)


@server.tool(title="Update Gmail Auto Forwarding", annotations=_WRITE)
@handle_http_errors("update_gmail_auto_forwarding", service_type="gmail")
@_delegated_only
@require_google_service("gmail", GMAIL_SETTINGS_SHARING_SCOPE)
async def update_gmail_auto_forwarding(
    service,
    user_google_email: str,
    enabled: bool,
    email_address: Optional[str] = None,
    disposition: Optional[
        Literal["leaveInInbox", "archive", "trash", "markRead"]
    ] = None,
) -> str:
    """
    Turns auto-forwarding of all incoming mail on or off. The target must be
    a verified forwarding address. Needs domain-wide delegation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        enabled (bool): Whether to forward all incoming mail.
        email_address (Optional[str]): Verified forwarding address. Required when enabling.
        disposition (Optional[str]): What happens to forwarded mail in this mailbox.

    Returns:
        str: The stored auto-forwarding settings.
    """
    logger.info(f"[update_gmail_auto_forwarding] Email: '{user_google_email}'")
    if enabled and not email_address:
        raise Exception("email_address is required when enabling auto-forwarding.")
    body = _drop_none(
        enabled=enabled, emailAddress=email_address, disposition=disposition
    )
    result = await asyncio.to_thread(
        service.users().settings().updateAutoForwarding(userId="me", body=body).execute
    )
    return _format_fields(result, "Auto-forwarding updated.")


@server.tool(title="List Gmail Forwarding Addresses", annotations=_READ)
@handle_http_errors(
    "list_gmail_forwarding_addresses", is_read_only=True, service_type="gmail"
)
@require_google_service("gmail", GMAIL_SETTINGS_BASIC_SCOPE)
async def list_gmail_forwarding_addresses(service, user_google_email: str) -> str:
    """
    Lists the forwarding addresses and their verification status.

    Args:
        user_google_email (str): The user's Google email address. Required.

    Returns:
        str: One line per address.
    """
    logger.info(f"[list_gmail_forwarding_addresses] Email: '{user_google_email}'")
    result = await asyncio.to_thread(
        service.users().settings().forwardingAddresses().list(userId="me").execute
    )
    addresses = result.get("forwardingAddresses", [])
    if not addresses:
        return "No forwarding addresses."
    return "\n".join(
        f"- {a.get('forwardingEmail')} ({a.get('verificationStatus', 'unknown')})"
        for a in addresses
    )


@server.tool(title="Get Gmail Forwarding Address", annotations=_READ)
@handle_http_errors(
    "get_gmail_forwarding_address", is_read_only=True, service_type="gmail"
)
@require_google_service("gmail", GMAIL_SETTINGS_BASIC_SCOPE)
async def get_gmail_forwarding_address(
    service, user_google_email: str, forwarding_email: str
) -> str:
    """
    Gets one forwarding address and its verification status.

    Args:
        user_google_email (str): The user's Google email address. Required.
        forwarding_email (str): The forwarding address.

    Returns:
        str: Address fields as key: value lines.
    """
    logger.info(f"[get_gmail_forwarding_address] Email: '{user_google_email}'")
    result = await asyncio.to_thread(
        service.users()
        .settings()
        .forwardingAddresses()
        .get(userId="me", forwardingEmail=forwarding_email)
        .execute
    )
    return _format_fields(result)


@server.tool(title="Create Gmail Forwarding Address", annotations=_WRITE)
@handle_http_errors("create_gmail_forwarding_address", service_type="gmail")
@_delegated_only
@require_google_service("gmail", GMAIL_SETTINGS_SHARING_SCOPE)
async def create_gmail_forwarding_address(
    service, user_google_email: str, forwarding_email: str
) -> str:
    """
    Adds a forwarding address. Gmail sends a verification mail to it unless
    the address is in the same Workspace domain. Needs domain-wide delegation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        forwarding_email (str): The address to add.

    Returns:
        str: The created address and its verification status.
    """
    logger.info(f"[create_gmail_forwarding_address] Email: '{user_google_email}'")
    result = await asyncio.to_thread(
        service.users()
        .settings()
        .forwardingAddresses()
        .create(userId="me", body={"forwardingEmail": forwarding_email})
        .execute
    )
    return _format_fields(result, "Forwarding address created.")


@server.tool(title="Delete Gmail Forwarding Address", annotations=_DESTRUCTIVE)
@handle_http_errors("delete_gmail_forwarding_address", service_type="gmail")
@_delegated_only
@require_google_service("gmail", GMAIL_SETTINGS_SHARING_SCOPE)
async def delete_gmail_forwarding_address(
    service, user_google_email: str, forwarding_email: str
) -> str:
    """
    Removes a forwarding address and any filters that use it. Needs domain-wide delegation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        forwarding_email (str): The address to remove.

    Returns:
        str: Confirmation.
    """
    logger.info(f"[delete_gmail_forwarding_address] Email: '{user_google_email}'")
    await asyncio.to_thread(
        service.users()
        .settings()
        .forwardingAddresses()
        .delete(userId="me", forwardingEmail=forwarding_email)
        .execute
    )
    return f"Forwarding address deleted: {forwarding_email}"


@server.tool(title="List Gmail Send-As Aliases", annotations=_READ)
@handle_http_errors("list_gmail_send_as", is_read_only=True, service_type="gmail")
@require_google_service("gmail", GMAIL_SETTINGS_BASIC_SCOPE)
async def list_gmail_send_as(service, user_google_email: str) -> str:
    """
    Lists the send-as aliases (Settings > Accounts > Send mail as).

    Args:
        user_google_email (str): The user's Google email address. Required.

    Returns:
        str: One line per alias with name, primary flag, and verification status.
    """
    logger.info(f"[list_gmail_send_as] Email: '{user_google_email}'")
    result = await asyncio.to_thread(
        service.users().settings().sendAs().list(userId="me").execute
    )
    aliases = result.get("sendAs", [])
    if not aliases:
        return "No send-as aliases."
    lines = []
    for alias in aliases:
        flags = []
        if alias.get("isPrimary"):
            flags.append("primary")
        if alias.get("isDefault"):
            flags.append("default")
        if alias.get("verificationStatus"):
            flags.append(alias["verificationStatus"])
        lines.append(
            f"- {alias.get('sendAsEmail')} | {alias.get('displayName', '')} | "
            f"{', '.join(flags) or '-'}"
        )
    return "\n".join(lines)


@server.tool(title="Get Gmail Send-As Alias", annotations=_READ)
@handle_http_errors("get_gmail_send_as", is_read_only=True, service_type="gmail")
@require_google_service("gmail", GMAIL_SETTINGS_BASIC_SCOPE)
async def get_gmail_send_as(service, user_google_email: str, send_as_email: str) -> str:
    """
    Gets one send-as alias, including its signature HTML.

    Args:
        user_google_email (str): The user's Google email address. Required.
        send_as_email (str): The alias address.

    Returns:
        str: Alias fields as key: value lines.
    """
    logger.info(f"[get_gmail_send_as] Email: '{user_google_email}'")
    result = await asyncio.to_thread(
        service.users()
        .settings()
        .sendAs()
        .get(userId="me", sendAsEmail=send_as_email)
        .execute
    )
    return _format_fields(result)


@server.tool(title="Create Gmail Send-As Alias", annotations=_WRITE)
@handle_http_errors("create_gmail_send_as", service_type="gmail")
@_delegated_only
@require_google_service("gmail", GMAIL_SETTINGS_SHARING_SCOPE)
async def create_gmail_send_as(
    service,
    user_google_email: str,
    send_as_email: str,
    display_name: Optional[str] = None,
    reply_to_address: Optional[str] = None,
    signature: Optional[str] = None,
    treat_as_alias: Optional[bool] = None,
) -> str:
    """
    Creates a send-as alias. Addresses outside the Workspace domain get a
    verification mail. Needs domain-wide delegation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        send_as_email (str): The address that appears in From.
        display_name (Optional[str]): Name that appears in From.
        reply_to_address (Optional[str]): Reply-To header value.
        signature (Optional[str]): HTML signature.
        treat_as_alias (Optional[bool]): Treat as alias of the primary address.

    Returns:
        str: The created alias.
    """
    logger.info(f"[create_gmail_send_as] Email: '{user_google_email}'")
    body = _drop_none(
        sendAsEmail=send_as_email,
        displayName=display_name,
        replyToAddress=reply_to_address,
        signature=signature,
        treatAsAlias=treat_as_alias,
    )
    result = await asyncio.to_thread(
        service.users().settings().sendAs().create(userId="me", body=body).execute
    )
    return _format_fields(result, "Send-as alias created.")


@server.tool(title="Update Gmail Send-As Alias", annotations=_WRITE)
@handle_http_errors("update_gmail_send_as", service_type="gmail")
@_delegated_only
@require_google_service("gmail", GMAIL_SETTINGS_SHARING_SCOPE)
async def update_gmail_send_as(
    service,
    user_google_email: str,
    send_as_email: str,
    display_name: Optional[str] = None,
    reply_to_address: Optional[str] = None,
    signature: Optional[str] = None,
    is_default: Optional[bool] = None,
    treat_as_alias: Optional[bool] = None,
) -> str:
    """
    Updates fields of a send-as alias. Only the given fields change (patch).
    Also works on the primary address, for example to set its signature.

    Args:
        user_google_email (str): The user's Google email address. Required.
        send_as_email (str): The alias to update.
        display_name (Optional[str]): Name that appears in From.
        reply_to_address (Optional[str]): Reply-To header value.
        signature (Optional[str]): HTML signature. Empty string clears it.
        is_default (Optional[bool]): Make this the default From address.
        treat_as_alias (Optional[bool]): Treat as alias of the primary address.

    Returns:
        str: The updated alias.
    """
    logger.info(f"[update_gmail_send_as] Email: '{user_google_email}'")
    body = _drop_none(
        displayName=display_name,
        replyToAddress=reply_to_address,
        signature=signature,
        isDefault=is_default,
        treatAsAlias=treat_as_alias,
    )
    if not body:
        raise Exception("Provide at least one field to update.")
    result = await asyncio.to_thread(
        service.users()
        .settings()
        .sendAs()
        .patch(userId="me", sendAsEmail=send_as_email, body=body)
        .execute
    )
    return _format_fields(result, "Send-as alias updated.")


@server.tool(title="Delete Gmail Send-As Alias", annotations=_DESTRUCTIVE)
@handle_http_errors("delete_gmail_send_as", service_type="gmail")
@_delegated_only
@require_google_service("gmail", GMAIL_SETTINGS_SHARING_SCOPE)
async def delete_gmail_send_as(
    service, user_google_email: str, send_as_email: str
) -> str:
    """
    Deletes a send-as alias. The primary address cannot be deleted. Needs domain-wide delegation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        send_as_email (str): The alias to delete.

    Returns:
        str: Confirmation.
    """
    logger.info(f"[delete_gmail_send_as] Email: '{user_google_email}'")
    await asyncio.to_thread(
        service.users()
        .settings()
        .sendAs()
        .delete(userId="me", sendAsEmail=send_as_email)
        .execute
    )
    return f"Send-as alias deleted: {send_as_email}"


@server.tool(title="Verify Gmail Send-As Alias", annotations=_WRITE)
@handle_http_errors("verify_gmail_send_as", service_type="gmail")
@_delegated_only
@require_google_service("gmail", GMAIL_SETTINGS_SHARING_SCOPE)
async def verify_gmail_send_as(
    service, user_google_email: str, send_as_email: str
) -> str:
    """
    Resends the verification mail for a pending send-as alias. Needs domain-wide delegation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        send_as_email (str): The alias to verify.

    Returns:
        str: Confirmation.
    """
    logger.info(f"[verify_gmail_send_as] Email: '{user_google_email}'")
    await asyncio.to_thread(
        service.users()
        .settings()
        .sendAs()
        .verify(userId="me", sendAsEmail=send_as_email)
        .execute
    )
    return f"Verification mail sent for: {send_as_email}"


EXTENDED_TOOL_NAMES = [
    "list_gmail_accounts",
    "trash_gmail_message",
    "untrash_gmail_message",
    "trash_gmail_thread",
    "untrash_gmail_thread",
    "list_gmail_drafts",
    "get_gmail_draft",
    "update_gmail_draft",
    "delete_gmail_draft",
    "send_gmail_draft",
    "list_gmail_threads",
    "modify_gmail_thread_labels",
    "get_gmail_label",
    "get_gmail_filter",
    "get_gmail_profile",
    "watch_gmail_mailbox",
    "stop_gmail_mailbox_watch",
    "get_gmail_vacation_settings",
    "update_gmail_vacation_settings",
    "get_gmail_imap_settings",
    "update_gmail_imap_settings",
    "get_gmail_pop_settings",
    "update_gmail_pop_settings",
    "get_gmail_language_settings",
    "update_gmail_language_settings",
    "get_gmail_auto_forwarding",
    "update_gmail_auto_forwarding",
    "list_gmail_forwarding_addresses",
    "get_gmail_forwarding_address",
    "create_gmail_forwarding_address",
    "delete_gmail_forwarding_address",
    "list_gmail_send_as",
    "get_gmail_send_as",
    "create_gmail_send_as",
    "update_gmail_send_as",
    "delete_gmail_send_as",
    "verify_gmail_send_as",
]

DELEGATED_TOOL_NAMES = [
    "update_gmail_auto_forwarding",
    "create_gmail_forwarding_address",
    "delete_gmail_forwarding_address",
    "create_gmail_send_as",
    "update_gmail_send_as",
    "delete_gmail_send_as",
    "verify_gmail_send_as",
]


def _hide_delegated_tools_without_service_account() -> None:
    """Without a service account nobody can ever use these, so drop them."""
    if is_service_account_enabled():
        return
    for name in DELEGATED_TOOL_NAMES:
        try:
            server.local_provider.remove_tool(name)
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("Could not hide %s: %s", name, exc)


_hide_delegated_tools_without_service_account()
