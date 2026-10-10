# Files when the MCP server runs on another machine

For agents and people using this server through MCPHub or another remote host.

## When you need this

| Setup | What to do |
|---|---|
| Server installed on the same machine as your AI client | Nothing. Pass a file path (`path` for Gmail attachments). |
| Server runs elsewhere (MCPHub, a container, another PC) | Read on. A path from your machine does not exist on the server. |
| The file is already in the mailbox (forward, re-attach) | Nothing. Use the message or attachment ID. |

## Why

An MCP tool call is text that the model writes. MCP has no way yet for the
client to attach a file from its disk to a call (proposal: [SEP-2631 File
Objects and Transfer](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2631),
still a draft). So a file can reach a remote server only inside the call, as
base64. For a few KB the model can do that itself. A 1 MB PDF is about 1.3
million characters, which fills the model's context.

## Small files: base64 content

```json
{"attachments": [{"content": "<standard base64>", "filename": "notes.txt", "mime_type": "text/plain"}]}
```

Standard base64 (`+` and `/`), not URL-safe. `filename` is required.

## Large files: the helper script

`scripts/mcp_file_call.py` reads the file and sends it straight to the hub.
The model writes one short command and sees only the result. Python 3.9 or
newer, standard library only, one file.

### Set up once

```bash
mkdir -p ~/.local/bin
curl -fsSLo ~/.local/bin/mcp_file_call.py \
  https://raw.githubusercontent.com/Fundryi/mcp-google-workspace/main/scripts/mcp_file_call.py
```

Hub address and key, first match wins:

1. `MCPHUB_URL` (for example `https://hub.example.com`) and `MCPHUB_KEY`
   (the bearer key) environment variables;
2. an MCP server entry named `mcphub` (any case) in `~/.codex/config.toml`,
   then in `~/.claude.json`, which most setups already have.

The script never prints the key.

### Send a file

Write the tool arguments to a JSON file. Where the file goes, write
`"@file:<path>"`:

```json
{
  "user_google_email": "user@example.com",
  "to": "someone@example.com",
  "subject": "Report",
  "body": "Attached.",
  "attachments": [{"content": "@file:/home/me/report.pdf", "filename": "report.pdf", "mime_type": "application/pdf"}]
}
```

```bash
python3 ~/.local/bin/mcp_file_call.py google-workspace draft_gmail_message args.json
```

`google-workspace` is the server name in your hub. Output is `ok` or
`tool error`, then the tool's text with any base64 removed.

### Save a file

```bash
python3 ~/.local/bin/mcp_file_call.py google-workspace get_gmail_attachment_content args.json --out report.pdf
```

with `return_base64: true` in the arguments. The file goes to disk; the model
sees `[saved N bytes to report.pdf]`.

The server also keeps its own copy of every downloaded attachment for 24
hours, then deletes it (checked every hour). Uploads are never stored on the
server: they go from memory straight to Gmail.

### Limits

- The script refuses a request larger than the hub accepts (default 70 MiB,
  `--max-body` or `MCPHUB_MAX_BODY` to change). Gmail's own limit is 25 MB
  per message; Gmail rejects larger sends itself.
- It never retries. A send that timed out may still have happened.
- Exit code 0 for any tool result, 1 for network or HTTP errors, 2 for bad
  usage or a request that is too large.

### Hub settings for large files

MCPHub defaults are too small for real attachments. The hub operator sets:

| Setting | Default | For 25 MB Gmail attachments |
|---|---|---|
| JSON body size limit (`routing.jsonBodyLimit`) | `1mb` | `40mb` or more (base64 adds a third) |
| Stdio buffer limit, per server (`options.maxBufferSize`) | 10 MiB | at least 3 times the largest download |
| Tool timeout, per server (`options.timeout`) | 60000 ms | 300000 |
| Store tool call payloads | on | off, or every file lands in the hub database |

MCPHub applies the body limit before it checks the key, so do not raise it
more than you need.

## Agents without a shell

Chat apps without a terminal cannot run the script. They are limited to small
base64 files until MCP gains file transfer.
