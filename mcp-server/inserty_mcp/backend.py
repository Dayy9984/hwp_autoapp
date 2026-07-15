"""JSON-RPC-over-stdio client for the Inserty HWP backend.

This module spawns ``python/hwp_com_process.py`` (the existing standalone
JSON-RPC server that drives HWP via Windows COM) and talks to it using the
*exact* newline-delimited JSON framing that server expects:

Request  (one JSON object per line, written to the child's stdin)::

    {"id": <int>, "method": "<method>", "params": {<params>}}

Response (one JSON object per line, read from the child's stdout)::

    {"id": <same id>, "result": {...}}            # success
    {"id": <same id>, "error": "<message>", ...}  # failure

The backend ALSO emits unsolicited progress events on stdout while it works::

    {"type": "progress", "event": "...", "data": {...}}

Those have no ``id`` field; the reader thread below filters them out and only
delivers the response whose ``id`` matches an in-flight request.

The framing here mirrors ``handle_request`` / ``main`` in
``python/hwp_com_process.py`` and the dev launch command used by
``electron/services/python-bridge.ts`` (``uv run python hwp_com_process.py``
with cwd = the repo's ``python`` directory).
"""

from __future__ import annotations

import atexit
import json
import os
import shlex
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional


class BackendError(RuntimeError):
    """The backend returned an ``error`` for a request, or the request could
    not be completed for a transport-level reason."""


class BackendSpawnError(BackendError):
    """The backend subprocess could not be started."""


class BackendTimeout(BackendError):
    """No response for a request arrived within the allotted time."""


def _default_python_dir() -> Path:
    """Locate the repo's ``python`` directory that holds hwp_com_process.py.

    Layout: ``<repo>/mcp-server/inserty_mcp/backend.py`` → ``<repo>/python``.
    Overridable via ``INSERTY_PYTHON_DIR`` (points directly at the python dir)
    or ``INSERTY_REPO_DIR`` (points at the repo root).
    """
    env_python_dir = os.environ.get("INSERTY_PYTHON_DIR")
    if env_python_dir:
        return Path(env_python_dir).expanduser().resolve()

    env_repo_dir = os.environ.get("INSERTY_REPO_DIR")
    if env_repo_dir:
        return (Path(env_repo_dir).expanduser().resolve() / "python")

    # backend.py -> inserty_mcp -> mcp-server -> <repo>
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "python"


def _default_launch_command() -> List[str]:
    """Command used to launch hwp_com_process.py (without the script name).

    Defaults to ``uv run python`` (the same invocation the Electron app uses in
    dev). Overridable via ``INSERTY_PYTHON_CMD`` (shell-style, e.g.
    ``"uv run python"`` or an absolute python path).
    """
    override = os.environ.get("INSERTY_PYTHON_CMD")
    if override:
        parts = shlex.split(override, posix=(os.name != "nt"))
        if parts:
            return parts
    return ["uv", "run", "python"]


class BackendClient:
    """Manages the lifecycle of one hwp_com_process.py subprocess and provides
    a synchronous, thread-safe ``call(method, params)``.

    The process is spawned lazily on the first ``call``. A single background
    thread continuously reads stdout, routes id-matched responses to waiting
    callers, and discards progress events. Requests are serialized so at most
    one is in flight at a time, which matches how the single-threaded (STA/COM)
    backend actually processes them.
    """

    #: Methods that legitimately take a long time inside HWP/COM. The default
    #: per-call timeout is generous; these get longer still.
    _LONG_METHODS = {
        "prepare_context",
        "execute_delta",
        "finalize_edits",
        "open",
        "extract_hdml",
        "extract",
        "extract_document",
        "trackChanges:applyAll",
        "trackChanges:rejectAll",
        "save",
    }

    def __init__(
        self,
        python_dir: Optional[Path] = None,
        launch_command: Optional[List[str]] = None,
        script_name: str = "hwp_com_process.py",
        default_timeout: float = 120.0,
        long_timeout: float = 600.0,
        startup_timeout: float = 60.0,
    ) -> None:
        self._python_dir = Path(python_dir) if python_dir else _default_python_dir()
        self._launch_command = list(launch_command) if launch_command else _default_launch_command()
        self._script_name = script_name
        self._default_timeout = default_timeout
        self._long_timeout = long_timeout
        self._startup_timeout = startup_timeout

        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._stderr_reader: Optional[threading.Thread] = None

        self._id_counter = 0
        self._pending: Dict[int, Dict[str, Any]] = {}
        self._pending_lock = threading.Lock()
        self._call_lock = threading.Lock()  # serialize whole request/response
        self._spawn_lock = threading.Lock()
        self._closed = False
        self._exit_reason: Optional[str] = None

        atexit.register(self.shutdown)

    # -- lifecycle ---------------------------------------------------------

    def _ensure_started(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        with self._spawn_lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            self._spawn()

    def _spawn(self) -> None:
        script_path = self._python_dir / self._script_name
        if not script_path.exists():
            raise BackendSpawnError(
                f"Backend script not found: {script_path}. Set INSERTY_PYTHON_DIR "
                f"or INSERTY_REPO_DIR to point at the Inserty repo."
            )

        argv = list(self._launch_command) + [self._script_name]
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")

        try:
            self._proc = subprocess.Popen(
                argv,
                cwd=str(self._python_dir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                encoding="utf-8",
                bufsize=1,  # line buffered
            )
        except FileNotFoundError as exc:
            raise BackendSpawnError(
                f"Failed to launch backend with {argv!r} (cwd={self._python_dir}). "
                f"Is '{self._launch_command[0]}' on PATH? Override with "
                f"INSERTY_PYTHON_CMD. Original error: {exc}"
            ) from exc
        except OSError as exc:
            raise BackendSpawnError(
                f"Failed to launch backend with {argv!r}: {exc}"
            ) from exc

        self._closed = False
        self._exit_reason = None

        self._reader = threading.Thread(
            target=self._read_stdout, name="inserty-backend-stdout", daemon=True
        )
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._read_stderr, name="inserty-backend-stderr", daemon=True
        )
        self._stderr_reader.start()

    def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stdout = self._proc.stdout
        try:
            for line in stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    # Non-JSON noise on stdout — ignore.
                    continue
                if not isinstance(message, dict):
                    continue
                # Progress events have no "id"; skip them.
                if "id" not in message:
                    continue
                msg_id = message.get("id")
                with self._pending_lock:
                    slot = self._pending.get(msg_id)
                    if slot is not None:
                        slot["response"] = message
                        slot["event"].set()
        finally:
            # stdout closed → process is exiting. Fail every waiter.
            self._fail_all("backend stdout closed (process exited)")

    def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        stderr = self._proc.stderr
        try:
            for line in stderr:
                if line:
                    sys.stderr.write(f"[inserty-backend] {line.rstrip()}\n")
                    sys.stderr.flush()
        except Exception:
            pass

    def _fail_all(self, reason: str) -> None:
        self._exit_reason = reason
        with self._pending_lock:
            for slot in self._pending.values():
                if "response" not in slot:
                    slot["error"] = reason
                    slot["event"].set()

    # -- request/response --------------------------------------------------

    def _next_id(self) -> int:
        self._id_counter += 1
        return self._id_counter

    def call(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send one JSON-RPC request and return the backend's ``result``.

        Raises :class:`BackendError` (or a subclass) on spawn failure, timeout,
        process death, or a backend-reported ``error``.
        """
        if self._closed:
            raise BackendError("Backend client has been shut down.")

        params = params or {}
        if timeout is None:
            timeout = self._long_timeout if method in self._LONG_METHODS else self._default_timeout

        # Serialize the whole exchange: the COM backend is single-threaded and
        # its progress stream is naturally associated with the current request.
        with self._call_lock:
            self._ensure_started()
            assert self._proc is not None and self._proc.stdin is not None

            req_id = self._next_id()
            event = threading.Event()
            slot: Dict[str, Any] = {"event": event}
            with self._pending_lock:
                self._pending[req_id] = slot

            request = {"id": req_id, "method": method, "params": params}
            payload = json.dumps(request, ensure_ascii=False) + "\n"

            try:
                self._proc.stdin.write(payload)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                with self._pending_lock:
                    self._pending.pop(req_id, None)
                raise BackendError(
                    f"Failed to send request to backend: {exc}. "
                    f"{self._exit_reason or ''}".strip()
                ) from exc

            got = event.wait(timeout)
            with self._pending_lock:
                self._pending.pop(req_id, None)

            if not got:
                # Timed out. If the process died, say so; otherwise report timeout.
                if self._proc.poll() is not None:
                    raise BackendError(
                        f"Backend process exited (code {self._proc.returncode}) "
                        f"while waiting for '{method}'. {self._exit_reason or ''}".strip()
                    )
                raise BackendTimeout(
                    f"Timed out after {timeout:.0f}s waiting for backend response to '{method}'."
                )

            if "error" in slot:
                raise BackendError(f"Backend request '{method}' failed: {slot['error']}")

            response = slot.get("response")
            if response is None:  # pragma: no cover - defensive
                raise BackendError(f"No response received for '{method}'.")

            if "error" in response:
                trace = response.get("trace")
                message = response["error"]
                if trace:
                    sys.stderr.write(f"[inserty-backend] trace for '{method}':\n{trace}\n")
                raise BackendError(f"Backend method '{method}' returned error: {message}")

            result = response.get("result")
            if result is None:
                # Some methods legitimately return no payload; normalize to {}.
                return {}
            return result

    # -- shutdown ----------------------------------------------------------

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                try:
                    # Ask the backend to quit cleanly (its main loop breaks on "quit").
                    proc.stdin.write(json.dumps({"id": -1, "method": "quit", "params": {}}) + "\n")
                    proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
        finally:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()


# Module-level singleton so all tools share one backend process.
_client: Optional[BackendClient] = None
_client_lock = threading.Lock()


def get_client() -> BackendClient:
    """Return the process-wide :class:`BackendClient`, creating it on demand."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = BackendClient()
    return _client
