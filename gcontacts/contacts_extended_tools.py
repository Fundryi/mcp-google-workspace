"""
Contacts tools added by this fork.

"Other contacts" are the addresses Google saved because you mailed them, but
that you never added to Contacts. Upstream's search_contacts cannot see them,
so a person you write to every week can be invisible. This finds them.
"""

import asyncio
import logging
from typing import Any, Dict, List

from mcp.types import ToolAnnotations

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import handle_http_errors

logger = logging.getLogger(__name__)

_READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)

_OTHER_CONTACT_FIELDS = "names,emailAddresses,phoneNumbers"


def _person_line(person: Dict[str, Any]) -> str:
    """One line per person: name, then every address we got."""
    names = person.get("names") or []
    name = names[0].get("displayName") if names else None
    emails = ", ".join(
        e.get("value", "") for e in person.get("emailAddresses") or [] if e.get("value")
    )
    phones = ", ".join(
        p.get("value", "") for p in person.get("phoneNumbers") or [] if p.get("value")
    )
    parts = [part for part in (emails, phones) if part]
    return f"  • {name or emails or '(no name)'}" + (
        f"\n    {' | '.join(parts)}" if parts else ""
    )


@server.tool(title="Search Other Contacts", annotations=_READ)
@handle_http_errors("search_other_contacts", is_read_only=True, service_type="people")
@require_google_service("people", "contacts_other_read")
async def search_other_contacts(
    service,
    user_google_email: str,
    query: str,
    max_results: int = 30,
) -> str:
    """
    Searches the addresses Google saved from your mail but that were never
    added to Contacts. Use it when search_contacts finds nothing for someone
    you clearly correspond with.

    Google caps this at 30 results and matches only the start of a name or
    address, so "smi" finds "Smith" but "mith" does not.

    Args:
        user_google_email (str): The user's Google email address. Required.
        query (str): What to look for. Matches the beginning of names and addresses.
        max_results (int): Up to 30, which is Google's own ceiling.

    Returns:
        str: One entry per match.
    """
    logger.info(
        f"[search_other_contacts] Email: '{user_google_email}', Query: '{query}'"
    )

    if not query.strip():
        raise Exception("A query is required.")

    # Google asks for a warm-up request with an empty query before the first
    # real search, otherwise the first search comes back empty.
    await asyncio.to_thread(
        service.otherContacts().search(query="", readMask=_OTHER_CONTACT_FIELDS).execute
    )
    response = await asyncio.to_thread(
        service.otherContacts()
        .search(
            query=query,
            pageSize=min(30, max_results),
            readMask=_OTHER_CONTACT_FIELDS,
        )
        .execute
    )
    results: List[Dict[str, Any]] = response.get("results", [])
    if not results:
        return f"No other contacts matched: {query}"

    lines = [f"{len(results)} other contacts matching '{query}':", ""]
    lines += [_person_line(result.get("person", {})) for result in results]
    if len(results) == 30:
        lines += ["", "That is Google's ceiling of 30. Narrow the query to see more."]
    return "\n".join(lines)


EXTENDED_CONTACTS_TOOL_NAMES = ["search_other_contacts"]
