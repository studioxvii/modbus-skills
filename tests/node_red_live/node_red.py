from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


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
