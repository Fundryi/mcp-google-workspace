"""
Drive tools added by this fork.

Kept in a separate module so upstream's drive_tools.py stays mergeable, the
same way gmail/gmail_extended_tools.py works.

House rule, carried over from Gmail: no permanent delete. Trash and untrash
cover the need and can be undone.
"""

import asyncio
import logging
from typing import Any, Dict, List, Literal, Optional

from mcp.types import ToolAnnotations

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import StringList, handle_http_errors

logger = logging.getLogger(__name__)

_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)

_TRASH_FIELDS = "id, name, mimeType, trashedTime, owners(emailAddress), size"


async def _drive_files_matching(
    service, query: str, limit: int
) -> List[Dict[str, Any]]:
    """Pages through files.list for a raw Drive query."""
    files: List[Dict[str, Any]] = []
    page_token = None
    while len(files) < limit:
        response = await asyncio.to_thread(
            service.files()
            .list(
                q=query,
                pageSize=min(100, limit - len(files)),
                pageToken=page_token,
                fields=f"nextPageToken, files({_TRASH_FIELDS})",
                # corpora is needed as well: with the default 'user', shared
                # drive items are left out even with includeItemsFromAllDrives.
                corpora="allDrives",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute
        )
        files += response.get("files", [])
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return files[:limit]


def _format_drive_file(file: Dict[str, Any]) -> str:
    """One line per file: name, id, and whatever else the API gave us."""
    owner = (file.get("owners") or [{}])[0].get("emailAddress")
    extras = [
        part
        for part in (
            file.get("mimeType"),
            f"owner: {owner}" if owner else None,
            f"trashed: {file['trashedTime']}" if file.get("trashedTime") else None,
        )
        if part
    ]
    return f"  • {file.get('name', '(no name)')} (ID: {file['id']})" + (
        f"\n    {' | '.join(extras)}" if extras else ""
    )


@server.tool(title="Manage Drive Trash", annotations=_WRITE)
@handle_http_errors("manage_drive_trash", service_type="drive")
@require_google_service("drive", "drive_file")
async def manage_drive_trash(
    service,
    user_google_email: str,
    action: Literal["list", "trash", "restore"] = "list",
    file_ids: Optional[StringList] = None,
    query: Optional[str] = None,
    dry_run: bool = True,
    max_files: int = 200,
) -> str:
    """
    Lists the Drive trash, moves files to it, or restores files from it.
    Files in the trash keep their ID, sharing and comments, and Drive empties
    the trash by itself after 30 days.

    Nothing is ever deleted for good here, and nothing is moved until dry_run
    is set to False.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (Literal["list", "trash", "restore"]): What to do. "list" shows what is in the trash.
        file_ids (Optional[List[str]]): The files to trash or restore. Use this or query.
        query (Optional[str]): A Drive query picking the files instead, for example "name contains 'draft' and modifiedTime < '2024-01-01'". Restore searches inside the trash on its own.
        dry_run (bool): True (default) lists what would move and changes nothing.
        max_files (int): Safety cap on how many files are touched.

    Returns:
        str: The files listed, or the ones moved.
    """
    logger.info(
        f"[manage_drive_trash] Email: '{user_google_email}', Action: '{action}', "
        f"Dry run: {dry_run}"
    )

    if action == "list":
        files = await _drive_files_matching(service, "trashed = true", max_files)
        if not files:
            return "The trash is empty."
        return "\n".join(
            [f"{len(files)} files in the trash:", ""]
            + [_format_drive_file(file) for file in files]
        )

    if not file_ids and not query:
        raise Exception("Provide either file_ids or query.")
    if file_ids and query:
        raise Exception("Provide file_ids or query, not both.")

    trashed = action == "trash"
    if query:
        # A restore only makes sense inside the trash, and trashing only makes
        # sense outside it, so the state clause is ours, not the caller's.
        scoped_query = f"({query}) and trashed = {str(not trashed).lower()}"
        files = await _drive_files_matching(service, scoped_query, max_files)
    else:
        files = []
        for file_id in file_ids[:max_files]:
            files.append(
                await asyncio.to_thread(
                    service.files()
                    .get(fileId=file_id, fields=_TRASH_FIELDS, supportsAllDrives=True)
                    .execute
                )
            )

    header = [
        f"Action: {action}",
        f"Query: {query}" if query else f"Files: {len(files)}",
        f"Matches: {len(files)}"
        + (f" (capped at {max_files})" if len(files) == max_files else ""),
    ]
    if not files:
        return "\n".join(header + ["", "Nothing to do."])

    if dry_run:
        return "\n".join(
            header
            + ["", "Dry run, nothing moved:"]
            + [_format_drive_file(file) for file in files[:20]]
            + (["  ..."] if len(files) > 20 else [])
            + ["", "Set dry_run to False to apply this."]
        )

    # One at a time on purpose. The service holds a single httplib2 connection
    # and it is not thread safe, so a parallel fan-out here fails with an SSL
    # error or a read timeout. A file the user cannot trash must not hide what
    # happened to the rest either, so each failure is caught and reported.
    moved: List[Dict[str, Any]] = []
    failed: List[Any] = []
    for file in files:
        try:
            await asyncio.to_thread(
                service.files()
                .update(
                    fileId=file["id"],
                    body={"trashed": trashed},
                    supportsAllDrives=True,
                )
                .execute
            )
            moved.append(file)
        except Exception as exc:
            failed.append((file, exc))
    verb = "moved to the trash" if trashed else "restored from the trash"
    lines = (
        header
        + ["", f"{len(moved)} files {verb}:"]
        + [_format_drive_file(file) for file in moved[:20]]
        + (["  ..."] if len(moved) > 20 else [])
    )
    if failed:
        lines += ["", f"{len(failed)} files could not be moved:"]
        lines += [
            f"  \u2022 {file.get('name', file['id'])}: {error}"
            for file, error in failed[:20]
        ]
    return "\n".join(lines)


_READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)


@server.tool(title="List Drive Changes", annotations=_READ)
@handle_http_errors("list_drive_changes", is_read_only=True, service_type="drive")
@require_google_service("drive", "drive_read")
async def list_drive_changes(
    service,
    user_google_email: str,
    page_token: Optional[str] = None,
    include_removed: bool = True,
    include_shared_drives: bool = True,
    max_changes: int = 200,
) -> str:
    """
    Lists what changed in Drive since a page token: files added, edited, moved,
    trashed, removed, or shared with you. This is the cheap way to catch up
    instead of listing everything again.

    Call it once with no page_token to get a starting token and nothing else.
    Keep the token it hands back and pass that in next time.

    Args:
        user_google_email (str): The user's Google email address. Required.
        page_token (Optional[str]): Report changes after this token. Omit to just fetch a fresh starting token.
        include_removed (bool): Include files deleted or no longer shared with you. Defaults to True.
        include_shared_drives (bool): Include shared drive items as well as My Drive. Defaults to True.
        max_changes (int): Safety cap on how many change records to read.

    Returns:
        str: One line per change, plus the token to pass in next time.
    """
    logger.info(
        f"[list_drive_changes] Email: '{user_google_email}', Token: '{page_token}'"
    )

    if not page_token:
        start = await asyncio.to_thread(
            service.changes()
            .getStartPageToken(supportsAllDrives=include_shared_drives)
            .execute
        )
        token = start.get("startPageToken")
        return (
            "No page_token given, so nothing was compared.\n"
            f"Starting page_token: {token}\n"
            "Keep it and pass it as page_token next time to see what changed since now."
        )

    drive_flags = (
        {
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
            "spaces": "drive",
        }
        if include_shared_drives
        else {}
    )
    changes: List[Dict[str, Any]] = []
    next_token = page_token
    new_start_token = None
    while len(changes) < max_changes:
        response = await asyncio.to_thread(
            service.changes()
            .list(
                pageToken=next_token,
                pageSize=min(1000, max_changes - len(changes)),
                includeRemoved=include_removed,
                fields=(
                    "nextPageToken, newStartPageToken, changes(changeType, time, "
                    "removed, fileId, driveId, file(id, name, mimeType, trashed, "
                    "modifiedTime, owners(emailAddress)))"
                ),
                **drive_flags,
            )
            .execute
        )
        changes += response.get("changes", [])
        new_start_token = response.get("newStartPageToken") or new_start_token
        next_token = response.get("nextPageToken")
        if not next_token:
            break

    lines = [
        f"Changes since token {page_token}: {len(changes)}",
        f"Next page_token: {new_start_token or next_token}",
    ]
    if not changes:
        return "\n".join(lines + ["", "Nothing changed."])

    lines.append("")
    for change in changes:
        file = change.get("file") or {}
        name = file.get("name") or change.get("fileId", "(unknown)")
        if change.get("removed"):
            state = "removed, or you lost access"
        elif file.get("trashed"):
            state = "moved to the trash"
        else:
            state = "added or edited"
        lines.append(f"  • {name}: {state}")
        details = [
            part
            for part in (
                f"id: {change.get('fileId')}",
                f"at {change['time']}" if change.get("time") else None,
                f"shared drive: {change['driveId']}" if change.get("driveId") else None,
            )
            if part
        ]
        lines.append(f"    {' | '.join(details)}")
    return "\n".join(lines)


@server.tool(title="List Drive File Revisions", annotations=_READ)
@handle_http_errors(
    "list_drive_file_revisions", is_read_only=True, service_type="drive"
)
@require_google_service("drive", "drive_read")
async def list_drive_file_revisions(
    service,
    user_google_email: str,
    file_id: str,
    max_revisions: int = 100,
) -> str:
    """
    Lists the saved versions of a Drive file: when each was kept, who wrote it,
    and how big it was. Google Docs, Sheets and Slides keep versions this way;
    uploaded files keep them only when the file was replaced.

    Args:
        user_google_email (str): The user's Google email address. Required.
        file_id (str): The file to look at.
        max_revisions (int): Safety cap on how many versions to list.

    Returns:
        str: One line per version, newest last.
    """
    logger.info(
        f"[list_drive_file_revisions] Email: '{user_google_email}', File: '{file_id}'"
    )

    revisions: List[Dict[str, Any]] = []
    page_token = None
    while len(revisions) < max_revisions:
        response = await asyncio.to_thread(
            service.revisions()
            .list(
                fileId=file_id,
                pageSize=min(1000, max_revisions - len(revisions)),
                pageToken=page_token,
                fields=(
                    "nextPageToken, revisions(id, modifiedTime, size, "
                    "keepForever, lastModifyingUser(displayName, emailAddress))"
                ),
            )
            .execute
        )
        revisions += response.get("revisions", [])
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    if not revisions:
        return f"No saved versions for file {file_id}."

    lines = [f"{len(revisions)} saved versions of file {file_id}:", ""]
    for revision in revisions[:max_revisions]:
        author = (revision.get("lastModifyingUser") or {}).get("displayName")
        details = [
            part
            for part in (
                revision.get("modifiedTime"),
                f"by {author}" if author else None,
                f"{revision['size']} bytes" if revision.get("size") else None,
                "kept forever" if revision.get("keepForever") else None,
            )
            if part
        ]
        lines.append(f"  • revision {revision['id']}: {' | '.join(details)}")
    return "\n".join(lines)


def _activity_actor(actor: Dict[str, Any]) -> str:
    """Names who did something, as far as the API will say."""
    if "user" in actor:
        user = actor["user"]
        if "knownUser" in user:
            person = user["knownUser"]
            return (
                "you"
                if person.get("isCurrentUser")
                else person.get("personName", "someone")
            )
        if "deletedUser" in user:
            return "a deleted user"
        return "an unknown user"
    for key, name in (
        ("anonymous", "an anonymous user"),
        ("impersonation", "an admin acting as someone"),
        ("system", "the system"),
        ("administrator", "an admin"),
    ):
        if key in actor:
            return name
    return "someone"


def _activity_what(detail: Dict[str, Any]) -> str:
    """Turns one action detail object into a few readable words."""
    for key, words in (
        ("create", "created"),
        ("edit", "edited"),
        ("move", "moved"),
        ("rename", "renamed"),
        ("delete", "trashed or deleted"),
        ("restore", "restored"),
        ("permissionChange", "changed sharing"),
        ("comment", "commented"),
        ("dlpChange", "changed a data loss prevention state"),
        ("reference", "referenced"),
        ("settingsChange", "changed settings"),
        ("appliedLabelChange", "changed a label"),
    ):
        if key in detail:
            return words
    return "did something"


@server.tool(title="Query Drive Activity", annotations=_READ)
@handle_http_errors("query_drive_activity", is_read_only=True, service_type="drive")
@require_google_service("driveactivity", "drive_activity_read")
async def query_drive_activity(
    service,
    user_google_email: str,
    file_id: Optional[str] = None,
    folder_id: Optional[str] = None,
    filter_expression: Optional[str] = None,
    max_activities: int = 100,
) -> str:
    """
    Answers who changed, moved, shared, deleted or commented on Drive content,
    and when. This is the audit trail the plain Drive tools cannot show.

    Needs the Drive Activity API turned on in the Cloud project as well as the
    drive.activity.readonly scope. Without the API enabled every call is a 403.

    Args:
        user_google_email (str): The user's Google email address. Required.
        file_id (str): Ask about one file. Use this or folder_id, not both.
        folder_id (str): Ask about everything under one folder.
        filter_expression (str): Extra filter, for example 'time > "2026-01-01T00:00:00Z"' or 'detail.action_detail_case: SHARE'.
        max_activities (int): Safety cap on how many records to read.

    Returns:
        str: One line per activity, newest first.
    """
    logger.info(
        f"[query_drive_activity] Email: '{user_google_email}', File: '{file_id}', "
        f"Folder: '{folder_id}'"
    )
    if file_id and folder_id:
        raise Exception("Ask about a file or a folder, not both.")

    body: Dict[str, Any] = {"pageSize": min(500, max_activities)}
    if file_id:
        body["itemName"] = f"items/{file_id}"
    if folder_id:
        body["ancestorName"] = f"items/{folder_id}"
    if filter_expression:
        body["filter"] = filter_expression

    activities: List[Dict[str, Any]] = []
    while len(activities) < max_activities:
        response = await asyncio.to_thread(service.activity().query(body=body).execute)
        activities += response.get("activities", [])
        page_token = response.get("nextPageToken")
        if not page_token:
            break
        body["pageToken"] = page_token

    if not activities:
        return "No Drive activity matched."

    lines = [f"{len(activities)} Drive activities:", ""]
    for activity in activities[:max_activities]:
        actors = ", ".join(_activity_actor(a) for a in activity.get("actors", []))
        what = _activity_what(activity.get("primaryActionDetail", {}))
        targets = ", ".join(
            (target.get("driveItem") or target.get("drive") or {}).get("title")
            or (target.get("driveItem") or {}).get("name")
            or "(unnamed)"
            for target in activity.get("targets", [])
        )
        when = activity.get("timestamp") or (activity.get("timeRange") or {}).get(
            "endTime"
        )
        lines.append(f"  • {actors or 'someone'} {what}: {targets}")
        if when:
            lines.append(f"    {when}")
    return "\n".join(lines)


EXTENDED_DRIVE_TOOL_NAMES = [
    "manage_drive_trash",
    "list_drive_changes",
    "list_drive_file_revisions",
    "query_drive_activity",
]
