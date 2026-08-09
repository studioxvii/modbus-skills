"""Production support for the optional, loopback-only Node-RED campaign."""

from __future__ import annotations

import copy
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class CampaignError(ValueError):
    """Raised when a live campaign would violate its safety contract."""


class SimulatorError(RuntimeError):
    """Raised when the simulator boundary is unavailable or invalid."""


_HOST_PLACEHOLDER = re.compile(r"^\$\{MODBUS(?:_[A-Z0-9_]+)?_HOST\}$")


def validate_flow(flow: Sequence[Mapping[str, Any]]) -> None:
    nodes = list(flow)
    tabs = [node for node in nodes if node.get("type") == "tab"]
    if not tabs or not all(bool(tab.get("disabled")) for tab in tabs):
        raise CampaignError("Node-RED flow must be disabled before import")
    clients: dict[str, Mapping[str, Any]] = {}
    for node in nodes:
        if str(node.get("type", "")).lower() != "modbus-client":
            continue
        identifier = str(node.get("id", ""))
        if not identifier or identifier in clients:
            raise CampaignError("Node-RED Modbus client IDs must be unique")
        clients[identifier] = node
    forbidden_types = ("inject-register", "modbus-read", "scan", "discover", "repeat")
    for node in nodes:
        node_type = str(node.get("type", "")).lower()
        node_name = str(node.get("name", "")).lower()
        unsafe_type = any(token in node_type for token in forbidden_types) or (
            "modbus" in node_type and "write" in node_type
        )
        unsafe_name = any(
            token in node_name for token in ("scan", "discover", "write register")
        )
        if unsafe_type or unsafe_name:
            if (
                node_type == "inject"
                and node.get("repeat", "") in ("", None)
                and not node.get("once", False)
            ):
                continue
            raise CampaignError(f"unsafe Node-RED node: {node.get('type', '')}")
        if node_type == "inject" and (
            node.get("repeat", "") or node.get("crontab", "") or node.get("once", False)
        ):
            raise CampaignError("Node-RED trigger must be manual one-shot")
        if node_type != "modbus-flex-getter":
            continue
        raw_codes = node.get("modbusSkillsAllowedFunctionCodes")
        if raw_codes is None:
            raw_codes = [node.get("fc", node.get("functionCode", 0))]
        if not isinstance(raw_codes, list) or not raw_codes:
            raise CampaignError("Node-RED read node has no bounded function codes")
        try:
            codes = {int(value) for value in raw_codes}
        except (TypeError, ValueError) as exc:
            raise CampaignError("Node-RED read node has invalid function code") from exc
        if not codes <= {1, 2, 3, 4}:
            raise CampaignError("only Modbus FC01-FC04 reads are permitted")
        server_id = str(node.get("server", ""))
        client = clients.get(server_id)
        if server_id and client is None:
            raise CampaignError("Node-RED read node references an unknown Modbus client")
        host_source = client if client is not None else node
        host = host_source.get("tcpHost", host_source.get("host"))
        if (
            host is not None
            and str(host) not in {"127.0.0.1", "localhost", "::1"}
            and _HOST_PLACEHOLDER.fullmatch(str(host)) is None
        ):
            raise CampaignError("Node-RED Modbus target must be loopback")


def planned_requests(flow: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    sequencers = [
        node
        for node in flow
        if node.get("type") == "function"
        and node.get("name") == "Run bounded read plan"
        and isinstance(node.get("modbusSkillsBlocks"), list)
    ]
    if len(sequencers) != 1:
        raise CampaignError("generated Node-RED flow must expose one bounded read plan")
    blocks = sequencers[0]["modbusSkillsBlocks"]
    if not blocks or not all(isinstance(block, Mapping) for block in blocks):
        raise CampaignError("generated Node-RED read plan is empty or invalid")
    return [dict(block) for block in blocks]


@dataclass
class NodeRedRuntime:
    which: Callable[[str], str | None] = shutil.which
    cli: str | None = None
    wait: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    def preflight(self, flow: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if flow:
            validate_flow(flow)
        executable = self.cli or self.which("node-red")
        if not executable:
            return {
                "status": "blocked",
                "issue_codes": ["node-red-runtime-unavailable"],
                "runtime": "unavailable",
            }
        return {"status": "ready", "issue_codes": [], "runtime": executable}


class NodeRedAdminClient:
    """Small loopback-only client for a disposable Node-RED runtime."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1880",
        *,
        timeout: float = 5.0,
        opener=urlopen,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[float], None] = time.sleep,
    ) -> None:
        if not base_url.startswith(
            ("http://127.0.0.1:", "http://localhost:", "http://[::1]:")
        ):
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

    def _wait_for_capture(
        self, capture_path: Path, *, previous_mtime: int | None, timeout_seconds: float
    ) -> dict[str, Any]:
        deadline = self._clock() + timeout_seconds
        while self._clock() < deadline:
            try:
                current_mtime = capture_path.stat().st_mtime_ns
            except FileNotFoundError:
                current_mtime = None
            if current_mtime is not None and current_mtime != previous_mtime:
                try:
                    capture = json.loads(capture_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise CampaignError("Node-RED capture sink wrote invalid JSON") from exc
                if not isinstance(capture, dict):
                    raise CampaignError("Node-RED capture sink wrote a non-object capture")
                return capture
            self._wait(0.1)
        raise CampaignError("Node-RED read plan did not drain before the timeout")

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
            raise CampaignError(
                "Node-RED campaign requires one tab and one manual start button"
            )
        tabs[0]["disabled"] = False
        tabs[0]["env"] = [
            {"name": name, "value": str(value), "type": "str"}
            for name, value in sorted(environment.items())
        ]
        try:
            previous_mtime = capture_path.stat().st_mtime_ns
        except FileNotFoundError:
            previous_mtime = None
        try:
            self.deploy(deployed)
            self.trigger(str(injects[0]["id"]))
            try:
                return self._wait_for_capture(
                    capture_path,
                    previous_mtime=previous_mtime,
                    timeout_seconds=timeout_seconds,
                )
            except CampaignError:
                injects[0]["payload"] = json.dumps({"action": "cancel"})
                injects[0]["payloadType"] = "json"
                self.deploy(deployed)
                self.trigger(str(injects[0]["id"]))
                raise
        finally:
            self.deploy(original)


@dataclass(frozen=True)
class SimulatorReady:
    ready: bool
    modbus_port: int
    fleet_size: int
    modbus_host: str


def _as_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SimulatorError(f"simulator returned an invalid {label}") from exc
    if result < 0:
        raise SimulatorError(f"simulator returned an invalid {label}")
    return result


class SimulatorClient:
    """Bounded JSON client for the generator simulator control-plane APIs."""

    def __init__(self, base_url: str, *, timeout: float = 3.0, opener=urlopen) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener

    def _request_json(
        self, path: str, *, method: str = "GET", payload: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        if not path.startswith("/"):
            raise SimulatorError("simulator API paths must be absolute")
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read()
                status = getattr(response, "status", 200)
        except HTTPError as exc:
            try:
                raw = exc.read()
                detail = json.loads(raw.decode("utf-8")) if raw else {}
            except (OSError, ValueError):
                detail = {}
            message = (
                detail.get("error", f"HTTP {exc.code}")
                if isinstance(detail, Mapping)
                else f"HTTP {exc.code}"
            )
            raise SimulatorError(str(message)) from exc
        except (OSError, URLError, TimeoutError) as exc:
            raise SimulatorError(f"simulator unavailable: {exc}") from exc
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SimulatorError(
                f"simulator returned non-JSON response (HTTP {status})"
            ) from exc
        if not isinstance(value, dict):
            raise SimulatorError("simulator returned a non-object JSON response")
        return value

    def readiness(self) -> dict[str, Any]:
        value = self._request_json("/api/ready")
        for key in ("ready", "modbus_port", "num_generators"):
            if key not in value:
                raise SimulatorError(f"simulator readiness omitted {key}")
        return value

    def require_ready(self, expected_fleet: int) -> SimulatorReady:
        value = self.readiness()
        ready = bool(value.get("ready"))
        fleet_size = _as_int(value.get("num_generators"), "fleet size")
        port = _as_int(value.get("modbus_port"), "Modbus port")
        if not ready:
            raise SimulatorError("simulator is not ready")
        if fleet_size != expected_fleet:
            raise SimulatorError(
                f"simulator fleet size {fleet_size} does not match {expected_fleet}"
            )
        if not 1 <= port <= 65535:
            raise SimulatorError("simulator reported an invalid Modbus port")
        return SimulatorReady(
            ready, port, fleet_size, str(value.get("modbus_host", "127.0.0.1"))
        )

    def configure_fleet(
        self, fleet_size: int, *, modbus_port: int | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "size_counts": {
                "500": fleet_size,
                "1000": 0,
                "1500": 0,
                "2000": 0,
                "2500": 0,
            }
        }
        if modbus_port is not None:
            payload["modbus_port"] = modbus_port
        return self._request_json("/api/startup", method="POST", payload=payload)

    def fleet_summary(self) -> dict[str, Any]:
        return self._request_json("/api/fleet/summary")

    def registers(self, unit_id: int) -> dict[str, Any]:
        if unit_id < 1:
            raise SimulatorError("unit_id must be positive")
        return self._request_json(f"/api/generators/{unit_id}/registers")

    def run_scenario(self, scenario_id: str) -> dict[str, Any]:
        if scenario_id != "fault-and-reset":
            raise SimulatorError("only the documented fault-and-reset scenario is allowed")
        return self._request_json(
            "/api/scenarios/run", method="POST", payload={"scenario_id": scenario_id}
        )

    def stop_scenario(self) -> dict[str, Any]:
        return self._request_json("/api/scenarios/stop", method="POST", payload={})


__all__ = [
    "CampaignError",
    "NodeRedAdminClient",
    "NodeRedRuntime",
    "SimulatorClient",
    "SimulatorError",
    "SimulatorReady",
    "planned_requests",
    "validate_flow",
]
