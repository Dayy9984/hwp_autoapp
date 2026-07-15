# -*- coding: utf-8 -*-
"""Isolation probe for the codex SDK-backend EDIT path (NO HWP, NO COM).

Verifies that ``generate_commands_streaming`` against the codex ChatGPT backend
(``https://chatgpt.com/backend-api/codex``) returns a ``StreamingResult`` and
actually emits edit commands for a trivial fill-in-the-blank request.

This isolates the codex-edit path from HWP so a 0-command / cancel / attribute
failure can be attributed to the LLM call rather than the document pipeline.

Run:
    cd python; uv run python -m eval.probe_codex_edit

NEVER prints the codex token. Exit code 0 if commands emitted, 1 otherwise.
"""

import os
import sys
import time

# Force UTF-8 stdout/stderr (Windows cp949 cannot encode emoji the LLM emits in
# its message field; the core streaming loop logs that to stderr and a
# UnicodeEncodeError there silently turns a good stream into status='error').
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)


def main() -> int:
    from eval.codex_auth import get_codex_credentials

    token, account_id = get_codex_credentials()
    if not token:
        print("[probe] NO codex credentials (~/.codex/auth.json). Run `codex login`.")
        return 2
    print(f"[probe] codex creds loaded (account_id={'set' if account_id else 'empty'}, "
          f"token len={len(token)}).")

    from llm.streaming_client import get_streaming_client, StreamingResult, StreamingCommand

    # Default edit model = the only model the codex ChatGPT backend supports.
    model = os.environ.get("EVAL_EDIT_MODEL", "gpt-5.5")

    client = get_streaming_client(token, codex_mode=True, codex_account_id=account_id or "")
    client.model = model
    print(f"[probe] client built: codex_mode={client.codex_mode}, model={client.model}, "
          f"base_url={getattr(client.client, 'base_url', '?')}")

    # Tiny synthetic ENRICHED_HDML: a label cell + an empty value cell.
    hdml = (
        "<table>\n"
        "  <tr><td id=\"1\"><p id=\"1\">이름</p></td>"
        "<td id=\"2\"><p id=\"2\"></p></td></tr>\n"
        "</table>"
    )
    prompt = "2번 칸을 '홍길동'으로 채워줘"

    commands: list = []
    messages: list = []
    validation_errors: list = []

    def on_command(cmd: StreamingCommand) -> None:
        commands.append({
            "action": cmd.action,
            "id": cmd.id,
            "content": (cmd.content or "")[:80],
            "op": (cmd.metadata or {}).get("operation"),
        })
        if cmd.action == "message":
            messages.append(cmd.message or cmd.content)
        print(f"[probe]   on_command: action={cmd.action!r} id={cmd.id!r} "
              f"op={(cmd.metadata or {}).get('operation')!r} content={(cmd.content or '')[:60]!r}")

    def on_progress(text: str) -> None:
        pass

    def on_validation_error(tool: str, reason: str) -> None:
        validation_errors.append((tool, reason))
        print(f"[probe]   on_tool_validation_error: tool={tool!r} reason={reason!r}")

    t0 = time.time()
    res = None
    err = None
    try:
        res = client.generate_commands_streaming(
            html=hdml,
            prompt=prompt,
            on_command=on_command,
            on_progress=on_progress,
            on_tool_validation_error=on_validation_error,
            use_delta=True,
            use_html=False,
            compact_mode=True,
            enable_file_search=False,
        )
    except Exception as e:  # noqa: BLE001
        err = e
        import traceback
        traceback.print_exc()

    dt = time.time() - t0
    print(f"\n[probe] === RESULT (elapsed {dt:.1f}s) ===")
    if err is not None:
        print(f"[probe] generate_commands_streaming RAISED: {type(err).__name__}: {err}")
        return 1

    print(f"[probe] return type: {type(res).__name__}")
    print(f"[probe] isinstance StreamingResult: {isinstance(res, StreamingResult)}")
    if isinstance(res, StreamingResult):
        print(f"[probe]   success={res.success} status={res.status!r} "
              f"commands_executed={res.commands_executed} error={res.error!r}")
        print(f"[probe]   token_usage={res.token_usage}")
        print(f"[probe]   messages(in result)={res.messages[:3]}")
    edit_cmds = [c for c in commands if c["action"] not in ("thinking", "message")]
    print(f"[probe] callback commands total={len(commands)} edit_cmds={len(edit_cmds)} "
          f"messages={len(messages)} validation_errors={len(validation_errors)}")
    print(f"[probe] edit_cmds detail: {edit_cmds}")

    ok = len(edit_cmds) > 0
    print(f"\n[probe] VERDICT: codex SDK-backend edit path "
          f"{'EMITTED edit commands -> usable' if ok else 'EMITTED NO edit commands -> needs CLI fallback or deeper diag'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
