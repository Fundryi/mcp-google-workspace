"""
Calendar tools added by this fork.

Upstream can create a calendar and manage its events. It cannot rename or
delete one, share it, or change what shows up in the calendar list. These do.

Kept in a separate module so upstream's calendar_tools.py stays mergeable,
the same way gmail/gmail_extended_tools.py works.
"""

import asyncio
import logging
from typing import Any, Dict, List, Literal, Optional

from mcp.types import ToolAnnotations

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import handle_http_errors

logger = logging.getLogger(__name__)

_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)


async def _all_pages(request_for) -> List[Dict[str, Any]]:
    """Collects every page of a Calendar list call.

    Both acl.list and calendarList.list stop at 100 entries per page, so a
    single call quietly reports a partial answer as if it were the whole one.
    """
    items: List[Dict[str, Any]] = []
    page_token = None
    while True:
        response = await asyncio.to_thread(request_for(page_token).execute)
        items += response.get("items", [])
        page_token = response.get("nextPageToken")
        if not page_token:
            return items


def _acl_line(rule: Dict[str, Any]) -> str:
    """One line per sharing rule."""
    scope = rule.get("scope", {})
    who = scope.get("value") or scope.get("type", "?")
    return f"  • {who} ({scope.get('type', '?')}): {rule.get('role', '?')} [rule id: {rule.get('id', '?')}]"


def _calendar_fields(calendar: Dict[str, Any]) -> List[str]:
    """Renders the fields a calendar or calendar-list entry carries."""
    keys = (
        ("summary", "Name"),
        ("summaryOverride", "Your name for it"),
        ("description", "Description"),
        ("location", "Location"),
        ("timeZone", "Time zone"),
        ("accessRole", "Your access"),
        ("colorId", "Color id"),
    )
    lines = [
        f"    {caption}: {calendar[key]}" for key, caption in keys if calendar.get(key)
    ]
    for key, caption in (("hidden", "Hidden"), ("selected", "Shown in the grid")):
        if key in calendar:
            lines.append(f"    {caption}: {calendar[key]}")
    return lines


@server.tool(
    title="Get Calendar Settings",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("get_calendar_settings", is_read_only=True, service_type="calendar")
@require_google_service("calendar", "calendar_read")
async def get_calendar_settings(
    service,
    user_google_email: str,
    setting_id: Optional[str] = None,
) -> str:
    """
    Reads the account's own calendar settings: time zone, locale, week start,
    12 or 24 hour clock, and how invitations are handled. Read these before
    writing times or reading a calendar, so the numbers mean what you think.

    Args:
        user_google_email (str): The user's Google email address. Required.
        setting_id (Optional[str]): Read one setting, for example "timezone". Omit for all of them.

    Returns:
        str: The settings as name: value lines.
    """
    logger.info(
        f"[get_calendar_settings] Email: '{user_google_email}', Setting: '{setting_id}'"
    )

    if setting_id:
        setting = await asyncio.to_thread(
            service.settings().get(setting=setting_id).execute
        )
        return f"{setting.get('id', setting_id)}: {setting.get('value', '(unset)')}"

    settings = await _all_pages(lambda token: service.settings().list(pageToken=token))
    if not settings:
        return "No calendar settings returned."
    return "\n".join(
        [f"{len(settings)} calendar settings:", ""]
        + [
            f"  • {setting.get('id')}: {setting.get('value', '(unset)')}"
            for setting in sorted(settings, key=lambda s: s.get("id", ""))
        ]
    )


@server.tool(title="Manage Calendar", annotations=_WRITE)
@handle_http_errors("manage_calendar", service_type="calendar")
@require_google_service("calendar", "calendar")
async def manage_calendar(
    service,
    user_google_email: str,
    action: Literal["get", "update", "delete", "clear"],
    calendar_id: str,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    timezone: Optional[str] = None,
    confirm: bool = False,
) -> str:
    """
    Reads, renames, deletes or empties a calendar itself, not its events.

    An update writes only the fields you pass; the rest keep their values.
    Delete and clear both destroy events for good, so both need confirm=True.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (Literal["get", "update", "delete", "clear"]): What to do. "delete" removes a secondary calendar and every event on it. "clear" empties your primary calendar and only works on "primary".
        calendar_id (str): The calendar to act on, for example "primary" or the address of a secondary calendar.
        summary (Optional[str]): New name. Update only.
        description (Optional[str]): New description. Update only.
        location (Optional[str]): New location. Update only.
        timezone (Optional[str]): New IANA time zone, for example "Europe/Berlin". Update only.
        confirm (bool): Required as True for delete and clear.

    Returns:
        str: The calendar as it stands after the call.
    """
    logger.info(
        f"[manage_calendar] Email: '{user_google_email}', Action: '{action}', "
        f"Calendar: '{calendar_id}'"
    )

    if action == "get":
        calendar = await asyncio.to_thread(
            service.calendars().get(calendarId=calendar_id).execute
        )
        return "\n".join(
            [f"Calendar {calendar.get('id', calendar_id)}:"]
            + _calendar_fields(calendar)
        )

    if action == "update":
        patch_body = {
            key: value
            for key, value in (
                ("summary", summary),
                ("description", description),
                ("location", location),
                ("timeZone", timezone),
            )
            if value is not None
        }
        if not patch_body:
            raise Exception(
                "Pass at least one of summary, description, location or timezone."
            )
        # patch, not update: update would replace the whole calendar object and
        # wipe every field left out of the body.
        calendar = await asyncio.to_thread(
            service.calendars().patch(calendarId=calendar_id, body=patch_body).execute
        )
        return "\n".join(
            [f"Calendar updated: {calendar.get('id', calendar_id)}"]
            + _calendar_fields(calendar)
        )

    if action == "clear":
        if calendar_id != "primary":
            raise Exception(
                "clear only works on 'primary'. To empty a secondary calendar, "
                "delete it instead."
            )
        if not confirm:
            raise Exception(
                "clear deletes every event on your primary calendar for good. "
                "Call again with confirm=True."
            )
        await asyncio.to_thread(service.calendars().clear(calendarId="primary").execute)
        return "Primary calendar cleared. Every event on it is gone."

    # action == "delete"
    calendar = await asyncio.to_thread(
        service.calendars().get(calendarId=calendar_id).execute
    )
    if not confirm:
        raise Exception(
            f"Deleting '{calendar.get('summary', calendar_id)}' removes the calendar "
            "and every event on it for good. Call again with confirm=True. To stop "
            "seeing a calendar you do not own, unsubscribe with "
            "manage_calendar_subscription instead."
        )
    await asyncio.to_thread(service.calendars().delete(calendarId=calendar_id).execute)
    return f"Calendar deleted: {calendar.get('summary', calendar_id)} ({calendar_id})"


@server.tool(title="Manage Calendar Access", annotations=_WRITE)
@handle_http_errors("manage_calendar_access", service_type="calendar")
@require_google_service("calendar", "calendar")
async def manage_calendar_access(
    service,
    user_google_email: str,
    action: Literal["list", "grant", "revoke"],
    calendar_id: str = "primary",
    scope_type: Literal["user", "group", "domain", "default"] = "user",
    scope_value: Optional[str] = None,
    role: Literal["none", "freeBusyReader", "reader", "writer", "owner"] = "reader",
    rule_id: Optional[str] = None,
    send_notifications: bool = True,
) -> str:
    """
    Lists who a calendar is shared with, shares it, or takes access away.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (Literal["list", "grant", "revoke"]): What to do.
        calendar_id (str): The calendar to act on. Defaults to "primary".
        scope_type (Literal["user", "group", "domain", "default"]): Who the rule is for. "default" means every person on the internet.
        scope_value (Optional[str]): The address or domain. Required unless scope_type is "default".
        role (Literal["none", "freeBusyReader", "reader", "writer", "owner"]): What they may do. "freeBusyReader" shows busy blocks only. "owner" can delete the calendar and change sharing.
        rule_id (Optional[str]): The rule to revoke. Take it from the list output. Built as "user:someone@example.com" if you omit it.
        send_notifications (bool): Whether Google mails the person about a grant. Defaults to True.

    Returns:
        str: The sharing rules, or the rule that changed.
    """
    logger.info(
        f"[manage_calendar_access] Email: '{user_google_email}', Action: '{action}', "
        f"Calendar: '{calendar_id}'"
    )

    if action == "list":
        rules = await _all_pages(
            lambda token: service.acl().list(calendarId=calendar_id, pageToken=token)
        )
        if not rules:
            return f"Calendar {calendar_id} is not shared with anyone."
        public = [r for r in rules if r.get("scope", {}).get("type") == "default"]
        lines = [f"{len(rules)} sharing rules on {calendar_id}:", ""]
        lines += [_acl_line(rule) for rule in rules]
        if public:
            lines += [
                "",
                "⚠ This calendar is public. Anyone on the internet can see it.",
            ]
        return "\n".join(lines)

    if action == "grant":
        if scope_type != "default" and not scope_value:
            raise Exception(f"scope_value is required for scope_type '{scope_type}'.")
        scope: Dict[str, str] = {"type": scope_type}
        if scope_value:
            scope["value"] = scope_value
        rule = await asyncio.to_thread(
            service.acl()
            .insert(
                calendarId=calendar_id,
                body={"scope": scope, "role": role},
                sendNotifications=send_notifications,
            )
            .execute
        )
        lines = [f"Access granted on {calendar_id}:", _acl_line(rule)]
        if scope_type == "default":
            lines += ["", "⚠ This calendar is now public to the whole internet."]
        elif scope_type == "domain":
            lines += ["", f"⚠ Everyone at {scope_value} can now see this calendar."]
        if role == "owner":
            lines += [
                "",
                "⚠ An owner can delete this calendar and change who else can see it.",
            ]
        return "\n".join(lines)

    # action == "revoke"
    target = rule_id or (f"{scope_type}:{scope_value}" if scope_value else scope_type)
    await asyncio.to_thread(
        service.acl().delete(calendarId=calendar_id, ruleId=target).execute
    )
    return f"Access removed from {calendar_id}: {target}"


@server.tool(title="Manage Calendar Subscription", annotations=_WRITE)
@handle_http_errors("manage_calendar_subscription", service_type="calendar")
@require_google_service("calendar", "calendar")
async def manage_calendar_subscription(
    service,
    user_google_email: str,
    action: Literal["list", "subscribe", "unsubscribe", "update"],
    calendar_id: Optional[str] = None,
    summary_override: Optional[str] = None,
    color_id: Optional[str] = None,
    hidden: Optional[bool] = None,
    selected: Optional[bool] = None,
) -> str:
    """
    Controls your own calendar list: which calendars you follow, what you call
    them, their color, and whether they show in the grid. It never changes the
    calendar itself, so unsubscribing is always safe and always reversible.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (Literal["list", "subscribe", "unsubscribe", "update"]): What to do.
        calendar_id (str): The calendar to follow, drop, or restyle. Required for everything except "list".
        summary_override (Optional[str]): Your own name for the calendar. Update only.
        color_id (Optional[str]): Calendar color id, "1" to "24". Update only.
        hidden (Optional[bool]): Hide it from the calendar list. Update only.
        selected (Optional[bool]): Show its events in the grid. Update only.

    Returns:
        str: Your calendar list, or the entry that changed.
    """
    logger.info(
        f"[manage_calendar_subscription] Email: '{user_google_email}', "
        f"Action: '{action}', Calendar: '{calendar_id}'"
    )

    if action == "list":
        entries = await _all_pages(
            lambda token: service.calendarList().list(showHidden=True, pageToken=token)
        )
        lines = [f"{len(entries)} calendars in your list:", ""]
        for entry in entries:
            lines.append(f"  • {entry.get('summary', '(no name)')} ({entry['id']})")
            lines += _calendar_fields(entry)
        return "\n".join(lines)

    if not calendar_id:
        raise Exception(f"calendar_id is required for the '{action}' action.")

    if action == "subscribe":
        entry = await asyncio.to_thread(
            service.calendarList().insert(body={"id": calendar_id}).execute
        )
        return "\n".join(
            [f"Subscribed to {entry.get('summary', calendar_id)}:"]
            + _calendar_fields(entry)
        )

    if action == "unsubscribe":
        # calendarList.delete only drops it from your list. The calendar and its
        # events stay where they are, and you can subscribe again any time.
        await asyncio.to_thread(
            service.calendarList().delete(calendarId=calendar_id).execute
        )
        return (
            f"Removed {calendar_id} from your calendar list. The calendar itself "
            "and its events are untouched; subscribe again to get it back."
        )

    # action == "update"
    patch_body = {
        key: value
        for key, value in (
            ("summaryOverride", summary_override),
            ("colorId", color_id),
            ("hidden", hidden),
            ("selected", selected),
        )
        if value is not None
    }
    if not patch_body:
        raise Exception(
            "Pass at least one of summary_override, color_id, hidden or selected."
        )
    entry = await asyncio.to_thread(
        service.calendarList().patch(calendarId=calendar_id, body=patch_body).execute
    )
    return "\n".join(
        [f"Calendar list entry updated: {entry.get('summary', calendar_id)}"]
        + _calendar_fields(entry)
    )


EXTENDED_CALENDAR_TOOL_NAMES = [
    "get_calendar_settings",
    "manage_calendar",
    "manage_calendar_access",
    "manage_calendar_subscription",
]
