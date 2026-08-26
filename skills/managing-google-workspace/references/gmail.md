# Google Gmail Tools Reference

MCP tools for Gmail message search, sending, drafting, labels, and filters. All tools require `user_google_email` (string, required).

## Contents
- Search & Read: search_gmail_messages, get_gmail_message_content, get_gmail_messages_content_batch, get_gmail_thread_content, get_gmail_threads_content_batch, get_gmail_attachment_content
- Send & Draft: send_gmail_message, draft_gmail_message
- Label Management: list_gmail_labels, manage_gmail_label, modify_gmail_message_labels, batch_modify_gmail_message_labels
- Filter Management: list_gmail_filters, manage_gmail_filter
- Extended Tools (this fork): accounts, trash, threads, drafts, lookups, push notifications, settings (vacation/IMAP/POP/language/forwarding/send-as)
- Tips

---

## Search & Read

### search_gmail_messages
Search messages by query. Returns message IDs, thread IDs, and Gmail web links.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| query | string | yes | | Gmail search query (see operators below) |
| user_google_email | string | yes | | |
| page_size | integer | no | 10 | Max results per page |
| page_token | any | no | | Pagination token |

### get_gmail_message_content
Get full content of a single message (subject, sender, recipients, date, Message-ID, body).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| message_id | string | yes | | |
| user_google_email | string | yes | | |

### get_gmail_messages_content_batch
Get content of multiple messages in one request. Max 25 per batch.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| message_ids | array of strings | yes | | Max 25 |
| user_google_email | string | yes | | |
| format | string | no | "full" | "full" (with body) or "metadata" (headers only) |

### get_gmail_thread_content
Get all messages in a conversation thread.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| thread_id | string | yes | | |
| user_google_email | string | yes | | |

### get_gmail_threads_content_batch
Get content of multiple threads in one request. Auto-batches in chunks of 25.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| thread_ids | array of strings | yes | | |
| user_google_email | string | yes | | |

### get_gmail_attachment_content
Download an attachment to local disk (stdio mode) or get a temporary URL (HTTP mode, 1-hour expiry).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| message_id | string | yes | | |
| attachment_id | string | yes | | |
| user_google_email | string | yes | | |

---

## Send & Draft

### send_gmail_message
Send an email. Supports new messages, replies, HTML, attachments, CC/BCC, and Send As aliases.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| to | string | yes | | Recipient address |
| subject | string | yes | | |
| body | string | yes | | Plain text or HTML content |
| body_format | string | no | "plain" | "plain" or "html" |
| user_google_email | string | yes | | |
| cc | string | no | | |
| bcc | string | no | | |
| from_name | string | no | | Display name, e.g. "John Doe" |
| from_email | string | no | | Send As alias (must be configured in Gmail settings) |
| thread_id | string | no | | Thread ID for replies |
| in_reply_to | string | no | | RFC Message-ID being replied to, e.g. `<msg@gmail.com>` |
| references | string | no | | Space-separated chain of Message-IDs for threading |
| attachments | array | no | | See attachment format below |

**Attachment format** (each item is an object):
- **File path**: `{"path": "path/to/file.pdf"}` -- optionally add `"filename"` and `"mime_type"`. Use forward slashes on all platforms
- **Base64 content**: `{"content": "base64data", "filename": "doc.pdf"}` -- optionally add `"mime_type"` (must be standard base64, not urlsafe)

### draft_gmail_message
Create a draft. Same capabilities as send but with additional signature/quoting options.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| subject | string | yes | | |
| body | string | yes | | |
| body_format | string | no | "plain" | "plain" or "html" |
| user_google_email | string | yes | | |
| to | string | no | | Can be empty for drafts |
| cc | string | no | | |
| bcc | string | no | | |
| from_name | string | no | | Display name |
| from_email | string | no | | Send As alias |
| thread_id | string | no | | For reply drafts |
| in_reply_to | string | no | | RFC Message-ID |
| references | string | no | | Message-ID chain |
| attachments | array | no | | Same format as send |
| include_signature | boolean | no | true | Append Gmail signature if available |
| quote_original | boolean | no | false | Include original message as quoted reply (requires thread_id) |

---

## Label Management

### list_gmail_labels
List all labels with IDs, names, and types.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |

### manage_gmail_label
Create, update, or delete a label.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | "create", "update", or "delete" |
| name | string | conditional | | Required for create, optional for update |
| label_id | string | conditional | | Required for update and delete |
| label_list_visibility | string | no | "labelShow" | "labelShow" or "labelHide" |
| message_list_visibility | string | no | "show" | "show" or "hide" |

### modify_gmail_message_labels
Add or remove labels on a single message.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| message_id | string | yes | | |
| add_label_ids | array of strings | no | | Label IDs to add |
| remove_label_ids | array of strings | no | | Label IDs to remove |

### batch_modify_gmail_message_labels
Add or remove labels on multiple messages at once.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| message_ids | array of strings | yes | | |
| add_label_ids | array of strings | no | | Label IDs to add |
| remove_label_ids | array of strings | no | | Label IDs to remove |

**Common label operations:**
- Archive: remove `"INBOX"`
- Mark read: remove `"UNREAD"`
- Mark unread: add `"UNREAD"`
- Star: add `"STARRED"`
- Trash: add `"TRASH"`

---

## Filter Management

### list_gmail_filters
List all filters with their criteria and actions.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |

### manage_gmail_filter
Create or delete a filter.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | "create" or "delete" |
| criteria | object | for create | | Filter criteria (see below) |
| filter_action | object | for create | | Actions to apply (see below) |
| filter_id | string | for delete | | ID of filter to remove |

**Criteria object keys:** `from`, `to`, `subject`, `query`, `negatedQuery`, `hasAttachment` (bool), `excludeChats` (bool), `size` (int), `sizeComparison` (string).

**Filter action object keys:** `addLabelIds` (array), `removeLabelIds` (array), `forward` (string).

---

## Extended Tools (this fork)

37 additional tools. All take `user_google_email` (string, required) unless noted. Parameters below are exhaustive -- a tool listed with no extra parameters takes only `user_google_email`.

### Accounts

- `list_gmail_accounts` -- no parameters. Lists every signed-in account as `email | type, auth | tools`: `all tools` means the account is served by a delegated service account and can use the delegated-only tools below; `core tools` means OAuth only. Call this first when unsure which account can do what.

### Trash & Threads

| Tool | Extra parameters |
|------|------------------|
| `trash_gmail_message` / `untrash_gmail_message` | `message_id` (string, required) |
| `trash_gmail_thread` / `untrash_gmail_thread` | `thread_id` (string, required) |
| `list_gmail_threads` | `query` (string), `max_results` (int, default 20), `label_ids` (array), `include_spam_trash` (bool, default false), `page_token` (string) |
| `modify_gmail_thread_labels` | `thread_id` (string, required), `add_label_ids` (array), `remove_label_ids` (array) |

### Drafts

| Tool | Extra parameters |
|------|------------------|
| `list_gmail_drafts` | `query` (string), `max_results` (int, default 20), `include_spam_trash` (bool, default false) |
| `get_gmail_draft` / `delete_gmail_draft` / `send_gmail_draft` | `draft_id` (string, required) |
| `update_gmail_draft` | `draft_id` (string, required), `subject`, `body`, `body_format` ("plain"/"html", default "plain"), `to`, `cc`, `bcc`, `thread_id` (all strings, optional) |

### Lookups

| Tool | Extra parameters |
|------|------------------|
| `get_gmail_label` | `label_id` (string, required) |
| `get_gmail_filter` | `filter_id` (string, required) |
| `get_gmail_profile` | none |

### Push Notifications

| Tool | Extra parameters |
|------|------------------|
| `watch_gmail_mailbox` | `topic_name` (string, required, a Pub/Sub topic), `label_ids` (array), `label_filter_behavior` ("include"/"exclude") |
| `stop_gmail_mailbox_watch` | none |

### Settings (read + write, any account)

| Tool | Extra parameters |
|------|------------------|
| `get_gmail_vacation_settings` | none |
| `update_gmail_vacation_settings` | `enable_auto_reply` (bool, required), `response_body_plain_text`, `response_body_html`, `response_subject` (strings), `restrict_to_contacts`, `restrict_to_domain` (bools), `start_time`, `end_time` (RFC3339 strings) |
| `get_gmail_imap_settings` | none |
| `update_gmail_imap_settings` | `enabled` (bool, required), `auto_expunge` (bool), `expunge_behavior` ("archive"/"trash"/"deleteForever"), `max_folder_size` (int) |
| `get_gmail_pop_settings` | none |
| `update_gmail_pop_settings` | `access_window` ("disabled"/"fromNowOn"/"allMail", required), `disposition` ("leaveInInbox"/"archive"/"trash"/"markRead", required) |
| `get_gmail_language_settings` | none |
| `update_gmail_language_settings` | `display_language` (string, required, e.g. "en-GB") |
| `get_gmail_auto_forwarding` | none |
| `list_gmail_forwarding_addresses` | none |
| `get_gmail_forwarding_address` | `forwarding_email` (string, required) |
| `list_gmail_send_as` | none |
| `get_gmail_send_as` | `send_as_email` (string, required) |

### Delegated-Only Writes (gmail.settings.sharing)

These seven tools only work for accounts that `list_gmail_accounts` marks `all tools` (a Workspace account served by a delegated service account; `DWD_ALLOWED_DOMAINS` on the server decides which domains qualify). On any other account they refuse before calling Google -- report that to the user instead of retrying.

| Tool | Extra parameters |
|------|------------------|
| `update_gmail_auto_forwarding` | `enabled` (bool, required), `email_address` (string, must be a verified forwarding address), `disposition` ("leaveInInbox"/"archive"/"trash"/"markRead") |
| `create_gmail_forwarding_address` / `delete_gmail_forwarding_address` | `forwarding_email` (string, required) |
| `create_gmail_send_as` | `send_as_email` (string, required), `display_name`, `reply_to_address`, `signature` (strings), `treat_as_alias` (bool) |
| `update_gmail_send_as` | `send_as_email` (string, required), `display_name`, `reply_to_address`, `signature` (strings), `is_default`, `treat_as_alias` (bools) |
| `delete_gmail_send_as` / `verify_gmail_send_as` | `send_as_email` (string, required) |

---

## Tips

**Search syntax**: The `query` parameter uses standard Gmail search syntax (`from:`, `to:`, `subject:`, `is:unread`, `has:attachment`, `newer_than:7d`, `label:`, `category:`, `rfc822msgid:`).

### Threading and Replies
- Every search result returns both a `message_id` and a `thread_id`. Use the thread_id to read the full conversation.
- To reply: pass `thread_id`, `in_reply_to` (the Message-ID header of the message you are replying to), and `references` (chain of all Message-IDs in the thread, space-separated). Prefix the subject with `Re:` followed by a space.
- The `in_reply_to` and `references` values come from the `Message-ID` header returned by `get_gmail_message_content`.
- The `in_reply_to` field in this MCP server is known to be unreliable. Always provide `thread_id` and `references` for threading -- those are the fields Gmail actually uses.

### Pagination
- `search_gmail_messages` returns a `next_page_token` when more results exist. Pass it as `page_token` in the next call.
- Unpaginated search results are incomplete -- always check for and follow `next_page_token` when you need full coverage.

### Batch Operations
- Batch tools (`get_gmail_messages_content_batch`, `get_gmail_threads_content_batch`, `batch_modify_gmail_message_labels`) max out at 25 items per call to avoid SSL exhaustion.
- For larger sets, make multiple batch calls.

### Label IDs
- System labels use uppercase IDs: `INBOX`, `SENT`, `TRASH`, `SPAM`, `DRAFT`, `UNREAD`, `STARRED`, `IMPORTANT`.
- Custom labels have generated IDs (e.g., `Label_123`). Use `list_gmail_labels` to discover them.
- Use label IDs (not names) in `modify_gmail_message_labels`, `batch_modify_gmail_message_labels`, and filter actions.

### Drafts vs Send
- Use `draft_gmail_message` when you want the user to review before sending. It supports `include_signature` (auto-appends Gmail signature) and `quote_original` (includes quoted reply text).
- Use `send_gmail_message` for immediate delivery.

### Attachments
- To find attachments, read the message with `get_gmail_message_content` -- attachment IDs are listed in the response.
- Download with `get_gmail_attachment_content` using both the message_id and attachment_id.
- When sending/drafting, attachments can be specified as file paths (auto-encoded) or pre-encoded base64 content (standard base64, not urlsafe).
