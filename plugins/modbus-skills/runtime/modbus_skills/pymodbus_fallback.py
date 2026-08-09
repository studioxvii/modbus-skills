"""Generate the shared one-request PyModbus fallback artifact."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from .exporters import (
    Artifact,
    block_function_code,
    block_id,
    block_quantity,
    block_route_id,
    block_start,
    block_unit_id,
)


FALLBACK_FILENAME = "pymodbus-read-once.py"


def pymodbus_fallback_artifact(
    blocks: Iterable[Mapping[str, Any]], path: str
) -> Artifact:
    """Return one cross-platform script bound to explicit compiled requests."""

    requests = []
    for index, block in sorted(
        enumerate(blocks), key=lambda item: block_id(item[1], item[0])
    ):
        function_code = block_function_code(block)
        unit_id = block_unit_id(block)
        address = block_start(block)
        count = block_quantity(block)
        if function_code not in {1, 2, 3, 4}:
            raise ValueError("PyModbus fallback permits only FC01 through FC04")
        if unit_id is None or address is None or count is None:
            raise ValueError("PyModbus fallback requires a bounded unit, address, and count")
        requests.append(
            {
                "request_id": block_id(block, index),
                "route_id": block_route_id(block),
                "unit_id": unit_id,
                "function_code": function_code,
                "address": address,
                "count": count,
            }
        )
    request_json = json.dumps(requests, ensure_ascii=False, indent=2, sort_keys=True)
    script = _SCRIPT_TEMPLATE.replace("__COMPILED_REQUESTS__", request_json)
    return Artifact.text(path, "text/x-python", script, "bounded-pymodbus-fallback")


def native_verification_not_run(product: str) -> dict[str, str]:
    return {
        "status": "not-run",
        "reason": (
            f"Native {product} verification was not run by this exporter, so "
            "native verification is unavailable."
        ),
    }


_SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
"""Run exactly one reviewed request from the compiled read plan."""

from __future__ import annotations

import argparse
import inspect
import json

from pymodbus.client import ModbusTcpClient


REQUESTS = __COMPILED_REQUESTS__
METHODS = {
    1: "read_coils",
    2: "read_discrete_inputs",
    3: "read_holding_registers",
    4: "read_input_registers",
}
LIMITS = {1: 2000, 2: 2000, 3: 125, 4: 125}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one explicit FC01-FC04 request from this compiled plan."
    )
    parser.add_argument("--request", required=True, choices=[item["request_id"] for item in REQUESTS])
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--unit", required=True, type=int)
    parser.add_argument("--confirm-read", required=True, choices=["READ"])
    args = parser.parse_args()

    request = next(item for item in REQUESTS if item["request_id"] == args.request)
    if args.unit != request["unit_id"]:
        parser.error("--unit must match the selected compiled request")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be from 1 through 65535")
    function_code = request["function_code"]
    count = request["count"]
    address = request["address"]
    if function_code not in METHODS or not 1 <= count <= LIMITS[function_code]:
        parser.error("selected request is not a bounded FC01-FC04 read")
    if not 0 <= address <= 65535 or address + count > 65536:
        parser.error("selected request exceeds the Modbus address range")

    client = ModbusTcpClient(args.host, port=args.port, timeout=3)
    if not client.connect():
        raise SystemExit("Could not connect to the explicit endpoint")
    try:
        method = getattr(client, METHODS[function_code])
        parameters = inspect.signature(method).parameters
        unit_parameter = next(
            (name for name in ("device_id", "slave", "unit") if name in parameters),
            None,
        )
        if unit_parameter is None:
            raise SystemExit("Installed PyModbus has no supported unit-ID parameter")
        response = method(
            address=address,
            count=count,
            **{unit_parameter: args.unit},
        )
        if response.isError():
            raise SystemExit(f"Modbus exception response: {response}")
        values = (
            list(response.bits[:count])
            if function_code in {1, 2}
            else list(response.registers)
        )
        print(json.dumps({**request, "values": values}, sort_keys=True))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
'''
