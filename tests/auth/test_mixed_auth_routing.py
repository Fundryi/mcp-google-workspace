"""A non-delegated address with its own OAuth credentials bypasses the service account."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import auth.credential_store as credential_store
import auth.service_decorator as sd


def _config(domains):
    return SimpleNamespace(dwd_allowed_domains=domains)


@pytest.mark.asyncio
async def test_private_address_with_oauth_creds_uses_oauth(monkeypatch):
    monkeypatch.setattr(sd, "is_service_account_enabled", lambda: True)
    monkeypatch.setattr(sd, "get_oauth_config", lambda: _config(["firma.example"]))
    store = Mock()
    store.get_credential.return_value = object()
    monkeypatch.setattr(credential_store, "get_credential_store", lambda: store)
    oauth = AsyncMock(return_value=("svc", "me@gmail.com"))
    monkeypatch.setattr(sd, "get_authenticated_google_service", oauth)

    service, email = await sd._authenticate_service(
        use_oauth21=False,
        service_name="gmail",
        service_version="v1",
        tool_name="t",
        user_google_email="me@gmail.com",
        resolved_scopes=[],
        mcp_session_id=None,
        authenticated_user=None,
    )
    assert (service, email) == ("svc", "me@gmail.com")
    oauth.assert_awaited_once()


def test_prefers_oauth_is_false_for_delegated_domain_or_missing_creds(monkeypatch):
    monkeypatch.setattr(sd, "get_oauth_config", lambda: _config(["firma.example"]))
    store = Mock()
    store.get_credential.return_value = None
    monkeypatch.setattr(credential_store, "get_credential_store", lambda: store)
    assert not sd._prefers_oauth("boss@firma.example")
    assert not sd._prefers_oauth("me@gmail.com")  # no creds: upstream rejection stands
    assert not sd._prefers_oauth(None)
