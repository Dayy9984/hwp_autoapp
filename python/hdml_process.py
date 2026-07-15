# -*- coding: utf-8 -*-
"""
HDML Child Process - HDML extraction/diff generation in isolated process.

Communicates with the main process via stdin/stdout JSON-RPC.
Runs HWP COM work in a separate process to avoid interfering with editing COM.
"""

import sys
import json
import traceback
from typing import Dict, Any

from services.config import config
from services.hdml_service import HDMLService
from services.diff_service import DiffService


class HDMLProcess:
    """HDML Child Process - HDML extraction/diff worker"""

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        method = request.get("method")
        params = request.get("params", {})
        request_id = request.get("id")

        try:
            if method == "ping":
                return {"id": request_id, "result": {"pong": True}}

            if method == "hdml:extractPair":
                result = self._extract_pair(params)
                return {"id": request_id, "result": result}

            if method == "hdml:generateDiff":
                result = self._generate_diff(params)
                return {"id": request_id, "result": result}

            if method == "quit":
                return {"id": request_id, "result": {"quitting": True}}

            return {"id": request_id, "error": f"Unknown method: {method}"}

        except Exception as e:
            return {
                "id": request_id,
                "error": str(e),
                "trace": traceback.format_exc(),
            }

    def _extract_pair(self, params: Dict[str, Any]) -> Dict[str, Any]:
        project_id = params.get("projectId")
        pair_id = params.get("pairId")
        template_path = params.get("templatePath")
        filled_path = params.get("filledPath")
        user_data_path = params.get("userDataPath")

        if not all([project_id, pair_id, template_path, filled_path, user_data_path]):
            return {"success": False, "error": "Missing required parameters"}

        config.set_user_data_path(user_data_path)

        def log_callback(level: str, message: str):
            print(f"[HDMLService][{level}] {message}", file=sys.stderr)

        def progress_callback(progress: float, message: str):
            event = {
                "type": "progress",
                "event": "hdml:progress",
                "data": {
                    "pairId": pair_id,
                    "progress": int(progress * 100),
                    "message": message,
                },
            }
            print(json.dumps(event, ensure_ascii=False))
            sys.stdout.flush()

        hdml_service = HDMLService(
            log_callback=log_callback,
            allow_existing_instance=False,
        )

        return hdml_service.extract_pair_hdml(
            project_id=project_id,
            pair_id=pair_id,
            template_path=template_path,
            filled_path=filled_path,
            progress_callback=progress_callback,
        )

    def _generate_diff(self, params: Dict[str, Any]) -> Dict[str, Any]:
        project_id = params.get("projectId")
        pair_id = params.get("pairId")
        user_data_path = params.get("userDataPath")

        if not all([project_id, pair_id, user_data_path]):
            return {"success": False, "error": "Missing required parameters"}

        if not config.initialized:
            config.set_user_data_path(user_data_path)

        diff_service = DiffService()
        return diff_service.generate_diff_for_pair(
            project_id=project_id,
            pair_id=pair_id,
        )

    def _send_response(self, response: Dict[str, Any]):
        try:
            json_line = json.dumps(response, ensure_ascii=False)
            print(json_line, flush=True)
        except Exception as e:
            print(f"[HDMLProcess] Response send error: {e}", file=sys.stderr)

    def run(self):
        print("[HDMLProcess] Ready, waiting for requests...", file=sys.stderr)

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[HDMLProcess] JSON decode error: {e}", file=sys.stderr)
                continue

            response = self.handle_request(request)
            self._send_response(response)

            if request.get("method") == "quit":
                print("[HDMLProcess] Quitting...", file=sys.stderr)
                break


if __name__ == "__main__":
    if sys.platform == 'win32':
        import io
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

    process = HDMLProcess()
    process.run()
