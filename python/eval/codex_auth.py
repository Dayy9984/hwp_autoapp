# -*- coding: utf-8 -*-
"""Codex CLI credential extraction for the headless auto-eval harness.

Replicates ``electron/services/codex-detector.ts`` ``getCodexAuth()`` exactly:
reads ``~/.codex/auth.json`` (or ``$CODEX_HOME/auth.json``) and returns the
OAuth access token + ChatGPT account id used to talk to the codex backend
(``https://chatgpt.com/backend-api/codex``). This is the SAME credential the
app uses when ``auth_mode == "chatgpt"`` (no API key).

NEVER prints the token. The token is returned to the caller in-memory only.
"""

import json
import os
from typing import Optional, Tuple


def _codex_home() -> str:
    """``$CODEX_HOME`` or ``~/.codex`` (mirrors codex-detector.ts getCodexHome)."""
    return os.environ.get("CODEX_HOME") or os.path.join(
        os.path.expanduser("~"), ".codex"
    )


def _auth_json_path() -> str:
    return os.path.join(_codex_home(), "auth.json")


def get_codex_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Return ``(access_token, account_id)`` from ``~/.codex/auth.json``.

    Mirrors codex-detector.ts ``getCodexAuth()``:
      - reads ``data.tokens.access_token`` (trimmed, must be non-empty),
      - reads ``data.tokens.account_id`` (trimmed; defaults to "").

    Returns ``(None, None)`` if the file is absent, malformed, or has no token.
    NEVER raises; NEVER prints the token.
    """
    path = _auth_json_path()
    if not os.path.isfile(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return None, None

    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, dict):
        return None, None

    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        return None, None

    account_id = tokens.get("account_id") or ""
    if not isinstance(account_id, str):
        account_id = ""

    return access_token.strip(), account_id.strip()


def has_codex_credentials() -> bool:
    """True iff a usable codex OAuth token is present (no token leak)."""
    token, _ = get_codex_credentials()
    return bool(token)
