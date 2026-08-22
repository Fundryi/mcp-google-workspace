"""Fork addition: keep the developer's real allowed.txt out of the test run."""

import pytest


@pytest.fixture(autouse=True)
def _no_real_allowlist(request, monkeypatch, tmp_path):
    if request.node.fspath.basename == "test_allowlist.py":
        return
    from auth import allowlist

    monkeypatch.delenv(allowlist.ALLOWED_EMAILS_ENV, raising=False)
    monkeypatch.setattr(allowlist, "_allowlist_path", lambda: tmp_path / "none.txt")
