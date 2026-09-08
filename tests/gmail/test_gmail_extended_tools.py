"""Tests for the fork's Gmail management tools (gmail/gmail_extended_tools.py)."""

import base64
import os
import re
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from auth.scopes import GMAIL_SCOPES, GMAIL_SETTINGS_SHARING_SCOPE
from core.server import server
from core.tool_registry import get_tool_components
import gmail.gmail_tools  # noqa: F401  registers upstream tools and ours
from gmail import gmail_extended_tools as ext


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _draft(subject="Re: Original topic", body="previous body"):
    return {
        "id": "d1",
        "message": {
            "id": "m1",
            "threadId": "t1",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "Subject", "value": subject},
                    {"name": "To", "value": "them@example.com"},
                    {"name": "In-Reply-To", "value": "<abc@mail.gmail.com>"},
                    {"name": "References", "value": "<abc@mail.gmail.com>"},
                ],
                "body": {"data": _b64(body)},
            },
        },
    }


def _sent_raw(service) -> str:
    body = service.users().drafts().update.call_args.kwargs["body"]
    raw = body["message"]["raw"]
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()


# --- registration ----------------------------------------------------------


def test_every_extended_tool_is_registered_exactly_once():
    # Tests run without a service account, so the 7 delegated tools are hidden.
    components = get_tool_components(server)
    visible = set(ext.EXTENDED_TOOL_NAMES) - set(ext.DELEGATED_TOOL_NAMES)
    for name in visible:
        assert name in components, name
    for name in ext.DELEGATED_TOOL_NAMES:
        assert name not in components, name
    assert len(set(ext.EXTENDED_TOOL_NAMES)) == len(ext.EXTENDED_TOOL_NAMES) == 41
    assert len(ext.DELEGATED_TOOL_NAMES) == 7


def test_every_extended_tool_is_in_tool_tiers():
    tiers = open(
        os.path.join(os.path.dirname(__file__), "../../core/tool_tiers.yaml"),
        encoding="utf-8",
    ).read()
    for name in ext.EXTENDED_TOOL_NAMES:
        assert f"- {name}\n" in tiers, name


def test_no_permanent_delete_anywhere():
    components = get_tool_components(server)
    for name in components:
        assert not re.search(r"(^|_)delete_gmail_(message|thread)", name), name
    assert "https://mail.google.com/" not in GMAIL_SCOPES
    scopes_src = open(
        os.path.join(os.path.dirname(__file__), "../../auth/scopes.py"),
        encoding="utf-8",
    ).read()
    assert "mail.google.com" not in scopes_src


def test_sharing_scope_only_with_service_account(monkeypatch):
    import importlib

    import auth.scopes as scopes

    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_KEY_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_KEY_JSON", raising=False)
    assert GMAIL_SETTINGS_SHARING_SCOPE not in importlib.reload(scopes).GMAIL_SCOPES
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_KEY_FILE", "/fake/key.json")
    assert GMAIL_SETTINGS_SHARING_SCOPE in importlib.reload(scopes).GMAIL_SCOPES
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_KEY_FILE")
    importlib.reload(scopes)


# --- trash -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_trash_message_calls_trash_not_delete():
    service = Mock()
    result = await _unwrap(ext.trash_gmail_message)(
        service=service, user_google_email="u@example.com", message_id="m1"
    )
    service.users().messages().trash.assert_called_once_with(userId="me", id="m1")
    service.users().messages().delete.assert_not_called()
    assert "m1" in result


@pytest.mark.asyncio
async def test_untrash_thread():
    service = Mock()
    await _unwrap(ext.untrash_gmail_thread)(
        service=service, user_google_email="u@example.com", thread_id="t1"
    )
    service.users().threads().untrash.assert_called_once_with(userId="me", id="t1")


# --- update_draft: subject precedence (the bug that kept it disabled upstream)


@pytest.mark.asyncio
async def test_update_draft_keeps_explicit_subject_in_thread():
    service = Mock()
    service.users().drafts().get().execute.return_value = _draft()
    service.users().drafts().update().execute.return_value = {"id": "d1"}

    await _unwrap(ext.update_gmail_draft)(
        service=service,
        user_google_email="u@example.com",
        draft_id="d1",
        subject="My own subject",
        body="hi",
    )

    text = _sent_raw(service)
    assert "Subject: My own subject" in text
    assert "Re: Original topic" not in text
    # threading survives
    assert "In-Reply-To: <abc@mail.gmail.com>" in text
    assert "References: <abc@mail.gmail.com>" in text
    assert (
        service.users().drafts().update.call_args.kwargs["body"]["message"]["threadId"]
        == "t1"
    )


@pytest.mark.asyncio
async def test_update_draft_copies_subject_and_recipients_when_not_given():
    service = Mock()
    service.users().drafts().get().execute.return_value = _draft()
    service.users().drafts().update().execute.return_value = {"id": "d1"}

    await _unwrap(ext.update_gmail_draft)(
        service=service, user_google_email="u@example.com", draft_id="d1", body="new"
    )

    text = _sent_raw(service)
    assert "Subject: Re: Original topic" in text
    assert "To: them@example.com" in text
    assert "new" in text


@pytest.mark.asyncio
async def test_update_draft_copies_body_when_not_given():
    service = Mock()
    service.users().drafts().get().execute.return_value = _draft(body="keep me")
    service.users().drafts().update().execute.return_value = {"id": "d1"}

    await _unwrap(ext.update_gmail_draft)(
        service=service, user_google_email="u@example.com", draft_id="d1", to="x@y.z"
    )

    text = _sent_raw(service)
    assert "keep me" in text
    assert "To: x@y.z" in text


# --- settings --------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_vacation_drops_unset_fields():
    service = Mock()
    service.users().settings().updateVacation().execute.return_value = {
        "enableAutoReply": True
    }
    await _unwrap(ext.update_gmail_vacation_settings)(
        service=service,
        user_google_email="u@example.com",
        enable_auto_reply=True,
        response_body_plain_text="Away",
    )
    service.users().settings().updateVacation.assert_called_with(
        userId="me", body={"enableAutoReply": True, "responseBodyPlainText": "Away"}
    )


@pytest.mark.asyncio
async def test_update_auto_forwarding_requires_address_when_enabling(monkeypatch):
    monkeypatch.setattr(ext, "is_delegated", lambda email: True)
    with pytest.raises(Exception, match="email_address"):
        await _unwrap(ext.update_gmail_auto_forwarding)(
            service=Mock(), user_google_email="u@example.com", enabled=True
        )


@pytest.mark.asyncio
async def test_update_send_as_uses_patch(monkeypatch):
    monkeypatch.setattr(ext, "is_delegated", lambda email: True)
    service = Mock()
    service.users().settings().sendAs().patch().execute.return_value = {}
    await _unwrap(ext.update_gmail_send_as)(
        service=service,
        user_google_email="u@example.com",
        send_as_email="alias@example.com",
        signature="<b>sig</b>",
    )
    service.users().settings().sendAs().patch.assert_called_with(
        userId="me", sendAsEmail="alias@example.com", body={"signature": "<b>sig</b>"}
    )


# --- no guessing which mailbox -------------------------------------------


def test_tool_refuses_to_guess_account(monkeypatch):
    import inspect

    import auth.service_decorator as sd

    monkeypatch.delenv("USER_GOOGLE_EMAIL", raising=False)
    monkeypatch.setattr(sd, "_ENV_USER_EMAIL", None)

    def sample(user_google_email: str, draft_id: str) -> str:
        return draft_id

    with pytest.raises(Exception, match="user_google_email"):
        sd._extract_oauth20_user_email(
            (), {"draft_id": "d1"}, inspect.signature(sample)
        )


# --- delegation gate --------------------------------------------------------


@pytest.mark.asyncio
async def test_delegated_tool_refuses_private_account_before_any_api_call(monkeypatch):
    monkeypatch.setattr(ext, "is_delegated", lambda email: False)
    service = Mock()
    gated = ext._delegated_only(_unwrap(ext.create_gmail_send_as))
    with pytest.raises(Exception, match="list_gmail_accounts"):
        await gated(
            service=service,
            user_google_email="me@gmail.com",
            send_as_email="alias@gmail.com",
        )
    service.users.assert_not_called()


@pytest.mark.asyncio
async def test_delegated_tool_runs_for_delegated_account(monkeypatch):
    monkeypatch.setattr(
        ext, "is_delegated", lambda email: email.endswith("@firma.example")
    )
    service = Mock()
    service.users().settings().sendAs().verify().execute.return_value = {}
    gated = ext._delegated_only(_unwrap(ext.verify_gmail_send_as))
    result = await gated(
        service=service,
        user_google_email="me@firma.example",
        send_as_email="alias@firma.example",
    )
    assert "alias@firma.example" in result


@pytest.mark.asyncio
async def test_list_accounts_reports_type_and_capabilities(monkeypatch):
    store = Mock()
    store.list_users.return_value = ["me@gmail.com", "boss@firma.example"]
    monkeypatch.setattr(ext, "get_credential_store", lambda: store)
    monkeypatch.setattr(ext, "is_service_account_enabled", lambda: False)
    out = await _unwrap(ext.list_gmail_accounts)()
    assert "me@gmail.com | private, oauth | core tools" in out
    assert "boss@firma.example | workspace, oauth | core tools" in out


# --- label updates must not wipe what the caller did not pass ---------------


@pytest.mark.asyncio
async def test_label_update_keeps_color_and_visibility():
    service = Mock()
    service.users().labels().get().execute.return_value = {
        "id": "Label_1",
        "name": "Old",
        "labelListVisibility": "labelHide",
        "messageListVisibility": "hide",
        "color": {"backgroundColor": "#fb4c2f", "textColor": "#ffffff"},
        "messagesTotal": 12,
    }
    service.users().labels().update().execute.return_value = {
        "id": "Label_1",
        "name": "New",
    }
    await _unwrap(gmail.gmail_tools.manage_gmail_label)(
        service=service,
        user_google_email="u@example.com",
        action="update",
        label_id="Label_1",
        name="New",
    )
    body = service.users().labels().update.call_args.kwargs["body"]
    assert body["name"] == "New"
    assert body["color"] == {"backgroundColor": "#fb4c2f", "textColor": "#ffffff"}
    assert body["labelListVisibility"] == "labelHide"
    assert body["messageListVisibility"] == "hide"
    assert "messagesTotal" not in body  # read-only field, Gmail rejects it


@pytest.mark.asyncio
async def test_label_update_writes_what_was_passed():
    service = Mock()
    service.users().labels().get().execute.return_value = {
        "id": "Label_1",
        "name": "Old",
        "color": {"backgroundColor": "#000000", "textColor": "#ffffff"},
    }
    service.users().labels().update().execute.return_value = {
        "id": "Label_1",
        "name": "Old",
    }
    await _unwrap(gmail.gmail_tools.manage_gmail_label)(
        service=service,
        user_google_email="u@example.com",
        action="update",
        label_id="Label_1",
        background_color="#ffffff",
        text_color="#000000",
        message_list_visibility="hide",
    )
    body = service.users().labels().update.call_args.kwargs["body"]
    assert body["color"]["backgroundColor"] == "#ffffff"
    assert body["messageListVisibility"] == "hide"
    assert body["name"] == "Old"


# --- filter replay ---------------------------------------------------------


def test_filter_criteria_to_query():
    # Values stay literal: unquoted, "alpha OR beta" would become Gmail's OR.
    assert (
        ext._filter_criteria_to_query(
            {"from": "a@b.com", "hasAttachment": True, "negatedQuery": "in:chats"}
        )
        == 'from:"a@b.com" has:attachment -(in:chats)'
    )
    assert ext._filter_criteria_to_query({"subject": "alpha OR beta"}) == (
        'subject:"alpha OR beta"'
    )
    # A filter's "to" matches To, Cc and Bcc; Gmail's to: operator does not.
    assert ext._filter_criteria_to_query({"to": "team@b.com"}) == (
        '(to:"team@b.com" OR cc:"team@b.com" OR bcc:"team@b.com")'
    )


@pytest.mark.asyncio
async def test_apply_filter_dry_run_changes_nothing():
    service = Mock()
    service.users().settings().filters().get().execute.return_value = {
        "criteria": {"from": "a@b.com"},
        "action": {"addLabelIds": ["Label_1"], "removeLabelIds": ["INBOX"]},
    }
    service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}]
    }
    service.users().messages().get().execute.return_value = {"payload": {"headers": []}}
    result = await _unwrap(ext.apply_gmail_filter_to_existing_mail)(
        service=service, user_google_email="u@example.com", filter_id="f1"
    )
    service.users().messages().batchModify.assert_not_called()
    assert "Matches: 1" in result
    assert "Would remove labels: (none)" in result  # label actions only by default


@pytest.mark.asyncio
async def test_apply_filter_applies_adds_only_by_default():
    service = Mock()
    service.users().settings().filters().get().execute.return_value = {
        "criteria": {"from": "a@b.com"},
        "action": {"addLabelIds": ["Label_1"], "removeLabelIds": ["INBOX"]},
    }
    service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}, {"id": "m2"}]
    }
    await _unwrap(ext.apply_gmail_filter_to_existing_mail)(
        service=service,
        user_google_email="u@example.com",
        filter_id="f1",
        dry_run=False,
    )
    body = service.users().messages().batchModify.call_args.kwargs["body"]
    assert body["ids"] == ["m1", "m2"]
    assert body["addLabelIds"] == ["Label_1"]
    assert "removeLabelIds" not in body
