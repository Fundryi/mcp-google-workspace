# Google Calendar Tools Reference

MCP tools for Google Calendar event management and availability queries. All tools require `user_google_email` (string, required).

---

## Calendars & Events

### list_calendars
List a single page of calendars accessible to the authenticated user. Returns summary, ID, and primary status; use the `Next page token` from the response as `page_token` to retrieve additional pages.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| max_results | integer | no | | Max calendars per page. Omit for the API default |
| page_token | string | no | | Token from a previous response's `Next page token` |

### get_events
Retrieve events from a calendar. Fetch a single event by ID, list events in a time range, or search by keyword.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| calendar_id | string | no | primary | Calendar ID from `list_calendars` |
| event_id | string | no | | Retrieve a single event; ignores time filters when set |
| time_min | string | no | now | Start of range, RFC 3339 (e.g. `2026-03-19T09:00:00Z` or `2026-03-19`) |
| time_max | string | no | | End of range, RFC 3339 (exclusive) |
| max_results | integer | no | 25 | Max events per page |
| query | string | no | | Keyword search across summary, description, location |
| detailed | boolean | no | false | Include description, location, attendees with response status |
| include_attachments | boolean | no | false | Show attachment details (fileId, fileUrl, mimeType, title). Only applies when `detailed=true` |
| single_events | boolean | no | true | Expand recurring events into instances. When false, an omitted `time_min` leaves the lower bound unset |
| page_token | string | no | | Token from a previous response's `Next page token`. Requires `time_min` when `single_events=true`. Ignored when `event_id` is set |

Both tools append `Next page token: <token>` to the response when more pages remain. Pass it
back as `page_token` to continue, keeping all other query parameters unchanged.

`get_events` also returns `Pagination time_min: <timestamp>` when a lower bound is used
and more pages remain, including on empty pages. Pass that timestamp as `time_min` on
the next call, even if you omitted it on the first call. This preserves the original
range instead of letting the default current time move between pages. With
`single_events=false` and no initial `time_min`, continue leaving `time_min` unset.

### manage_event
Create, update, or delete a calendar event.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | `create`, `update`, or `delete` |
| summary | string | for create | | Event title |
| start_time | string | for create | | RFC 3339 format |
| end_time | string | for create | | RFC 3339 format |
| event_id | string | for update/delete | | Event ID |
| confirm_recurring | boolean | no | false | Required as true to delete a repeating event, which removes every instance. To drop one instance, pass that instance's own event_id |
| calendar_id | string | no | primary | |
| description | string | no | | Event description |
| location | string | no | | Event location |
| attendees | array | no | | Email strings or attendee objects |
| timezone | string | no | | e.g. `Australia/Melbourne` |
| attachments | array of strings | no | | Google Drive file URLs or IDs |
| add_google_meet | boolean | no | | Add or remove Google Meet link |
| reminders | array | no | | Custom reminder objects (see below) |
| use_default_reminders | boolean | no | | Use account default reminders |
| transparency | string | no | | `opaque` (busy) or `transparent` (free) |
| visibility | string | no | | `default`, `public`, `private`, or `confidential` |
| color_id | string | no | | Event color 1-11 (update only) |
| guests_can_modify | boolean | no | | Attendees can edit the event |
| guests_can_invite_others | boolean | no | | Attendees can invite others |
| guests_can_see_other_guests | boolean | no | | Attendees can see other attendees |

**Reminder format** (each item is a dict):
- `{"method": "email", "minutes": 30}` -- email reminder 30 minutes before
- `{"method": "popup", "minutes": 10}` -- popup reminder 10 minutes before

---

## Calendar Administration

### get_calendar_settings
The account's own settings: time zone, locale, week start, 12 or 24 hour clock, invitation handling. Read these before writing times, so the numbers mean what you think.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| setting_id | string | no | | One setting, for example "timezone". Omit for all |


### manage_calendar
Reads, renames, deletes or empties a calendar itself, not its events. An update writes only the fields you pass.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | "get", "update", "delete" or "clear" |
| calendar_id | string | yes | | "primary" or a secondary calendar's address |
| summary, description, location, timezone | string | no | | Update only |
| confirm | boolean | no | false | Required as true for delete and clear |

`clear` works on "primary" only and empties it. `delete` removes a secondary calendar and its events. To stop seeing a calendar you do not own, unsubscribe instead.

### manage_calendar_access
Lists who a calendar is shared with, shares it, or takes access away.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | "list", "grant" or "revoke" |
| calendar_id | string | no | primary | |
| scope_type | string | no | user | "user", "group", "domain" or "default". "default" means the whole internet |
| scope_value | string | conditional | | Address or domain. Not needed for "default" |
| role | string | no | reader | "none", "freeBusyReader", "reader", "writer" or "owner" |
| rule_id | string | for revoke | | Take it from the list output |
| send_notifications | boolean | no | true | Whether Google mails the person |

A grant that reaches a whole domain or the whole internet says so in the result.

### manage_calendar_subscription
Your own calendar list: which calendars you follow, your name for them, their color, and whether they show in the grid. It never changes the calendar itself, so unsubscribing is always reversible.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | "list", "subscribe", "unsubscribe" or "update" |
| calendar_id | string | conditional | | Required for everything except "list" |
| summary_override | string | no | | Your own name for it. Update only |
| color_id | string | no | | Calendar color id. Update only |
| hidden, selected | boolean | no | | Update only |

---

## Availability

### query_freebusy
Check free/busy status for one or more calendars over a time interval.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| time_min | string | yes | | Start of interval, RFC 3339 |
| time_max | string | yes | | End of interval, RFC 3339 |
| calendar_ids | any | no | | Calendar IDs to query. Defaults to primary calendar if omitted |
| group_expansion_max | integer | no | | Max members per group (max 100) |
| calendar_expansion_max | integer | no | | Max calendars to query (max 50) |

---

## Tips

**Calendar IDs**: Use `list_calendars` to discover IDs. The primary calendar can always be referenced as `primary`.

**Time format**: All time parameters use RFC 3339. Date-only values (e.g. `2026-03-19`) are accepted and interpreted as midnight UTC.

**All-day events**: Set `start_time` and `end_time` to date-only strings (e.g. `2026-03-20` and `2026-03-21` for a single all-day event on 20 March).

**Attendees**: Can be simple email strings (`["alice@example.com"]`) or attendee objects with additional fields (`[{"email": "alice@example.com", "optional": true}]`).
