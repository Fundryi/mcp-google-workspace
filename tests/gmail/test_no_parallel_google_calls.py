"""One googleapiclient service must never be called from two threads at once.

build() hands every tool a single httplib2.Http, and httplib2.Http is not
thread safe: it keeps one TLS connection and two threads writing to it produce
garbled records. That surfaces as ssl.SSLError or a read timeout, not as
anything that names the real cause. Upstream already learned this, which is why
its batch tools say "to prevent SSL connection exhaustion" and fall back to
sequential fetches rather than parallel ones.

These tests pin the rule: fan out over the Gmail batch endpoint, or go one at a
time. Never asyncio.gather over the shared service.
"""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import gmail.gmail_extended_tools as ext  # noqa: E402
import gmail.gmail_tools as gmail  # noqa: E402


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _batching_service():
    """A service whose batch endpoint works, and that screams if bypassed."""
    service = Mock()
    added = []

    def _add(request, request_id=None):
        added.append(request_id)

    batch = Mock()
    batch.add.side_effect = _add
    service.new_batch_http_request.return_value = batch
    service._added = added
    service._batch = batch
    return service


@pytest.mark.asyncio
async def test_message_metadata_uses_the_batch_endpoint():
    service = _batching_service()

    def _execute_batch():
        # The real batch endpoint answers through the callback it was given.
        callback = service.new_batch_http_request.call_args.kwargs["callback"]
        for mid in service._added:
            callback(mid, {"id": mid, "payload": {"headers": []}}, None)

    service._batch.execute.side_effect = _execute_batch

    messages = await ext._message_metadata(service, ["m1", "m2", "m3"])

    assert service.new_batch_http_request.called
    assert [m["id"] for m in messages] == ["m1", "m2", "m3"]
    # One HTTP round trip for the chunk, not one per message on shared sockets.
    assert service._batch.execute.call_count == 1


@pytest.mark.asyncio
async def test_message_metadata_falls_back_to_one_at_a_time():
    service = Mock()
    service.new_batch_http_request.side_effect = RuntimeError("batch endpoint down")
    service.users().messages().get().execute.return_value = {
        "id": "m1",
        "payload": {"headers": []},
    }

    messages = await ext._message_metadata(service, ["m1", "m2"])

    assert len(messages) == 2  # degraded, but still answered


@pytest.mark.asyncio
async def test_detailed_label_listing_uses_the_batch_endpoint():
    service = _batching_service()
    service.users().labels().list().execute.return_value = {
        "labels": [{"id": f"Label_{n}", "name": f"L{n}"} for n in range(37)]
    }

    def _execute_batch():
        callback = service.new_batch_http_request.call_args.kwargs["callback"]
        for label_id in service._added:
            callback(label_id, {"id": label_id, "name": label_id}, None)

    service._batch.execute.side_effect = _execute_batch

    result = await _unwrap(gmail.list_gmail_labels)(
        service=service, user_google_email="u@example.com", detailed=True
    )

    assert service.new_batch_http_request.called
    assert "Label_36" in result


@pytest.mark.asyncio
async def test_bulk_query_dry_run_renders_its_sample_through_the_batch_path():
    service = _batching_service()
    service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}, {"id": "m2"}]
    }

    def _execute_batch():
        callback = service.new_batch_http_request.call_args.kwargs["callback"]
        for mid in service._added:
            callback(
                mid,
                {
                    "id": mid,
                    "payload": {
                        "headers": [
                            {"name": "From", "value": f"{mid}@example.com"},
                            {"name": "Subject", "value": f"subject {mid}"},
                        ]
                    },
                },
                None,
            )

    service._batch.execute.side_effect = _execute_batch

    result = await _unwrap(ext.modify_gmail_messages_by_query)(
        service=service,
        user_google_email="u@example.com",
        query="from:a@b.com",
        add_label_ids=["Label_1"],
    )

    assert "m1@example.com" in result and "subject m2" in result
    service.users().messages().batchModify.assert_not_called()


@pytest.mark.asyncio
async def test_a_real_run_reports_in_the_past_tense():
    """ "Would add" over "Applied to 2 messages" makes the caller re-check."""
    service = _batching_service()
    service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}, {"id": "m2"}]
    }
    service._batch.execute.side_effect = lambda: None

    applied = await _unwrap(ext.modify_gmail_messages_by_query)(
        service=service,
        user_google_email="u@example.com",
        query="from:a@b.com",
        add_label_ids=["Label_1"],
        dry_run=False,
    )
    assert "Added labels: Label_1" in applied
    assert "Would" not in applied
    assert "Applied to 2 messages" in applied

    previewed = await _unwrap(ext.modify_gmail_messages_by_query)(
        service=service,
        user_google_email="u@example.com",
        query="from:a@b.com",
        add_label_ids=["Label_1"],
    )
    assert "Would add labels: Label_1" in previewed


@pytest.mark.asyncio
async def test_a_real_run_that_matched_nothing_stays_in_the_would_tense():
    service = _batching_service()
    service.users().messages().list().execute.return_value = {"messages": []}
    service.users().settings().filters().get().execute.return_value = {
        "criteria": {"from": "a@b.com"},
        "action": {"addLabelIds": ["Label_1"]},
    }

    result = await _unwrap(ext.apply_gmail_filter_to_existing_mail)(
        service=service,
        user_google_email="u@example.com",
        filter_id="f1",
        dry_run=False,
    )
    assert "Would add labels" in result
    assert "Nothing to do." in result


@pytest.mark.asyncio
async def test_both_bulk_tools_word_the_change_block_the_same_way():
    service = _batching_service()
    service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}]
    }
    service.users().settings().filters().get().execute.return_value = {
        "criteria": {"from": "a@b.com"},
        "action": {"addLabelIds": ["Label_1"]},
    }
    service._batch.execute.side_effect = lambda: None

    by_query = await _unwrap(ext.modify_gmail_messages_by_query)(
        service=service,
        user_google_email="u@example.com",
        query="from:a@b.com",
        add_label_ids=["Label_1"],
        dry_run=False,
    )
    by_filter = await _unwrap(ext.apply_gmail_filter_to_existing_mail)(
        service=service,
        user_google_email="u@example.com",
        filter_id="f1",
        dry_run=False,
    )
    for line in ("Added labels: Label_1", "Removed labels: (none)"):
        assert line in by_query, line
        assert line in by_filter, line


@pytest.mark.asyncio
async def test_label_verification_chunks_at_the_small_read_size_and_paces(
    monkeypatch,
):
    """Upstream's read-back must chunk at 10 and breathe between chunks.

    It arrived chunking at GMAIL_BATCH_SIZE (25) with no gap. The batch endpoint
    runs every get in a chunk concurrently server-side, and the comment above
    GMAIL_SEARCH_HEADER_BATCH_SIZE already records what 25 costs: Gmail answers
    "Too many concurrent requests". Verification is on by default, so every bulk
    label sweep would pay for it.
    """
    message_ids = [f"m{i}" for i in range(12)]
    chunks = []
    sleeps = []

    def _new_batch(callback):
        batch = Mock()
        ids = []
        batch.add.side_effect = lambda request, request_id=None: ids.append(request_id)

        def _execute():
            chunks.append(list(ids))
            for mid in ids:
                callback(mid, {"id": mid, "labelIds": ["INBOX", "STARRED"]}, None)

        batch.execute.side_effect = _execute
        return batch

    service = Mock()
    service.new_batch_http_request.side_effect = _new_batch

    async def _record_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(gmail.asyncio, "sleep", _record_sleep)

    statuses = await gmail._verify_batch_label_changes(
        service, message_ids, ["STARRED"], None
    )

    assert [len(c) for c in chunks] == [10, 2]
    assert sleeps == [gmail.GMAIL_REQUEST_DELAY]
    assert set(statuses.values()) == {"applied"}
