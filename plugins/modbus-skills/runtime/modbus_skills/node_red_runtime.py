"""Production support for the optional, loopback-only Node-RED campaign."""

from __future__ import annotations

import copy
import json
import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class CampaignError(ValueError):
    """Raised when a live campaign would violate its safety contract."""


class SimulatorError(RuntimeError):
    """Raised when the simulator boundary is unavailable or invalid."""


_HOST_PLACEHOLDER = re.compile(r"^\$\{MODBUS(?:_[A-Z0-9_]+)?_HOST\}$")


def _loopback_http_url(value: str, label: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} must use loopback HTTP with an explicit port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or port is None
        or not 1 <= port <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(f"{label} must use loopback HTTP with an explicit port")
    return value.rstrip("/")


def _same_origin(url: str, expected: str) -> bool:
    try:
        actual_url = urlsplit(url)
        expected_url = urlsplit(expected)
        return (
            actual_url.scheme,
            actual_url.hostname,
            actual_url.port,
        ) == (
            expected_url.scheme,
            expected_url.hostname,
            expected_url.port,
        )
    except ValueError:
        return False


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
        if node_type == "inject":
            is_one_shot = (
                node.get("repeat", "") in ("", None)
                and node.get("crontab", "") in ("", None)
                and not node.get("once", False)
            )
            is_bounded_poll = (
                node.get("modbusSkillsRole") == "live-poll"
                and str(node.get("repeat", "")) == "5"
                and node.get("crontab", "") in ("", None)
                and not node.get("once", False)
            )
            if not (is_one_shot or is_bounded_poll):
                raise CampaignError(
                    "Node-RED trigger must be manual one-shot or the bounded 5-second live poll"
                )
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
        and (
            node.get("modbusSkillsRole") == "sequencer"
            or node.get("name") in {"Run bounded read plan", "02 Sequence read blocks"}
        )
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
        try:
            self.base_url = _loopback_http_url(base_url, "Node-RED admin URL")
        except ValueError as exc:
            raise CampaignError(str(exc)) from exc
        self.timeout = timeout
        self._opener = opener
        self._clock = clock
        self._wait = wait

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: Any = None,
        timeout_seconds: float | None = None,
    ) -> Any:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            timeout = self.timeout if timeout_seconds is None else min(
                self.timeout, max(timeout_seconds, 0.001)
            )
            with self._opener(request, timeout=timeout) as response:
                final_url = response.geturl() if hasattr(response, "geturl") else request.full_url
                if not _same_origin(final_url, self.base_url):
                    raise CampaignError("Node-RED admin redirected away from loopback")
                status = getattr(response, "status", 200)
                raw = response.read()
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise CampaignError(f"Node-RED admin request failed: {exc}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if method == "POST" and 200 <= status < 300 and raw.strip() in {b"", b"OK"}:
                return None
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

    def trigger(self, node_id: str, *, timeout_seconds: float | None = None) -> None:
        if not node_id.isalnum():
            raise CampaignError("Node-RED inject ID is invalid")
        self._request(
            f"/inject/{node_id}", method="POST", timeout_seconds=timeout_seconds
        )

    def _wait_for_capture(
        self, capture_path: Path, *, previous_mtime: int | None, deadline: float
    ) -> dict[str, Any]:
        while self._clock() < deadline:
            try:
                current_mtime = capture_path.stat().st_mtime_ns
            except FileNotFoundError:
                current_mtime = None
            if current_mtime is not None and current_mtime != previous_mtime:
                try:
                    capture = json.loads(capture_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    # The Node-RED file node can update mtime before its final
                    # write buffer is visible. Keep the same bounded deadline
                    # and retry instead of treating a transient partial file
                    # as a campaign failure.
                    self._wait(0.05)
                    continue
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
        with self.campaign_session(
            flow, capture_path=capture_path, environment=environment
        ) as run_round:
            return run_round(timeout_seconds)

    @contextmanager
    def campaign_session(
        self,
        flow: Sequence[Mapping[str, Any]],
        *,
        capture_path: Path,
        environment: Mapping[str, str],
    ) -> Iterator[Callable[[float], dict[str, Any]]]:
        """Deploy once, run bounded rounds, and restore the prior flow once."""

        validate_flow(flow)
        original = self.flows()
        deployed = copy.deepcopy([dict(node) for node in flow])
        tabs = [node for node in deployed if node.get("type") == "tab"]
        injects = [node for node in deployed if node.get("type") == "inject"]
        if len(tabs) != 1 or len(injects) != 1:
            raise CampaignError(
                "Node-RED campaign requires one tab and one manual start button"
            )
        # Native campaign runs are explicitly bounded. Temporarily disable the
        # final flow's live poll so the harness can trigger exactly one plan and
        # collect one deterministic capture; restore the polling flow on exit.
        if injects[0].get("modbusSkillsRole") == "live-poll":
            injects[0]["repeat"] = ""
        tabs[0]["disabled"] = False
        tabs[0]["env"] = [
            {"name": name, "value": str(value), "type": "str"}
            for name, value in sorted(environment.items())
        ]

        def run_round(timeout_seconds: float) -> dict[str, Any]:
            deadline = self._clock() + timeout_seconds
            try:
                previous_mtime = capture_path.stat().st_mtime_ns
            except FileNotFoundError:
                previous_mtime = None
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise CampaignError("Node-RED read plan did not drain before the timeout")
            self.trigger(str(injects[0]["id"]), timeout_seconds=remaining)
            return self._wait_for_capture(
                capture_path,
                previous_mtime=previous_mtime,
                deadline=deadline,
            )

        try:
            self.deploy(deployed)
            # Node-RED returns 204 before the newly deployed tab has finished
            # registering its admin routes. Give the runtime a short bounded
            # startup window before the first manual trigger.
            self._wait(1.0)
            yield run_round
        finally:
            self.deploy(original)


@dataclass(frozen=True)
class SimulatorReady:
    modbus_port: int


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
        try:
            self.base_url = _loopback_http_url(base_url, "simulator URL")
        except ValueError as exc:
            raise SimulatorError(str(exc)) from exc
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
                final_url = response.geturl() if hasattr(response, "geturl") else request.full_url
                if not _same_origin(final_url, self.base_url):
                    raise SimulatorError("simulator redirected away from loopback")
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
        return SimulatorReady(port)

    def registers(self, unit_id: int) -> dict[str, Any]:
        if unit_id < 1:
            raise SimulatorError("unit_id must be positive")
        return self._request_json(f"/api/generators/{unit_id}/registers")

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
