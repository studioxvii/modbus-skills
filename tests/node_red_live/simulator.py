from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class SimulatorError(RuntimeError):
    """A simulator boundary or readiness failure."""


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
            message = detail.get("error", f"HTTP {exc.code}") if isinstance(detail, Mapping) else f"HTTP {exc.code}"
            raise SimulatorError(str(message)) from exc
        except (OSError, URLError, TimeoutError) as exc:
            raise SimulatorError(f"simulator unavailable: {exc}") from exc
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SimulatorError(f"simulator returned non-JSON response (HTTP {status})") from exc
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
            raise SimulatorError(f"simulator fleet size {fleet_size} does not match {expected_fleet}")
        if not 1 <= port <= 65535:
            raise SimulatorError("simulator reported an invalid Modbus port")
        return SimulatorReady(ready, port, fleet_size, str(value.get("modbus_host", "127.0.0.1")))

    def configure_fleet(self, fleet_size: int, *, modbus_port: int | None = None) -> dict[str, Any]:
        payload = {"size_counts": {"500": fleet_size, "1000": 0, "1500": 0, "2000": 0, "2500": 0}}
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
        return self._request_json("/api/scenarios/run", method="POST", payload={"scenario_id": scenario_id})

    def stop_scenario(self) -> dict[str, Any]:
        return self._request_json("/api/scenarios/stop", method="POST", payload={})
