"""Tests for the fork's Gmail management tools (gmail/gmail_extended_tools.py)."""

import base64
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import SMTP
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


def _draft(subject="Re: Original topic", body="previous body", html=None, pdf=False):
    message = EmailMessage(policy=SMTP)
    message["Subject"] = subject
    message["To"] = "them@example.com"
    message["Reply-To"] = "me+replies@example.com"
    message["In-Reply-To"] = "<abc@mail.gmail.com>"
    message["References"] = "<abc@mail.gmail.com>"
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    if pdf:
        message.add_attachment(
            b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="a.pdf"
        )
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {"id": "d1", "message": {"id": "m1", "threadId": "t1", "raw": raw}}


def _draft_service(draft, stored_headers=()):
    service = Mock()
    stored = {
        "message": {
            "threadId": "t1",
            "payload": {
                "headers": [{"name": n, "value": v} for n, v in stored_headers]
            },
        }
    }
    # First get is the raw draft, the second the read-back after the update.
    service.users().drafts().get().execute.side_effect = [draft, stored]
    service.users().drafts().update().execute.return_value = {"id": "d1"}
    return service


def _sent(service) -> EmailMessage:
    raw = service.users().drafts().update.call_args.kwargs["body"]["message"]["raw"]
    return message_from_bytes(base64.urlsafe_b64decode(raw), policy=SMTP)


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


def test_extended_tier_gmail_set_is_exact():
    from core.tool_tier_loader import ToolTierLoader

    loader = ToolTierLoader()
    promoted = {
        "get_gmail_threads_content_batch",
        "batch_modify_gmail_message_labels",
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
        "get_gmail_vacation_settings",
        "modify_gmail_messages_by_query",
        "list_gmail_history",
    }
    extended = set(loader.get_tools_up_to_tier("extended", ["gmail"]))
    complete = set(loader.get_tools_up_to_tier("complete", ["gmail"]))
    upstream_extended = {
        "get_gmail_attachment_content",
        "get_gmail_thread_content",
        "modify_gmail_message_labels",
        "list_gmail_labels",
        "manage_gmail_label",
        "draft_gmail_message",
        "list_gmail_filters",
        "manage_gmail_filter",
    }
    core = set(loader.get_tools_for_tier("core", ["gmail"]))
    assert extended == core | upstream_extended | promoted
    # Forwarding, send-as, IMAP/POP and mailbox watch stay complete-only.
    assert promoted < complete


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


# --- update_draft: edits the MIME message in place


async def _update(service, **kwargs):
    return await _unwrap(ext.update_gmail_draft)(
        service=service, user_google_email="u@example.com", draft_id="d1", **kwargs
    )


@pytest.mark.asyncio
async def test_update_draft_keeps_explicit_subject_in_thread():
    service = _draft_service(_draft())
    await _update(service, subject="My own subject", body="hi")

    sent = _sent(service)
    assert sent["Subject"] == "My own subject"
    assert sent["In-Reply-To"] == "<abc@mail.gmail.com>"
    assert sent["References"] == "<abc@mail.gmail.com>"
    assert sent.get_body().get_content().strip() == "hi"
    message = service.users().drafts().update.call_args.kwargs["body"]["message"]
    assert message["threadId"] == "t1"


@pytest.mark.asyncio
async def test_update_draft_header_only_keeps_html_attachment_and_reply_to():
    service = _draft_service(_draft(html="<p>rich</p>", pdf=True))
    await _update(service, to="x@y.z", cc="")

    sent = _sent(service)
    assert sent["To"] == "x@y.z"
    assert sent["Cc"] is None
    assert sent["Reply-To"] == "me+replies@example.com"
    assert sent["Subject"] == "Re: Original topic"
    assert len(sent.get_all("Subject")) == 1
    assert "<p>rich</p>" in sent.get_body(("html",)).get_content()
    assert [a.get_filename() for a in sent.iter_attachments()] == ["a.pdf"]


@pytest.mark.asyncio
async def test_update_draft_new_body_keeps_attachment():
    service = _draft_service(_draft(html="<p>old</p>", pdf=True))
    await _update(service, body="<p>new</p>", body_format="html")

    sent = _sent(service)
    assert "<p>new</p>" in sent.get_body(("html",)).get_content()
    assert "old" not in sent.get_body(("plain",)).get_content()
    assert [a.get_filename() for a in sent.iter_attachments()] == ["a.pdf"]
    assert len(sent.get_all("MIME-Version")) == 1


@pytest.mark.asyncio
async def test_update_draft_refuses_body_that_would_drop_inline_image():
    message = EmailMessage(policy=SMTP)
    message["Subject"] = "s"
    message.set_content("plain")
    message.add_alternative("<img src=cid:i1>", subtype="html")
    message.get_payload(1).add_related(
        b"png", maintype="image", subtype="png", cid="<i1>"
    )
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service = _draft_service({"id": "d1", "message": {"id": "m1", "raw": raw}})

    with pytest.raises(ext.UserInputError):
        await _update(service, body="new")
    service.users().drafts().update.return_value.execute.assert_not_called()


@pytest.mark.asyncio
async def test_update_draft_reports_what_gmail_stored():
    service = _draft_service(_draft(), stored_headers=[("Subject", "Stored subject")])
    result = await _update(service, subject="Sent subject")

    assert "Subject: Stored subject" in result
    assert "Sent subject" not in result


# --- list_drafts -------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_drafts_batches_and_names_unreadable_drafts():
    service = Mock()
    service.users().drafts().list().execute.return_value = {
        "drafts": [
            {"id": "d1", "message": {"id": "m1", "threadId": "t1"}},
            {"id": "d2", "message": {"id": "m2", "threadId": "t2"}},
        ],
        "nextPageToken": "next",
    }
    added = []
    batch = Mock()
    batch.add.side_effect = lambda request, request_id=None: added.append(request_id)
    service.new_batch_http_request.return_value = batch

    def _execute_batch():
        callback = service.new_batch_http_request.call_args.kwargs["callback"]
        # m1 fails, m2 answers: the subject must stay on d2, not slide to d1.
        callback("m1", None, RuntimeError("gone"))
        callback(
            "m2",
            {
                "id": "m2",
                "payload": {"headers": [{"name": "Subject", "value": "Second"}]},
            },
            None,
        )

    batch.execute.side_effect = _execute_batch

    result = await _unwrap(ext.list_gmail_drafts)(
        service=service, user_google_email="u@example.com", page_token="p1"
    )

    assert added == ["m1", "m2"]
    assert batch.execute.call_count == 1
    assert "Draft ID: d1 | Thread ID: t1 | could not be read" in result
    assert "Draft ID: d2 | Thread ID: t2 | Subject: Second" in result
    assert "Next page token: next" in result
    assert service.users().drafts().list.call_args.kwargs["pageToken"] == "p1"
    get_kwargs = service.users().messages().get.call_args.kwargs
    assert get_kwargs["metadataHeaders"] == ["Subject", "To"]


# --- limits are refused, never reinterpreted ------------------------------------


@pytest.mark.asyncio
async def test_modify_by_query_refuses_zero_cap_before_any_call():
    service = Mock()
    with pytest.raises(ext.UserInputError):
        await _unwrap(ext.modify_gmail_messages_by_query)(
            service=service,
            user_google_email="u@example.com",
            query="from:x",
            add_label_ids=["L"],
            dry_run=False,
            max_messages=0,
        )
    assert not service.users().messages().list.called
    assert not service.users().messages().batchModify.called


@pytest.mark.asyncio
async def test_modify_by_query_reports_partial_apply():
    service = Mock()
    service.users().messages().list().execute.return_value = {
        "messages": [{"id": f"m{n}"} for n in range(1500)]
    }
    service.users().messages().batchModify().execute.side_effect = [
        {},
        RuntimeError("boom"),
    ]
    with pytest.raises(Exception, match="Applied to 1000 of 1500 messages"):
        await _unwrap(ext.modify_gmail_messages_by_query)(
            service=service,
            user_google_email="u@example.com",
            query="from:x",
            add_label_ids=["L"],
            dry_run=False,
        )


@pytest.mark.asyncio
async def test_history_refuses_zero_cap():
    with pytest.raises(ext.UserInputError):
        await _unwrap(ext.list_gmail_history)(
            service=Mock(),
            user_google_email="u@example.com",
            start_history_id="1",
            max_changes=0,
        )


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
