"""Large Gmail drafts and sends go through the resumable upload endpoint."""

import base64
import json
import os
import sys
from unittest.mock import Mock

import pytest
from googleapiclient.discovery import build
from googleapiclient.http import HttpMockSequence

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import core.utils as core_utils
import gmail.gmail_tools as gmail_tools
from core.utils import UserInputError
from gmail.gmail_tools import _gmail_message_kwargs


def _raw(size: int) -> str:
    return base64.urlsafe_b64encode(b"x" * size).decode()


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.mark.asyncio
async def test_missing_path_in_stdio_points_caller_to_remote_files_guide(
    tmp_path, monkeypatch
):
    """A caller-side path on a remote stdio server must say why, not vanish."""
    monkeypatch.setenv("ALLOWED_FILE_DIRS", str(tmp_path))
    monkeypatch.setattr(core_utils, "get_transport_mode", lambda: "stdio")
    service = Mock()
    service.users.return_value.settings.return_value.sendAs.return_value.list.return_value.execute.return_value = {
        "sendAs": [
            {"sendAsEmail": "user@example.com", "isPrimary": True, "isDefault": True}
        ]
    }

    with pytest.raises(UserInputError) as exc:
        await _unwrap(gmail_tools.draft_gmail_message)(
            service=service,
            user_google_email="user@example.com",
            subject="s",
            body="b",
            attachments=[{"path": str(tmp_path / "not-here.pdf")}],
            include_signature=False,
        )

    message = str(exc.value)
    assert "MCPHub" in message
    assert core_utils.REMOTE_FILES_GUIDE_URL in message
    service.users.return_value.drafts.return_value.create.assert_not_called()


def test_small_message_keeps_json_route():
    kwargs = _gmail_message_kwargs(_raw(1000), {"threadId": "t1"}, draft=True)
    assert "media_body" not in kwargs
    assert kwargs["body"] == {"message": {"threadId": "t1", "raw": _raw(1000)}}


def test_large_send_uses_resumable_upload_without_raw():
    size = gmail_tools.GMAIL_UPLOAD_CHUNK_BYTES + 1
    kwargs = _gmail_message_kwargs(_raw(size), {})
    assert "body" not in kwargs
    media = kwargs["media_body"]
    assert media.resumable()
    assert media.mimetype() == "message/rfc822"
    assert media.size() == size


def test_large_draft_keeps_thread_id_in_metadata():
    size = gmail_tools.GMAIL_UPLOAD_CHUNK_BYTES + 1
    kwargs = _gmail_message_kwargs(_raw(size), {"threadId": "t1"}, draft=True)
    assert kwargs["body"] == {"message": {"threadId": "t1"}}
    assert kwargs["media_body"].size() == size


def test_message_above_upload_cap_falls_back_to_json(monkeypatch):
    monkeypatch.setattr(gmail_tools, "GMAIL_UPLOAD_CHUNK_BYTES", 10)
    monkeypatch.setattr(gmail_tools, "GMAIL_UPLOAD_MAX_BYTES", 100)
    kwargs = _gmail_message_kwargs(_raw(101), {})
    assert "media_body" not in kwargs
    assert kwargs["body"] == {"raw": _raw(101)}


def test_resumable_draft_end_to_end_sends_chunks(monkeypatch):
    """Drive the real client through init + two chunks against a mock transport."""
    monkeypatch.setattr(gmail_tools, "GMAIL_UPLOAD_CHUNK_BYTES", 256 * 1024)
    size = 256 * 1024 + 1000
    upload_url = "https://gmail.googleapis.com/resumable/session/abc"
    http = HttpMockSequence(
        [
            ({"status": "200", "location": upload_url}, ""),
            ({"status": "308", "range": f"bytes=0-{256 * 1024 - 1}"}, ""),
            ({"status": "200"}, json.dumps({"id": "draft-big"})),
        ]
    )
    service = build("gmail", "v1", http=http, static_discovery=True)

    request = (
        service.users()
        .drafts()
        .create(**_gmail_message_kwargs(_raw(size), {"threadId": "t1"}, draft=True))
    )
    assert "/upload/gmail/v1/users/me/drafts" in request.uri
    assert "uploadType=resumable" in request.uri
    assert json.loads(request.body) == {"message": {"threadId": "t1"}}

    result = request.execute()

    assert result == {"id": "draft-big"}
    # init, first chunk (308), last chunk (200): every scripted response used.
    assert http._iterable == []
