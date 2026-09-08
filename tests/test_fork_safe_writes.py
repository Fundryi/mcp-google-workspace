"""Guards for the fork's safe-write rules.

Every case here is a write that used to change more than the caller asked for,
or one that hides how much it destroys. One test per rule.
"""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import gcalendar.calendar_tools as cal  # noqa: E402
import gdrive.drive_extended_tools as drive_ext  # noqa: E402
import gchat.chat_extended_tools as chat_ext  # noqa: E402
import gcontacts.contacts_extended_tools as contacts_ext  # noqa: E402
import gmail.gmail_extended_tools as gmail_ext  # noqa: E402
import gmail.gmail_tools as gmail  # noqa: E402
import gtasks.tasks_tools as tasks  # noqa: E402
from gcalendar import calendar_extended_tools as cal_ext  # noqa: E402
from gdrive.drive_helpers import public_share_warning  # noqa: E402


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


# --- Gmail: labels ---------------------------------------------------------


@pytest.mark.asyncio
async def test_label_update_rejects_a_color_gmail_would_refuse():
    with pytest.raises(Exception, match="palette"):
        await _unwrap(gmail.manage_gmail_label)(
            service=Mock(),
            user_google_email="u@example.com",
            action="update",
            label_id="Label_1",
            background_color="#123456",
            text_color="#ffffff",
        )


@pytest.mark.asyncio
async def test_label_delete_reports_what_it_took_with_it():
    service = Mock()
    service.users().labels().get().execute.return_value = {
        "id": "Label_1",
        "name": "Work",
        "messagesTotal": 42,
    }
    result = await _unwrap(gmail.manage_gmail_label)(
        service=service,
        user_google_email="u@example.com",
        action="delete",
        label_id="Label_1",
    )
    assert "42 messages" in result


@pytest.mark.asyncio
async def test_label_delete_states_the_count_even_when_it_is_zero():
    # An absent line is not an answer. A caller checking whether a delete was
    # safe should read the number, not infer it.
    service = Mock()
    service.users().labels().get().execute.return_value = {
        "id": "Label_9",
        "name": "zz-probe",
    }
    result = await _unwrap(gmail.manage_gmail_label)(
        service=service,
        user_google_email="u@example.com",
        action="delete",
        label_id="Label_9",
    )
    assert "0 messages" in result


# --- Gmail: filters --------------------------------------------------------


def test_filter_surprises_names_what_gmail_added():
    notes = gmail._filter_action_surprises(
        {"removeLabelIds": ["INBOX"]}, {"removeLabelIds": ["INBOX", "SPAM"]}
    )
    assert notes and "SPAM" in notes[0]
    assert (
        gmail._filter_action_surprises(
            {"removeLabelIds": ["INBOX"]}, {"removeLabelIds": ["INBOX"]}
        )
        == []
    )


@pytest.mark.asyncio
async def test_filter_replace_creates_before_it_deletes():
    service = Mock()
    calls = []
    service.users().settings().filters().create().execute.side_effect = lambda *a, **k: (
        calls.append("create") or {"id": "new", "criteria": {}, "action": {}}
    )
    service.users().settings().filters().delete().execute.side_effect = lambda *a, **k: (
        calls.append("delete")
    )
    result = await _unwrap(gmail.manage_gmail_filter)(
        service=service,
        user_google_email="u@example.com",
        action="replace",
        filter_id="old",
        criteria={"from": "a@b.com"},
        filter_action={"addLabelIds": ["Label_1"]},
    )
    assert calls == ["create", "delete"]
    assert "old" in result


# --- Calendar --------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_a_repeating_event_needs_confirmation():
    service = Mock()
    service.events().get().execute.return_value = {
        "summary": "Standup",
        "recurrence": ["RRULE:FREQ=DAILY"],
    }
    with pytest.raises(Exception, match="every instance"):
        await _unwrap(cal._delete_event_impl)(
            service=service, user_google_email="u@example.com", event_id="e1"
        )
    service.events().delete.assert_not_called()


@pytest.mark.asyncio
async def test_deleting_a_single_event_still_just_works():
    service = Mock()
    service.events().get().execute.return_value = {
        "summary": "One off",
        "start": {"dateTime": "2026-09-01T10:00:00Z"},
    }
    result = await _unwrap(cal._delete_event_impl)(
        service=service, user_google_email="u@example.com", event_id="e1"
    )
    assert "One off" in result


@pytest.mark.asyncio
async def test_calendar_update_sends_only_what_was_passed():
    service = Mock()
    service.calendars().patch().execute.return_value = {"id": "c1", "summary": "New"}
    await _unwrap(cal_ext.manage_calendar)(
        service=service,
        user_google_email="u@example.com",
        action="update",
        calendar_id="c1",
        summary="New",
    )
    body = service.calendars().patch.call_args.kwargs["body"]
    assert body == {"summary": "New"}
    service.calendars().update.assert_not_called()


@pytest.mark.asyncio
async def test_calendar_delete_needs_confirmation():
    service = Mock()
    service.calendars().get().execute.return_value = {"summary": "Team"}
    with pytest.raises(Exception, match="confirm=True"):
        await _unwrap(cal_ext.manage_calendar)(
            service=service,
            user_google_email="u@example.com",
            action="delete",
            calendar_id="c1",
        )
    service.calendars().delete.assert_not_called()


@pytest.mark.asyncio
async def test_public_calendar_share_says_so():
    service = Mock()
    service.acl().insert().execute.return_value = {
        "id": "default",
        "scope": {"type": "default"},
        "role": "reader",
    }
    result = await _unwrap(cal_ext.manage_calendar_access)(
        service=service,
        user_google_email="u@example.com",
        action="grant",
        scope_type="default",
    )
    assert "public" in result


# --- Tasks -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_a_task_list_with_tasks_needs_confirmation():
    service = Mock()
    service.tasks().list().execute.return_value = {
        "items": [{"id": "t1"}, {"id": "t2"}]
    }
    with pytest.raises(Exception, match="2 tasks"):
        await _unwrap(tasks._delete_task_list_impl)(
            service=service, user_google_email="u@example.com", task_list_id="l1"
        )
    service.tasklists().delete.assert_not_called()


@pytest.mark.asyncio
async def test_deleting_an_empty_task_list_just_works():
    service = Mock()
    service.tasks().list().execute.return_value = {"items": []}
    result = await _unwrap(tasks._delete_task_list_impl)(
        service=service, user_google_email="u@example.com", task_list_id="l1"
    )
    assert "l1" in result


# --- Drive -----------------------------------------------------------------


def test_public_share_warning_only_fires_for_open_shares():
    assert public_share_warning("user") == []
    assert "anyone with the link" in " ".join(public_share_warning("anyone"))
    assert "search results" in " ".join(public_share_warning("anyone", True))


@pytest.mark.asyncio
async def test_drive_trash_dry_run_changes_nothing():
    service = Mock()
    service.files().list().execute.return_value = {
        "files": [{"id": "f1", "name": "Old draft"}]
    }
    result = await _unwrap(drive_ext.manage_drive_trash)(
        service=service,
        user_google_email="u@example.com",
        action="trash",
        query="name contains 'draft'",
    )
    service.files().update.assert_not_called()
    assert "Old draft" in result
    assert "Dry run" in result


@pytest.mark.asyncio
async def test_drive_trash_applies_and_never_deletes():
    service = Mock()
    service.files().list().execute.return_value = {
        "files": [{"id": "f1", "name": "Old draft"}]
    }
    await _unwrap(drive_ext.manage_drive_trash)(
        service=service,
        user_google_email="u@example.com",
        action="trash",
        query="name contains 'draft'",
        dry_run=False,
    )
    assert service.files().update.call_args.kwargs["body"] == {"trashed": True}
    service.files().delete.assert_not_called()


@pytest.mark.asyncio
async def test_drive_restore_searches_inside_the_trash():
    service = Mock()
    service.files().list().execute.return_value = {"files": []}
    await _unwrap(drive_ext.manage_drive_trash)(
        service=service,
        user_google_email="u@example.com",
        action="restore",
        query="name = 'x'",
    )
    assert "trashed = true" in service.files().list.call_args.kwargs["q"]


@pytest.mark.asyncio
async def test_drive_trash_caps_explicit_file_ids_too():
    service = Mock()
    service.files().get().execute.return_value = {"id": "f1", "name": "One"}
    await _unwrap(drive_ext.manage_drive_trash)(
        service=service,
        user_google_email="u@example.com",
        action="trash",
        file_ids=["f1", "f2", "f3"],
        max_files=1,
        dry_run=False,
    )
    assert service.files().update.call_count == 1


# --- the read-only catch-up tools ------------------------------------------


@pytest.mark.asyncio
async def test_drive_changes_without_a_token_only_hands_one_back():
    service = Mock()
    service.changes().getStartPageToken().execute.return_value = {
        "startPageToken": "12345"
    }
    result = await _unwrap(drive_ext.list_drive_changes)(
        service=service, user_google_email="u@example.com"
    )
    service.changes().list.assert_not_called()
    assert "12345" in result


@pytest.mark.asyncio
async def test_drive_changes_reports_removals_and_the_next_token():
    service = Mock()
    service.changes().list().execute.return_value = {
        "newStartPageToken": "999",
        "changes": [
            {"fileId": "f1", "removed": True},
            {"fileId": "f2", "file": {"name": "Notes", "trashed": True}},
        ],
    }
    result = await _unwrap(drive_ext.list_drive_changes)(
        service=service, user_google_email="u@example.com", page_token="1"
    )
    assert "lost access" in result
    assert "moved to the trash" in result
    assert "Next page_token: 999" in result


@pytest.mark.asyncio
async def test_gmail_history_hands_back_the_next_history_id():
    service = Mock()
    service.users().history().list().execute.return_value = {
        "historyId": "777",
        "history": [{"labelsAdded": [{"message": {"id": "m1"}, "labelIds": ["L1"]}]}],
    }
    service.users().labels().list().execute.return_value = {
        "labels": [{"id": "L1", "name": "Work"}]
    }
    result = await _unwrap(gmail_ext.list_gmail_history)(
        service=service, user_google_email="u@example.com", start_history_id="1"
    )
    assert "Next start_history_id: 777" in result
    assert "Work" in result


@pytest.mark.asyncio
async def test_calendar_settings_lists_every_page():
    service = Mock()
    pages = [
        {
            "items": [{"id": "timezone", "value": "Europe/Berlin"}],
            "nextPageToken": "p2",
        },
        {"items": [{"id": "locale", "value": "en"}]},
    ]
    service.settings().list().execute.side_effect = pages
    result = await _unwrap(cal_ext.get_calendar_settings)(
        service=service, user_google_email="u@example.com"
    )
    assert "Europe/Berlin" in result and "locale" in result


# --- the tools that needed a new scope -------------------------------------


def test_new_scopes_are_requested_and_resolvable():
    from auth.scopes import (
        CHAT_SCOPES,
        CONTACTS_SCOPES,
        DRIVE_SCOPES,
        CHAT_MEMBERSHIPS_READONLY_SCOPE,
        CONTACTS_OTHER_READONLY_SCOPE,
        DRIVE_ACTIVITY_READONLY_SCOPE,
    )
    from auth.service_decorator import SCOPE_GROUPS, SERVICE_CONFIGS

    assert DRIVE_ACTIVITY_READONLY_SCOPE in DRIVE_SCOPES
    assert CHAT_MEMBERSHIPS_READONLY_SCOPE in CHAT_SCOPES
    assert CONTACTS_OTHER_READONLY_SCOPE in CONTACTS_SCOPES
    assert SCOPE_GROUPS["drive_activity_read"] == DRIVE_ACTIVITY_READONLY_SCOPE
    assert SCOPE_GROUPS["chat_memberships_read"] == CHAT_MEMBERSHIPS_READONLY_SCOPE
    assert SCOPE_GROUPS["contacts_other_read"] == CONTACTS_OTHER_READONLY_SCOPE
    # Drive Activity is its own API, not a corner of drive v3.
    assert SERVICE_CONFIGS["driveactivity"] == {
        "service": "driveactivity",
        "version": "v2",
    }


@pytest.mark.asyncio
async def test_chat_tools_refuse_a_private_account_before_calling_google():
    service = Mock()
    for tool, kwargs in (
        (chat_ext.list_chat_members, {"space_name": "spaces/A"}),
        (
            chat_ext.update_chat_message,
            {"message_name": "spaces/A/messages/B", "text": "hi"},
        ),
    ):
        with pytest.raises(Exception, match="Workspace"):
            await _unwrap(tool)(
                service=service, user_google_email="someone@gmail.com", **kwargs
            )
    service.spaces().members().list.assert_not_called()
    service.spaces().messages().patch.assert_not_called()


@pytest.mark.asyncio
async def test_chat_message_update_only_touches_the_text():
    service = Mock()
    service.spaces().messages().patch().execute.return_value = {
        "name": "spaces/A/messages/B",
        "text": "fixed",
    }
    await _unwrap(chat_ext.update_chat_message)(
        service=service,
        user_google_email="me@company.com",
        message_name="spaces/A/messages/B",
        text="fixed",
    )
    kwargs = service.spaces().messages().patch.call_args.kwargs
    assert kwargs["updateMask"] == "text"  # without it Chat clears the other fields
    assert kwargs["body"] == {"text": "fixed"}


@pytest.mark.asyncio
async def test_other_contacts_search_warms_up_first():
    service = Mock()
    service.otherContacts().search().execute.side_effect = [
        {},
        {"results": [{"person": {"emailAddresses": [{"value": "a@b.com"}]}}]},
    ]
    result = await _unwrap(contacts_ext.search_other_contacts)(
        service=service, user_google_email="u@example.com", query="a"
    )
    queries = [
        call.kwargs.get("query")
        for call in service.otherContacts().search.call_args_list
        if "query" in call.kwargs
    ]
    assert queries[0] == ""  # Google returns nothing on a cold first search
    assert "a@b.com" in result


@pytest.mark.asyncio
async def test_drive_activity_refuses_a_file_and_folder_at_once():
    with pytest.raises(Exception, match="not both"):
        await _unwrap(drive_ext.query_drive_activity)(
            service=Mock(),
            user_google_email="u@example.com",
            file_id="f1",
            folder_id="d1",
        )


@pytest.mark.asyncio
async def test_drive_activity_names_who_did_what():
    service = Mock()
    service.activity().query().execute.return_value = {
        "activities": [
            {
                "primaryActionDetail": {"permissionChange": {}},
                "actors": [{"user": {"knownUser": {"isCurrentUser": True}}}],
                "targets": [{"driveItem": {"title": "Budget"}}],
                "timestamp": "2026-08-01T10:00:00Z",
            }
        ]
    }
    result = await _unwrap(drive_ext.query_drive_activity)(
        service=service, user_google_email="u@example.com", file_id="f1"
    )
    assert "you changed sharing: Budget" in result
