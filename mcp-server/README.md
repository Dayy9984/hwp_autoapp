# Inserty HWP MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that
exposes the existing **Inserty** HWP-editing backend as MCP tools, so external
MCP clients — Claude Code CLI, Codex CLI, Gemini CLI, Claude Desktop, and (via a
self-hosted remote transport) web clients — can **read and edit the user's open
HWP document**.

This is a standalone add-on. It does **not** change the Inserty application; it
only launches the existing backend (`python/hwp_com_process.py`) as a
subprocess and talks to it over its JSON-RPC-over-stdio protocol.

> **Approval model:** edits are applied as **track changes**. The human accepts
> or rejects them inside Inserty — that is the approval gate. This server never
> auto-accepts.

---

## Prerequisites

- **Windows** with **HWP running and a document open** (the backend drives HWP
  via Windows COM automation).
- The Inserty repository checked out, with the Python backend dependencies
  installed via [`uv`](https://docs.astral.sh/uv/). The MCP server launches the
  backend with `uv run python hwp_com_process.py` from the repo's `python/`
  directory (the same command Inserty uses in dev).
- `uv` on `PATH`.

The MCP server locates the backend automatically (it lives at
`<repo>/mcp-server`, so it uses `<repo>/python`). Override if needed:

| Env var              | Purpose                                                        |
| -------------------- | -------------------------------------------------------------- |
| `INSERTY_REPO_DIR`   | Path to the Inserty repo root (uses `<repo>/python`).          |
| `INSERTY_PYTHON_DIR` | Path directly to the backend's `python/` directory.            |
| `INSERTY_PYTHON_CMD` | Launch command before the script name (default `uv run python`).|

---

## Running

From the `mcp-server/` directory:

### stdio (default — for local CLI clients)

```bash
uv run inserty-mcp
# or
uv run python -m inserty_mcp.server
```

### HTTP (streamable-http — for remote/web clients you host yourself)

```bash
uv run inserty-mcp --http --port 8765
# or
INSERTY_MCP_TRANSPORT=http INSERTY_MCP_PORT=8765 uv run inserty-mcp
```

The endpoint is served at `http://<host>:<port>/mcp`. Reaching it from a hosted
web client (e.g. Claude web, Gemini web) requires **you** to expose/tunnel this
local endpoint (it drives HWP on your machine). Local CLIs should use stdio.

---

## Connecting clients

Replace `<repo>` with the absolute path to your Inserty checkout.

### Claude Code

```bash
claude mcp add inserty-hwp -- uv run --directory <repo>/mcp-server inserty-mcp
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "inserty-hwp": {
      "command": "uv",
      "args": ["run", "--directory", "<repo>/mcp-server", "inserty-mcp"]
    }
  }
}
```

### Codex CLI (`~/.codex/config.toml`)

```toml
[mcp_servers.inserty-hwp]
command = "uv"
args = ["run", "--directory", "<repo>/mcp-server", "inserty-mcp"]
```

### Gemini CLI (`~/.gemini/settings.json`)

```json
{
  "mcpServers": {
    "inserty-hwp": {
      "command": "uv",
      "args": ["run", "--directory", "<repo>/mcp-server", "inserty-mcp"]
    }
  }
}
```

### Generic stdio client

Spawn the process and speak MCP over its stdin/stdout:

```
command: uv
args:    ["run", "--directory", "<repo>/mcp-server", "inserty-mcp"]
```

---

## Tools

| Tool                    | Description                                                                                   |
| ----------------------- | --------------------------------------------------------------------------------------------- |
| `list_documents()`      | List open HWP documents (index, name, path, type).                                            |
| `select_document(index)`| Bind subsequent calls to the HWP document at that index.                                       |
| `read_document_markup(start_page?, end_page?)` | Return the document as HDML markup so the model can see element ids to target. |
| `apply_edits(edits, start_page?, end_page?, instruction?)` | Apply edit ops **as track changes** (does not accept them). |
| `accept_all_changes()`  | Accept all pending track changes (normally done by the user in Inserty).                       |
| `reject_all_changes()`  | Reject all pending track changes (normally done by the user in Inserty).                       |
| `save_document()`       | Save the current HWP document to disk.                                                         |

### Editing: shape of `apply_edits`

`edits` is a list of edit-operation objects. Each op targets an element by its
**bare integer id** (from `read_document_markup` — e.g. `<p id="1">`,
`<td id="2">`), never a prefixed id like `cell-4`. Two equivalent shapes work:

```jsonc
// explicit method_type
{ "method_type": "replace_cell_content", "id": 4, "new_text": "새 내용" }

// streaming-delta style
{ "action": "edit_document", "id": 4, "content": "새 내용",
  "metadata": { "operation": "replace_cell_content" } }
```

Supported operations (`method_type` / `metadata.operation`): `replace_cell_content`,
`replace_paragraph`, `append_paragraph`, `delete_paragraph`, `replace_list`,
`append_list`, `delete_list`, `delete_cell_content`, `append_table_row`,
`replace_table_row`, `delete_table_row`, `create_table`, `delete_table`,
`delete_textbox`, `insert_footnote`, `replace_footnote`, `delete_footnote`,
`apply_para_style`, `apply_charshape`, `add_footnote`, `add_endnote`,
`add_source_ref`, `line_break`, `find_and_replace_in_paragraph`,
`find_and_replace_all`.

Pass the **same** `start_page` / `end_page` to `apply_edits` that you used for
`read_document_markup`, so element ids resolve to the same elements. If omitted,
the backend uses the page at the current cursor position.

---

## Approval flow

There are two layers of approval:

1. **MCP client tool approval.** Your MCP client (Claude Code, Claude Desktop,
   etc.) prompts you before running a tool. Editing tools are annotated as
   destructive so clients can require explicit confirmation.
2. **Inserty track-changes approval (the real gate).** `apply_edits` applies
   changes as **track changes** via the backend's
   `setDiffMode → prepare_context → execute_delta → finalize_edits` lifecycle.
   The changes appear as pending track-changes in Inserty; you accept or reject
   them in Inserty's UI. `accept_all_changes` / `reject_all_changes` are
   provided for programmatic finalize/rollback, but the human normally does this
   in Inserty.

---

## Notes / limitations

- Requires a live HWP instance + Windows COM, so tool calls cannot be exercised
  end-to-end on non-Windows CI. The server code is import-clean and the JSON-RPC
  framing mirrors `python/hwp_com_process.py` exactly.
- The backend is single-threaded (STA/COM); this server serializes requests
  accordingly and filters the backend's unsolicited `progress` events off the
  response stream.
