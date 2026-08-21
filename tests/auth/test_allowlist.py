"""Fail-closed email allowlist (auth/allowlist.py), ported from mcp-gmail-multi."""

import pytest

from auth import allowlist
from auth.allowlist import EmailNotAllowedError, enforce_allowlist, is_allowed


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.delenv(allowlist.ALLOWED_EMAILS_ENV, raising=False)
    monkeypatch.setattr(
        allowlist, "_allowlist_path", lambda: tmp_path / allowlist.ALLOWLIST_FILENAME
    )
    return tmp_path / allowlist.ALLOWLIST_FILENAME


def test_not_configured_allows_everything():
    assert is_allowed("anyone@example.com")


def test_empty_file_denies_everything(isolated):
    isolated.write_text("")
    assert not is_allowed("anyone@example.com")


def test_empty_env_denies_everything(monkeypatch):
    monkeypatch.setenv(allowlist.ALLOWED_EMAILS_ENV, "")
    assert not is_allowed("anyone@example.com")


def test_file_ignores_blanks_comments_and_case(isolated):
    isolated.write_text("# my accounts\nMe@Example.com\n\nother@example.com\n")
    assert is_allowed("me@example.com")
    assert is_allowed("OTHER@example.com")
    assert not is_allowed("stranger@example.com")


def test_env_overrides_file(isolated, monkeypatch):
    isolated.write_text("file@example.com\n")
    monkeypatch.setenv(
        allowlist.ALLOWED_EMAILS_ENV, "env@example.com, second@example.com"
    )
    assert is_allowed("env@example.com")
    assert is_allowed("second@example.com")
    assert not is_allowed("file@example.com")


def test_enforce_raises_with_address(isolated):
    isolated.write_text("ok@example.com\n")
    enforce_allowlist("ok@example.com")
    with pytest.raises(EmailNotAllowedError, match="bad@example.com"):
        enforce_allowlist("bad@example.com")
