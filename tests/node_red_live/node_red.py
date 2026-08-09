from __future__ import annotations

import copy
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class CampaignError(ValueError):
    """Raised when a live campaign would violate its safety contract."""


@dataclass(frozen=True)
class ReadLedger:
    rounds: int = 3
    max_reads: int = 180
    max_seconds: float = 60.0
    cadence_seconds: float = 1.0
    max_in_flight: int = 1

    def __post_init__(self) -> None:
        if (
            self.rounds != 3
            or self.max_reads != 180
            or self.max_seconds != 60
            or self.cadence_seconds != 1
            or self.max_in_flight != 1
        ):
            raise CampaignError("live campaign budget is fixed at three rounds, 180 reads, 60 seconds, one in flight")


class BoundedReadLedger:
    """Monotonic ledger that prevents accidental unbounded live polling."""

    def __init__(self, budget: ReadLedger | None = None, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.budget = budget or ReadLedger()
        self._clock = clock
        self.started_at = self._clock()
        self.request_count = 0
        self.round = 0
        self.in_flight = 0

    def begin_round(self) -> None:
        if self.round >= self.budget.rounds:
            raise CampaignError("read round budget exhausted")
        self.round += 1

    def begin(self) -> None:
        if self.in_flight >= self.budget.max_in_flight:
            raise CampaignError("concurrent read budget exceeded")
        if self.request_count >= self.budget.max_reads:
            raise CampaignError("compiled-block read budget exhausted")
        if self._clock() - self.started_at > self.budget.max_seconds:
            raise CampaignError("live campaign time budget exhausted")
        self.in_flight += 1
        self.request_count += 1

    def finish(self) -> None:
        if self.in_flight <= 0:
            raise CampaignError("read completion without a read")
        self.in_flight -= 1


def validate_flow(flow: Sequence[Mapping[str, Any]]) -> None:
    nodes = list(flow)
    tabs = [node for node in nodes if node.get("type") == "tab"]
    if not tabs or not all(bool(tab.get("disabled")) for tab in tabs):
        raise CampaignError("Node-RED flow must be disabled before import")
    forbidden = ("write", "inject-register", "modbus-read", "scan", "discover", "repeat")
    for node in nodes:
        node_type = str(node.get("type", "")).lower()
        node_name = str(node.get("name", "")).lower()
        if any(token in node_type or token in node_name for token in forbidden):
            if node_type == "inject" and node.get("repeat", "") in ("", None) and not node.get("once", False):
                continue
            raise CampaignError(f"unsafe Node-RED node: {node.get('type', '')}")
        if node_type == "inject" and (node.get("repeat", "") or node.get("crontab", "") or node.get("once", False)):
            raise CampaignError("Node-RED trigger must be manual one-shot")
        if node_type == "modbus-flex-getter":
            try:
                fc = int(node.get("fc", node.get("functionCode", 0)))
            except (TypeError, ValueError) as exc:
                raise CampaignError("Node-RED read node has invalid function code") from exc
            if fc not in {1, 2, 3, 4}:
                raise CampaignError("only Modbus FC01-FC04 reads are permitted")
            host = node.get("tcpHost", node.get("host"))
            if host is not None and str(host) not in {"${MODBUS_HOST}", "127.0.0.1", "localhost", "::1"}:
                raise CampaignError("Node-RED Modbus target must be loopback")


@dataclass
class NodeRedRuntime:
    which: Callable[[str], str | None] = shutil.which
    cli: str | None = None

    def preflight(self, flow: Sequence[Mapping[str, Any]], *, endpoint: str | None = None) -> dict[str, Any]:
        if flow:
            validate_flow(flow)
        executable = self.cli or self.which("node-red")
        if not executable:
            return {"status": "blocked", "issue_codes": ["node-red-runtime-unavailable"], "runtime": "unavailable"}
        return {"status": "ready", "issue_codes": [], "runtime": executable}


class NodeRedAdminClient:
    """Small loopback-only client for a disposable Node-RED test runtime."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1880",
        *,
        timeout: float = 5.0,
        opener=urlopen,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[float], None] = time.sleep,
    ) -> None:
        if not base_url.startswith(("http://127.0.0.1:", "http://localhost:", "http://[::1]:")):
            raise CampaignError("Node-RED admin URL must use loopback HTTP")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener
        self._clock = clock
        self._wait = wait

    def _request(self, path: str, *, method: str = "GET", payload: Any = None) -> Any:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read()
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise CampaignError(f"Node-RED admin request failed: {exc}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CampaignError("Node-RED admin returned invalid JSON") from exc

    def flows(self) -> list[dict[str, Any]]:
        value = self._request("/flows")
        if isinstance(value, Mapping):
            value = value.get("flows")
        if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
            raise CampaignError("Node-RED admin returned an invalid flow list")
        return [dict(item) for item in value]

    def deploy(self, flow: Sequence[Mapping[str, Any]]) -> None:
        self._request("/flows", method="POST", payload=[dict(node) for node in flow])

    def trigger(self, node_id: str) -> None:
        if not node_id or not all(character.isalnum() for character in node_id):
            raise CampaignError("Node-RED inject ID is invalid")
        self._request(f"/inject/{node_id}", method="POST")

    def run_flow(
        self,
        flow: Sequence[Mapping[str, Any]],
        *,
        capture_path: Path,
        environment: Mapping[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        validate_flow(flow)
        original = self.flows()
        deployed = copy.deepcopy([dict(node) for node in flow])
        tabs = [node for node in deployed if node.get("type") == "tab"]
        injects = [node for node in deployed if node.get("type") == "inject"]
        if len(tabs) != 1 or len(injects) != 1:
            raise CampaignError("Node-RED campaign requires one tab and one manual start button")
        tabs[0]["disabled"] = False
        tabs[0]["env"] = [
            {"name": name, "value": str(value), "type": "str"}
            for name, value in sorted(environment.items())
        ]
        before_mtime = capture_path.stat().st_mtime_ns if capture_path.exists() else None
        try:
            self.deploy(deployed)
            self.trigger(str(injects[0]["id"]))
            deadline = self._clock() + timeout_seconds
            while self._clock() < deadline:
                if capture_path.is_file():
                    current_mtime = capture_path.stat().st_mtime_ns
                    if before_mtime is None or current_mtime != before_mtime:
                        try:
                            capture = json.loads(capture_path.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            capture = None
                        if isinstance(capture, dict):
                            expected = set(capture.get("expected_request_ids", ()))
                            observed = {
                                str(sample.get("request_id"))
                                for sample in capture.get("samples", ())
                                if isinstance(sample, Mapping) and sample.get("request_id")
                            }
                            if expected and expected <= observed:
                                return capture
                self._wait(0.1)
            raise CampaignError("Node-RED read plan did not complete before the timeout")
        finally:
            self.deploy(original)
