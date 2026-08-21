"""
Per-account capabilities.

Two separate questions hide in "private or Workspace":

1. Account type. Private Gmail lives on gmail.com / googlemail.com. Anything
   else is treated as a Workspace domain.
2. Delegation. The gmail.settings.sharing writes (send-as aliases, forwarding
   addresses, auto-forwarding) only work through a service account with
   domain-wide delegation for that domain. That is a server config fact, so
   it can be answered without calling Google.

A server may hold OAuth credentials for private accounts and a delegated
service account for one or more Workspace domains at the same time. The
domain list in DWD_ALLOWED_DOMAINS decides which path an address takes.
"""

from typing import Dict

from auth.oauth_config import get_oauth_config, is_service_account_enabled

PRIVATE_DOMAINS = {"gmail.com", "googlemail.com"}


def domain_of(email: str) -> str:
    return (email or "").rsplit("@", 1)[-1].lower()


def is_private_account(email: str) -> bool:
    return domain_of(email) in PRIVATE_DOMAINS


def is_delegated(email: str) -> bool:
    """True when this address is served through a delegated service account."""
    if not is_service_account_enabled():
        return False
    domains = get_oauth_config().dwd_allowed_domains
    # ponytail: without a domain list the service account serves everything,
    # which is upstream's behaviour. Set DWD_ALLOWED_DOMAINS to mix in OAuth accounts.
    return not domains or domain_of(email) in domains


def describe_account(email: str) -> Dict[str, str]:
    """Type and capability summary for one address, no network calls."""
    delegated = is_delegated(email)
    if delegated:
        kind, auth, tools = "workspace", "delegated", "all tools"
    elif is_private_account(email):
        kind, auth, tools = "private", "oauth", "core tools"
    else:
        kind, auth, tools = "workspace", "oauth", "core tools"
    return {"email": email, "type": kind, "auth": auth, "tools": tools}


def format_account_line(email: str) -> str:
    info = describe_account(email)
    return f"{info['email']} | {info['type']}, {info['auth']} | {info['tools']}"
