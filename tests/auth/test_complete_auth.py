"""Paste-back sign-in: complete_auth, is_headless, and PORT precedence."""

import logging
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

from auth import google_auth, port_resolver
from auth.allowlist import EmailNotAllowedError
from auth.google_auth import GoogleAuthenticationError, complete_auth
from auth.oauth21_session_store import OAuth21SessionStore

REDIRECT = "http://localhost:8000/oauth2callback"


@pytest.fixture
def capture(monkeypatch):
    """Stub the token exchange and record the URL complete_auth built."""
    seen = {}

    async def fake_callback(**kwargs):
        seen.update(kwargs)
        return "me@example.com", object()

    monkeypatch.setattr(google_auth, "handle_auth_callback", fake_callback)
    monkeypatch.setattr(google_auth, "get_oauth_redirect_uri", lambda: REDIRECT)
    monkeypatch.setattr(google_auth, "get_current_scopes", lambda: ["s"])
    return seen


@pytest.mark.parametrize(
    "pasted",
    [
        "http://localhost:8000/oauth2callback?state=st1&code=4/abc&scope=x",
        "http://127.0.0.1:8001/oauth2callback?code=4/abc&state=st1",
        "?code=4/abc&state=st1",
        "code=4/abc&state=st1",
    ],
)
@pytest.mark.asyncio
async def test_complete_auth_accepts_every_paste_shape(capture, pasted):
    assert await complete_auth(pasted) == "me@example.com"
    parts = urlsplit(capture["authorization_response"])
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == REDIRECT
    q = parse_qs(parts.query)
    assert q["code"] == ["4/abc"]
    assert q["state"] == ["st1"]
    assert capture["redirect_uri"] == REDIRECT


@pytest.mark.asyncio
async def test_bare_code_uses_newest_state(capture, monkeypatch):
    store = OAuth21SessionStore()
    store.store_oauth_state("old", code_verifier="v1")
    store.store_oauth_state("new", code_verifier="v2")
    monkeypatch.setattr(google_auth, "get_oauth21_session_store", lambda: store)
    await complete_auth("4/rawcode")
    q = parse_qs(urlsplit(capture["authorization_response"]).query)
    assert q == {"code": ["4/rawcode"], "state": ["new"]}


@pytest.mark.asyncio
async def test_bare_code_without_pending_state_is_explained(capture, monkeypatch):
    monkeypatch.setattr(
        google_auth, "get_oauth21_session_store", lambda: OAuth21SessionStore()
    )
    with pytest.raises(GoogleAuthenticationError, match="start_google_auth first"):
        await complete_auth("4/rawcode")


@pytest.mark.asyncio
async def test_expired_state_is_named(monkeypatch):
    monkeypatch.setattr(google_auth, "get_oauth_redirect_uri", lambda: REDIRECT)
    monkeypatch.setattr(google_auth, "get_current_scopes", lambda: ["s"])
    store = OAuth21SessionStore()
    store.store_oauth_state("gone", code_verifier="v", expires_in_seconds=0)
    monkeypatch.setattr(google_auth, "get_oauth21_session_store", lambda: store)
    with pytest.raises(GoogleAuthenticationError, match="expired or was already used"):
        await complete_auth("code=4/abc&state=gone")


@pytest.mark.asyncio
async def test_reused_code_is_named(monkeypatch):
    async def rejected(**kwargs):
        raise RuntimeError("(invalid_grant) Bad Request")

    monkeypatch.setattr(google_auth, "handle_auth_callback", rejected)
    monkeypatch.setattr(google_auth, "get_oauth_redirect_uri", lambda: REDIRECT)
    monkeypatch.setattr(google_auth, "get_current_scopes", lambda: ["s"])
    with pytest.raises(GoogleAuthenticationError, match="already used or has expired"):
        await complete_auth("code=4/abc&state=st1")


@pytest.mark.asyncio
async def test_allowlist_rejection_passes_through(monkeypatch):
    async def rejected(**kwargs):
        raise EmailNotAllowedError("x@y is not on the email allowlist")

    monkeypatch.setattr(google_auth, "handle_auth_callback", rejected)
    monkeypatch.setattr(google_auth, "get_oauth_redirect_uri", lambda: REDIRECT)
    monkeypatch.setattr(google_auth, "get_current_scopes", lambda: ["s"])
    with pytest.raises(EmailNotAllowedError, match="allowlist"):
        await complete_auth("code=4/abc&state=st1")


@pytest.mark.asyncio
async def test_google_error_and_missing_code_are_named(capture):
    with pytest.raises(GoogleAuthenticationError, match="access_denied"):
        await complete_auth(f"{REDIRECT}?error=access_denied&state=st1")
    with pytest.raises(GoogleAuthenticationError, match="No authorization code"):
        await complete_auth(f"{REDIRECT}?state=st1")


@pytest.mark.asyncio
async def test_tool_never_echoes_tokens(monkeypatch):
    import core.auth_extended_tools as mod

    async def ok(_):
        return "me@example.com"

    monkeypatch.setattr(mod, "complete_auth", ok)
    monkeypatch.setattr(mod, "is_oauth21_enabled", lambda: False)
    text = await mod.complete_google_auth("code=4/abc&state=st1")
    assert "me@example.com" in text
    assert "token" not in text.lower()


def test_is_headless(monkeypatch):
    for var in (
        "WORKSPACE_MCP_NO_BROWSER",
        "SSH_CONNECTION",
        "DISPLAY",
        "WAYLAND_DISPLAY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert google_auth.is_headless() is False
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 22 5.6.7.8 22")
    assert google_auth.is_headless() is True
    monkeypatch.delenv("SSH_CONNECTION")
    monkeypatch.setattr(sys, "platform", "linux")
    assert google_auth.is_headless() is True
    monkeypatch.setenv("DISPLAY", ":0")
    assert google_auth.is_headless() is False
    monkeypatch.setenv("WORKSPACE_MCP_NO_BROWSER", "1")
    assert google_auth.is_headless() is True


def test_workspace_mcp_port_beats_port(monkeypatch, caplog):
    monkeypatch.setenv("PORT", "3001")
    monkeypatch.setenv("WORKSPACE_MCP_PORT", "8000")
    with caplog.at_level(logging.WARNING, logger=port_resolver.__name__):
        assert port_resolver.preferred_port() == 8000
    assert "PORT=3001 ignored" in caplog.text
    monkeypatch.delenv("WORKSPACE_MCP_PORT")
    assert port_resolver.preferred_port() == 3001
    monkeypatch.delenv("PORT")
    assert port_resolver.preferred_port() == 8000


@pytest.mark.asyncio
async def test_start_google_auth_skips_listener_when_headless(monkeypatch):
    from core.server import start_google_auth

    async def fake_start_auth_flow(**kwargs):  # noqa: ARG001
        return "auth-url"

    def fail_if_called(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("listener must not start in paste-back mode")

    monkeypatch.setattr("core.server.is_oauth21_enabled", lambda: False)
    monkeypatch.setattr("core.server.is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr("core.server.check_client_secrets", lambda: None)
    monkeypatch.setattr("core.server.is_headless", lambda: True)
    monkeypatch.setattr(
        "core.server.get_oauth_redirect_uri_for_current_mode", lambda: REDIRECT
    )
    monkeypatch.setattr("core.server.start_auth_flow", fake_start_auth_flow)
    monkeypatch.setattr(
        "auth.oauth_callback_server.ensure_stdio_oauth_callback_available",
        fail_if_called,
    )
    assert await start_google_auth("Gmail", "user@gmail.com") == "auth-url"
