"""Shared fixtures. The last two are fork additions.

The fork fixtures request ``_reset_oauth_config`` before ``monkeypatch`` so the
config rebuild still runs after the env is restored.
"""

import pytest

import auth.google_auth as google_auth
from auth.oauth_config import reload_oauth_config


@pytest.fixture(autouse=True)
def _reset_oauth_config():
    """Rebuild the OAuth config singleton after each test.

    Tests that set env vars and reload the config would otherwise leave a
    singleton built from their env (stateless mode, transport, OAuth 2.1) for
    every test that follows. As an argument-free autouse fixture this tears
    down after ``monkeypatch`` has restored the env, so the rebuild sees it.
    """
    yield
    reload_oauth_config()


@pytest.fixture(autouse=True)
def _empty_http_pool():
    """Keep connections recycled by one test, often mocks, out of the next."""
    google_auth._idle_http.clear()
    yield
    google_auth._idle_http.clear()


@pytest.fixture(autouse=True)
def _no_real_allowlist(request, _reset_oauth_config, monkeypatch, tmp_path):
    if request.node.fspath.basename == "test_allowlist.py":
        return
    from auth import allowlist

    monkeypatch.delenv(allowlist.ALLOWED_EMAILS_ENV, raising=False)
    monkeypatch.setattr(allowlist, "_allowlist_path", lambda: tmp_path / "none.txt")


@pytest.fixture(autouse=True)
def _desktop_with_a_browser(_reset_oauth_config, monkeypatch):
    """Tests assert on the tab and the listener, so run as a desktop everywhere.

    is_headless() reads the real environment. Over SSH or on a Linux box
    without DISPLAY it would switch to paste-back and three upstream-shaped
    tests would fail for no reason. Tests of is_headless() set their own env.
    """
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("WORKSPACE_MCP_NO_BROWSER", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
