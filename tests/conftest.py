"""Fork addition: keep the developer's real allowed.txt out of the test run."""

import pytest


@pytest.fixture(autouse=True)
def _no_real_allowlist(request, monkeypatch, tmp_path):
    if request.node.fspath.basename == "test_allowlist.py":
        return
    from auth import allowlist

    monkeypatch.delenv(allowlist.ALLOWED_EMAILS_ENV, raising=False)
    monkeypatch.setattr(allowlist, "_allowlist_path", lambda: tmp_path / "none.txt")


@pytest.fixture(autouse=True)
def _desktop_with_a_browser(monkeypatch):
    """Tests assert on the tab and the listener, so run as a desktop everywhere.

    is_headless() reads the real environment. Over SSH or on a Linux box
    without DISPLAY it would switch to paste-back and three upstream-shaped
    tests would fail for no reason. Tests of is_headless() set their own env.
    """
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("WORKSPACE_MCP_NO_BROWSER", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
