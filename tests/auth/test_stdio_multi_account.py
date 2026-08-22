"""Fork additions: quiet browser, transient refresh errors, atomic credential writes."""

import json

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from auth import google_auth
from auth.credential_store import LocalDirectoryCredentialStore


@pytest.fixture(autouse=True)
def stdio(monkeypatch):
    monkeypatch.setattr(google_auth, "get_transport_mode", lambda: "stdio")
    monkeypatch.setattr(google_auth, "is_oauth21_enabled", lambda: False)
    monkeypatch.delenv("WORKSPACE_MCP_NO_BROWSER", raising=False)


def test_browser_only_when_explicitly_requested():
    assert google_auth._should_open_browser(True)
    assert not google_auth._should_open_browser(False)


def test_no_browser_env_wins(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_NO_BROWSER", "1")
    assert not google_auth._should_open_browser(True)


def test_no_browser_outside_stdio(monkeypatch):
    monkeypatch.setattr(google_auth, "get_transport_mode", lambda: "streamable-http")
    assert not google_auth._should_open_browser(True)


@pytest.mark.parametrize(
    "message, permanent",
    [
        ("invalid_grant: Token has been expired or revoked.", True),
        ("deleted_client: The OAuth client was deleted.", True),
        ("('Unable to find the server at oauth2.googleapis.com')", False),
        ("internal_failure: 500 Internal Server Error", False),
    ],
)
def test_permanent_vs_transient_refresh_error(message, permanent):
    assert google_auth._is_permanent_refresh_error(RefreshError(message)) is permanent


def test_store_credential_leaves_no_temp_file(tmp_path):
    store = LocalDirectoryCredentialStore(str(tmp_path))
    creds = Credentials(
        token="t",
        refresh_token="r",
        token_uri="u",
        client_id="c",
        client_secret="s",
        scopes=["x"],
    )
    assert store.store_credential("a@example.com", creds)
    files = sorted(p.name for p in tmp_path.iterdir())
    assert len(files) == 1 and not files[0].endswith(".tmp")
    assert json.loads((tmp_path / files[0]).read_text())["token"] == "t"
