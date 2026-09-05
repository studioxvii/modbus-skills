"""Protocol tests never launch a model or depend on credentials."""
import io
import json
import queue
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from skill_usability.codex_rpc import CodexRpc, RpcError


class CodexRpcTests(unittest.TestCase):
    def rpc(self, data=b"", limit=100):
        rpc = CodexRpc.__new__(CodexRpc)
        rpc.max_bytes = limit
        rpc.bytes_read = 0
        rpc.messages = queue.Queue()
        rpc.sequence = 0
        rpc.pending = []
        rpc.process = SimpleNamespace(stdout=io.BytesIO(data), stdin=io.BytesIO())
        return rpc

    def test_output_limit_and_malformed_json_fail_closed(self):
        for data, limit in ((b"x" * 101, 100), (b"not json\n", 100)):
            rpc = self.rpc(data, limit)
            rpc._read()
            with self.assertRaises(RpcError):
                rpc.next(time.monotonic() + 1)

    def test_eof_and_expired_deadline_are_explicit(self):
        rpc = self.rpc()
        rpc._read()
        with self.assertRaisesRegex(RpcError, "server-exited"):
            rpc.next(time.monotonic() + 1)
        with self.assertRaisesRegex(RpcError, "time-budget"):
            rpc.next(time.monotonic() - 1)

    def test_unsolicited_approval_is_rejected(self):
        rpc = self.rpc()
        rpc.messages.put({"id": 20, "method": "approval"})
        with self.assertRaisesRegex(RpcError, "unexpected-server-request"):
            rpc.next(time.monotonic() + 1)
        self.assertIn("error", json.loads(rpc.process.stdin.getvalue()))

    def test_call_keeps_notifications_and_matches_response_id(self):
        rpc = self.rpc()
        rpc.messages.put({"method": "notice", "params": {}})
        rpc.messages.put({"id": 1, "result": {"ok": True}})
        self.assertEqual({"ok": True}, rpc.call("initialize", {}, deadline=time.monotonic() + 1))
        self.assertEqual("notice", rpc.pending[0]["method"])

    def test_remote_error_is_not_success(self):
        rpc = self.rpc()
        rpc.messages.put({"id": 1, "error": {"message": "bad configuration"}})
        with self.assertRaisesRegex(RpcError, "bad configuration"):
            rpc.call("initialize", {}, deadline=time.monotonic() + 1)
