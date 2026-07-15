"""Inserty HWP MCP server.

Exposes a small, safe surface of the existing Inserty HWP-editing backend as
MCP tools. Reading is done via HDML markup extraction; editing is applied
through the backend's track-changes edit lifecycle
(``setDiffMode`` → ``prepare_context`` → ``execute_delta`` → ``finalize_edits``)
so that every change lands as a *track change* the user accepts or rejects
inside the Inserty application. Edits are NEVER auto-accepted here.

Transports:
- ``stdio`` (default) for local CLI clients (Claude Code, Codex CLI, Gemini
  CLI, Claude Desktop).
- ``streamable-http`` (opt-in via ``--http`` / ``INSERTY_MCP_TRANSPORT=http``)
  so the server can be exposed to web clients that the user hosts/tunnels
  themselves.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .backend import BackendError, get_client

mcp = FastMCP(
    "inserty-hwp",
    instructions=(
        "Tools to read and edit the user's currently open HWP (Hangul Word "
        "Processor) document via the Inserty backend.\n\n"
        "Typical flow: list_documents() → select_document(index) → "
        "read_document_markup() to see element ids → apply_edits([...]) to "
        "propose changes. Edits are applied as TRACK CHANGES; the user accepts "
        "or rejects them inside Inserty (that is the approval gate). Element "
        "ids in edits are BARE integers (e.g. 4), never prefixed (never "
        "'cell-4')."
    ),
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _err(exc: Exception) -> Dict[str, Any]:
    """Normalize a backend/transport failure into a structured tool result."""
    return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# read-only tools
# ---------------------------------------------------------------------------

@mcp.tool(
    annotations=ToolAnnotations(
        title="List open HWP documents",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def list_documents() -> Dict[str, Any]:
    """List the HWP (and any Word/Excel) documents currently open on the user's
    machine.

    Returns a dict with ``documents``: a list of entries containing at least
    ``index``, ``name``, ``path``, and ``type`` ("hwp"). Use the ``index`` of an
    HWP entry with ``select_document`` before reading or editing.
    """
    try:
        return get_client().call("getOpenDocuments", {})
    except BackendError as exc:
        return _err(exc)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Select the active HWP document",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
def select_document(index: int) -> Dict[str, Any]:
    """Bind subsequent read/edit calls to the open HWP document at ``index``.

    ``index`` is the ``index`` field from ``list_documents`` (HWP documents
    only). This only changes which document is targeted; it does not modify any
    document content.
    """
    try:
        return get_client().call("selectDocument", {"type": "hwp", "index": int(index)})
    except BackendError as exc:
        return _err(exc)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Read current HWP document as HDML markup",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def read_document_markup(
    start_page: Optional[int] = None,
    end_page: Optional[int] = None,
) -> Dict[str, Any]:
    """Return the current HWP document as HDML markup so the model can see the
    element ids it can target when editing.

    HDML is HTML-like: paragraphs are ``<p id="1">...</p>`` and table cells are
    ``<td id="2">...</td>``. The integer ``id`` on each element is what you pass
    (as a BARE integer) in ``apply_edits``.

    ``start_page`` / ``end_page`` are 1-based and optional. If omitted, the
    backend reads the page at the current cursor position. IMPORTANT: when you
    later call ``apply_edits``, pass the SAME ``start_page`` / ``end_page`` you
    read here so the element ids line up.
    """
    params: Dict[str, Any] = {}
    if start_page is not None:
        params["start_page"] = int(start_page)
    if end_page is not None:
        params["end_page"] = int(end_page)
    try:
        result = get_client().call("extract_hdml", params)
    except BackendError as exc:
        return _err(exc)
    # Return the markup and success flag; drop the large id_to_pos index (the
    # ids the model needs are already embedded in the HDML markup).
    return {
        "success": bool(result.get("success", False)),
        "hdml": result.get("hdml"),
        "error": result.get("error"),
    }


# ---------------------------------------------------------------------------
# editing tools (apply as track changes; user approves in Inserty)
# ---------------------------------------------------------------------------

@mcp.tool(
    annotations=ToolAnnotations(
        title="Apply edits as track changes",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def apply_edits(
    edits: List[Dict[str, Any]],
    start_page: Optional[int] = None,
    end_page: Optional[int] = None,
    instruction: str = "",
) -> Dict[str, Any]:
    """Apply a batch of edit operations to the current HWP document AS TRACK
    CHANGES. The changes appear as pending track-changes in Inserty; the human
    then accepts or rejects them in Inserty's UI — that is the approval gate.
    This tool does NOT accept the changes.

    ``edits`` is a list of edit-operation objects. Each object targets an
    element by its BARE integer id (from ``read_document_markup``) and names an
    operation. Two equivalent shapes are accepted per op:

    - ``{"method_type": "<op>", "id": <int>, "new_text": "<text>"}``
    - ``{"action": "edit_document", "id": <int>, "content": "<text>",
       "metadata": {"operation": "<op>"}}``

    ``<op>`` (``method_type`` / ``metadata.operation``) is one of, e.g.:
    replace_cell_content, replace_paragraph, append_paragraph, delete_paragraph,
    replace_list, append_list, delete_list, delete_cell_content,
    append_table_row, replace_table_row, delete_table_row, create_table,
    delete_table, delete_textbox, insert_footnote, replace_footnote,
    delete_footnote, apply_para_style, apply_charshape, add_footnote,
    add_endnote, add_source_ref, line_break, find_and_replace_in_paragraph,
    find_and_replace_all.

    ``start_page`` / ``end_page`` should match what you passed to
    ``read_document_markup`` so element ids resolve to the same elements.
    ``instruction`` is an optional short description of the intent (used by the
    backend for context/telemetry only).

    Returns a summary: the context id, how many ops were applied/edited/failed,
    and a per-op result list surfacing any backend validation errors.
    """
    if not isinstance(edits, list) or not edits:
        return {"success": False, "error": "edits must be a non-empty list of edit-operation objects."}
    if not all(isinstance(e, dict) for e in edits):
        return {"success": False, "error": "each edit must be an object (dict)."}

    client = get_client()

    try:
        # 1. Enable diff mode so edits are recorded as track changes.
        client.call("setDiffMode", {"enabled": True, "skipTrackChanges": False})

        # 2. Build the edit context (segment registry + context id) for the page
        #    range. execute_delta relies on the registry prepare_context builds.
        prep_params: Dict[str, Any] = {
            "prompt": instruction or "Apply edits requested via MCP.",
            "docType": "hwp",
        }
        if start_page is not None:
            prep_params["startPage"] = int(start_page)
        if end_page is not None:
            prep_params["endPage"] = int(end_page)

        context = client.call("prepare_context", prep_params)
        if not context.get("success"):
            return {"success": False, "error": context.get("error") or "prepare_context failed."}

        context_id = context.get("context_id")
        if not context_id:
            return {"success": False, "error": "prepare_context did not return a context_id."}

        # 3. Apply each op via execute_delta, scoped to this context.
        results: List[Dict[str, Any]] = []
        applied = 0
        edited = 0
        errors = 0
        for index, edit in enumerate(edits):
            delta = dict(edit)
            delta["context_id"] = context_id
            op_name = delta.get("method_type") or (delta.get("metadata") or {}).get("operation") or delta.get("action")
            try:
                res = client.call("execute_delta", delta)
            except BackendError as exc:
                errors += 1
                results.append({"index": index, "id": edit.get("id") or edit.get("block_id"),
                                "op": op_name, "success": False, "error": str(exc)})
                continue
            ok = bool(res.get("success"))
            if ok:
                applied += 1
                if res.get("edited"):
                    edited += 1
            else:
                errors += 1
            results.append({
                "index": index,
                "id": edit.get("id") or edit.get("block_id"),
                "op": op_name,
                "success": ok,
                "edited": bool(res.get("edited")),
                "error": res.get("error"),
            })

        # 4. Finalize (records edit history). Does NOT accept the track changes.
        finalize = client.call(
            "finalize_edits",
            {"contextId": context_id, "editsCount": applied, "messages": []},
        )

        return {
            "success": errors == 0,
            "context_id": context_id,
            "total": len(edits),
            "applied": applied,
            "edited": edited,
            "errors": errors,
            "results": results,
            "finalize_message": finalize.get("message"),
            "note": (
                "Edits applied as TRACK CHANGES. The user accepts or rejects them "
                "in Inserty (or via accept_all_changes / reject_all_changes)."
            ),
        }
    except BackendError as exc:
        return _err(exc)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Accept all pending track changes",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
def accept_all_changes() -> Dict[str, Any]:
    """Accept ALL pending track changes in the current HWP document.

    Normally the user does this inside Inserty's UI — this tool is provided so a
    client can finalize programmatically when appropriate. Prefer letting the
    human approve in Inserty.
    """
    try:
        return get_client().call("trackChanges:applyAll", {})
    except BackendError as exc:
        return _err(exc)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Reject all pending track changes",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    )
)
def reject_all_changes() -> Dict[str, Any]:
    """Reject (roll back) ALL pending track changes in the current HWP document.

    Normally the user does this inside Inserty's UI — this tool is provided so a
    client can roll back programmatically when appropriate.
    """
    try:
        return get_client().call("trackChanges:rejectAll", {})
    except BackendError as exc:
        return _err(exc)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Save the current HWP document",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
def save_document() -> Dict[str, Any]:
    """Save the current HWP document to its file on disk."""
    try:
        return get_client().call("save", {})
    except BackendError as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def _resolve_transport(args: argparse.Namespace) -> str:
    """Pick the FastMCP transport from CLI flags then environment."""
    if args.http:
        return "streamable-http"
    if args.transport:
        raw = args.transport
    else:
        raw = os.environ.get("INSERTY_MCP_TRANSPORT", "stdio")
    raw = raw.strip().lower()
    if raw in ("http", "streamable-http", "streamable_http"):
        return "streamable-http"
    if raw == "sse":
        return "sse"
    return "stdio"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="inserty-mcp",
        description="MCP server exposing the Inserty HWP-editing backend.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "streamable-http", "sse"],
        default=None,
        help="Transport to use. Default: stdio (or $INSERTY_MCP_TRANSPORT).",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Shorthand for --transport streamable-http (for web/remote clients).",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("INSERTY_MCP_HOST", "127.0.0.1"),
        help="Bind host for http/sse transport (default 127.0.0.1 or $INSERTY_MCP_HOST).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("INSERTY_MCP_PORT", "8765")),
        help="Bind port for http/sse transport (default 8765 or $INSERTY_MCP_PORT).",
    )
    args = parser.parse_args()

    transport = _resolve_transport(args)

    if transport in ("streamable-http", "sse"):
        # FastMCP reads host/port from its settings.
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        print(
            f"[inserty-mcp] starting {transport} transport on http://{args.host}:{args.port}",
            file=sys.stderr,
            flush=True,
        )

    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
