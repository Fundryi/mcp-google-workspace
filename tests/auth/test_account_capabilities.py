"""Per-account capability resolution (auth/account_capabilities.py)."""

from types import SimpleNamespace

import pytest

from auth import account_capabilities as ac


@pytest.fixture
def sa_mode(monkeypatch):
    def enable(domains):
        monkeypatch.setattr(ac, "is_service_account_enabled", lambda: True)
        monkeypatch.setattr(
            ac, "get_oauth_config", lambda: SimpleNamespace(dwd_allowed_domains=domains)
        )

    return enable


def test_without_service_account_nothing_is_delegated(monkeypatch):
    monkeypatch.setattr(ac, "is_service_account_enabled", lambda: False)
    assert not ac.is_delegated("boss@firma.example")
    assert ac.describe_account("boss@firma.example")["auth"] == "oauth"


def test_domain_list_splits_private_and_delegated(sa_mode):
    sa_mode(["firma.example"])
    assert ac.is_delegated("Boss@Firma.Example")
    assert not ac.is_delegated("me@gmail.com")
    assert ac.describe_account("me@gmail.com") == {
        "email": "me@gmail.com",
        "type": "private",
        "auth": "oauth",
        "tools": "core tools",
    }
    assert ac.describe_account("boss@firma.example")["tools"] == "all tools"


def test_empty_domain_list_delegates_everything(sa_mode):
    sa_mode([])
    assert ac.is_delegated("me@gmail.com")


def test_private_domains():
    assert ac.is_private_account("a@googlemail.com")
    assert not ac.is_private_account("a@firma.example")
