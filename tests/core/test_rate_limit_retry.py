"""Fork addition: 429 / rate-limit responses are retried with backoff."""

import pytest
from googleapiclient.errors import HttpError

from core import utils
from core.utils import handle_http_errors, is_rate_limited, rate_limit_delay


def _http_error(status, body=b"", headers=None):
    resp = {"status": status, **(headers or {})}

    class Resp(dict):
        pass

    r = Resp(resp)
    r.status = status
    r.reason = "x"
    return HttpError(r, body)


def test_is_rate_limited():
    assert is_rate_limited(_http_error(429))
    assert is_rate_limited(
        _http_error(403, b'{"error":{"errors":[{"reason":"userRateLimitExceeded"}]}}')
    )
    assert not is_rate_limited(_http_error(403, b"accessNotConfigured"))
    assert not is_rate_limited(_http_error(500))


def test_rate_limit_delay_prefers_retry_after():
    assert rate_limit_delay(_http_error(429, headers={"retry-after": "7"}), 0) == 7
    assert rate_limit_delay(_http_error(429), 0) == 2
    assert rate_limit_delay(_http_error(429), 2) == 8


@pytest.mark.asyncio
async def test_handler_retries_then_succeeds(monkeypatch):
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(utils.asyncio, "sleep", fake_sleep)
    calls = {"n": 0}

    @handle_http_errors("demo", is_read_only=False, service_type="gmail")
    async def demo(user_google_email: str):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(429)
        return "ok"

    assert await demo(user_google_email="a@example.com") == "ok"
    assert calls["n"] == 3
    assert sleeps == [2, 4]
