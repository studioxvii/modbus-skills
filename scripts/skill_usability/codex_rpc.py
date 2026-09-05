"""Bounded stdio transport for actual Codex test sessions.

Protocol reference: https://learn.chatgpt.com/docs/app-server
Permission profiles: https://learn.chatgpt.com/docs/permissions
"""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import tempfile
import threading
import time
from typing import Any


class RpcError(RuntimeError):
    pass


class CodexRpc:
    def __init__(self, executable: str, *, max_bytes: int = 2_000_000):
        self.max_bytes = max_bytes
        self.bytes_read = 0
        self.messages: queue.Queue = queue.Queue()
        self.sequence = 0
        self.pending: list[dict] = []
        self.stderr = tempfile.TemporaryFile()
        self.process = subprocess.Popen(
            [executable, "app-server", "--stdio", "-c", "features.apps=false", "-c", "features.connectors=false"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
            start_new_session=True,
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(self.max_bytes + 1)
                if not line:
                    self.messages.put(RpcError("codex-server-exited"))
                    return
                self.bytes_read += len(line)
                if self.bytes_read > self.max_bytes:
                    self.messages.put(RpcError("codex-output-budget-exceeded"))
                    return
                self.messages.put(json.loads(line))
        except (ValueError, OSError) as exc:
            self.messages.put(RpcError(type(exc).__name__))

    def send(self, message: dict):
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        self.process.stdin.flush()

    def next(self, deadline: float) -> dict:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RpcError("codex-time-budget-exceeded")
        try:
            message = self.messages.get(timeout=remaining)
        except queue.Empty as exc:
            raise RpcError("codex-time-budget-exceeded") from exc
        if isinstance(message, Exception):
            raise message
        if "method" in message and "id" in message:
            # Tests must never solicit an actual operator or approve an
            # unexpected server-side action. User dialogue is ordinary input.
            self.send({"id": message["id"], "error": {"code": -32601, "message": "unsupported test-session request"}})
            raise RpcError("codex-unexpected-server-request")
        return message

    def call(self, method: str, params: dict, *, deadline: float) -> dict[str, Any]:
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self.next(deadline)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RpcError(f"codex-rpc-error:{method}:{message['error'].get('message', 'unknown')}")
                return message.get("result", {})
            self.pending.append(message)

    def close(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()
        self.stderr.close()
