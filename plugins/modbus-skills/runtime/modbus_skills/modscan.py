"""Auditable, read-only ModScan setup and test-message plan exporter."""

from __future__ import annotations

import csv
from io import StringIO
from typing import Any, Mapping

from .exporters import (
    Artifact,
    ExportResult,
    block_area,
    block_function_code,
    block_id,
    block_interval_ms,
    block_point_ids,
    block_quantity,
    block_route_id,
    block_start,
    block_unit_id,
    blocks_from_plan,
    canonical_map_hash,
    env_prefix_for_route,
    has_errors,
    held_result,
    normalize_mode,
    point_byte_order,
    point_datatype,
    point_id,
    point_name,
    point_protocol_offset,
    point_word_count,
    points_for_block,
    preflight_common,
    read_plan_hash,
    stable_json,
    target_manifest,
    write_csv_row,
)
from .pymodbus_fallback import (
    FALLBACK_FILENAME,
    native_verification_not_run,
    pymodbus_fallback_artifact,
)


ADAPTER_VERSION = "1.0.0"
TARGET = "modscan"
_REFERENCE_BASE = {
    "coil": 1,
    "discrete-input": 10001,
    "input-register": 30001,
    "holding-register": 40001,
}


def export_modscan(
    canonical_map: Mapping[str, Any],
    read_plan: Mapping[str, Any],
    *,
    mode: str = "final",
    options: Mapping[str, Any] | None = None,
) -> ExportResult:
    """Generate documented ModScan planning files without opaque formats.

    WinTECH documents spreadsheet-editable test messages but does not publish a
    stable native script schema on its product page.  This exporter therefore
    emits a protocol-level message plan and a manual setup table.  It never
    claims that a synthetic ``.tst`` or ``.cfg`` file is native ModScan data.
    """

    mode = normalize_mode(mode)
    options = dict(options or {})
    findings = list(preflight_common(canonical_map, read_plan, mode=mode))
    if has_errors(findings):
        return held_result(
            TARGET,
            canonical_map,
            read_plan,
            mode=mode,
            adapter_version=ADAPTER_VERSION,
            findings=findings,
        )

    blocks = tuple(blocks_from_plan(read_plan))
    routes = sorted({block_route_id(block) for block in blocks})
    multiple_routes = len(routes) > 1
    route_setup = []
    for route in routes:
        prefix = env_prefix_for_route(route, multiple_routes=multiple_routes)
        route_setup.append(
            {
                "route_id": route,
                "host_environment": f"{prefix}_HOST",
                "port_environment": f"{prefix}_PORT",
            }
        )

    setup_manifest = {
        "schema_version": "modscan-setup/v1",
        "target_versions": ["ModScan32", "ModScan64"],
        "mode": mode,
        "routes": route_setup,
        "protocol_address_base": 0,
        "opaque_native_files_bundled": False,
        "native_import_claim": False,
        "operator_entry_required": True,
        "source_files": [
            "read-plan.csv",
            "test-message-plan.csv",
            "point-map.csv",
        ],
        "native_verification": native_verification_not_run("ModScan"),
    }
    manifest = target_manifest(
        target=TARGET,
        profile=None,
        mode=mode,
        adapter_version=ADAPTER_VERSION,
        canonical_map=canonical_map,
        read_plan=read_plan,
        findings=findings,
        extra={
            "format": "modscan-auditable-read-plan",
            "product_documentation": "https://www.win-tech.com/html/modscan32.htm",
            "opaque_native_files_bundled": False,
            "native_import_claim": False,
            "routes": route_setup,
            "native_verification": native_verification_not_run("ModScan"),
        },
    )
    artifacts = (
        Artifact.text(
            "modscan/read-plan.csv",
            "text/csv",
            _read_plan_csv(blocks),
            "modscan-manual-setup-plan",
        ),
        Artifact.text(
            "modscan/test-message-plan.csv",
            "text/csv",
            _message_plan_csv(blocks),
            "modscan-test-message-plan",
        ),
        Artifact.text(
            "modscan/point-map.csv",
            "text/csv",
            _point_map_csv(canonical_map, blocks),
            "modscan-point-map",
        ),
        Artifact.text(
            "modscan/setup-manifest.json",
            "application/json",
            stable_json(setup_manifest),
            "setup-manifest",
        ),
        Artifact.text(
            "modscan/manifest.json",
            "application/json",
            stable_json(manifest),
            "target-manifest",
        ),
        Artifact.text(
            "modscan/README.md",
            "text/markdown",
            _readme(mode),
            "operator-instructions",
        ),
        pymodbus_fallback_artifact(
            blocks, f"modscan/{FALLBACK_FILENAME}"
        ),
    )
    return ExportResult(
        target=TARGET,
        status="generated",
        mode=mode,
        map_hash=canonical_map_hash(canonical_map),
        read_plan_hash=read_plan_hash(read_plan),
        adapter_version=ADAPTER_VERSION,
        findings=tuple(findings),
        artifacts=artifacts,
    )


def _read_plan_csv(blocks: tuple[Mapping[str, Any], ...]) -> str:
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    write_csv_row(
        writer,
        [
            "request_id",
            "route_id",
            "unit_id",
            "function_code",
            "area",
            "protocol_offset_base_0",
            "common_reference_base_1",
            "quantity",
            "poll_interval_ms",
            "point_ids",
        ]
    )
    for index, block in sorted(
        enumerate(blocks), key=lambda item: block_id(item[1], item[0])
    ):
        area = block_area(block)
        start = block_start(block)
        assert area is not None
        assert start is not None
        write_csv_row(
            writer,
            [
                block_id(block, index),
                block_route_id(block),
                block_unit_id(block),
                f"{block_function_code(block):02d}",
                area,
                start,
                _REFERENCE_BASE[area] + start,
                block_quantity(block),
                block_interval_ms(block),
                "|".join(block_point_ids(block)),
            ]
        )
    return buffer.getvalue()


def _message_plan_csv(blocks: tuple[Mapping[str, Any], ...]) -> str:
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    write_csv_row(
        writer,
        [
            "test_id",
            "route_id",
            "unit_id",
            "request_pdu_hex",
            "expected_function_hex",
            "expected_byte_count",
            "expected_response_prefix_hex",
            "expected_data_hex",
        ]
    )
    for index, block in sorted(
        enumerate(blocks), key=lambda item: block_id(item[1], item[0])
    ):
        function_code = block_function_code(block)
        start = block_start(block)
        quantity = block_quantity(block)
        assert function_code is not None
        assert start is not None
        assert quantity is not None
        expected_bytes = (
            (quantity + 7) // 8 if function_code in {1, 2} else quantity * 2
        )
        request = bytes(
            [
                function_code,
                (start >> 8) & 0xFF,
                start & 0xFF,
                (quantity >> 8) & 0xFF,
                quantity & 0xFF,
            ]
        )
        response_prefix = bytes([function_code, expected_bytes])
        write_csv_row(
            writer,
            [
                block_id(block, index),
                block_route_id(block),
                block_unit_id(block),
                _spaced_hex(request),
                f"{function_code:02X}",
                expected_bytes,
                _spaced_hex(response_prefix),
                "",
            ]
        )
    return buffer.getvalue()


def _point_map_csv(
    canonical_map: Mapping[str, Any], blocks: tuple[Mapping[str, Any], ...]
) -> str:
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    write_csv_row(
        writer,
        [
            "request_id",
            "logical_point_id",
            "name",
            "protocol_offset",
            "word_span",
            "datatype",
            "byte_order",
            "scale",
            "engineering_offset",
            "engineering_unit",
        ]
    )
    for block_index, block in sorted(
        enumerate(blocks), key=lambda item: block_id(item[1], item[0])
    ):
        points = points_for_block(canonical_map, block, block_index)
        for point_index, point in enumerate(
            sorted(
                points,
                key=lambda value: (
                    point_protocol_offset(value) or 0,
                    point_id(value) or "",
                ),
            )
        ):
            identifier = point_id(point, point_index) or f"point-{point_index + 1}"
            write_csv_row(
                writer,
                [
                    block_id(block, block_index),
                    identifier,
                    point_name(point, identifier),
                    point_protocol_offset(point),
                    point_word_count(point),
                    point_datatype(point) or "",
                    point_byte_order(point) or "",
                    "" if point.get("scale") is None else point.get("scale"),
                    ""
                    if point.get("engineering_offset", point.get("offset")) is None
                    else point.get("engineering_offset", point.get("offset")),
                    ""
                    if point.get("engineering_unit", point.get("unit")) is None
                    else point.get("engineering_unit", point.get("unit")),
                ]
            )
    return buffer.getvalue()


def _spaced_hex(value: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in value)


def _readme(mode: str) -> str:
    return f"""# ModScan {mode.title()} Read Plan

This target contains auditable planning files for ModScan32 or ModScan64. It
does not contain a synthetic `.tst` or `.cfg` file. WinTECH does not publish a
stable native import schema on its product page, so this exporter does not
claim that these CSV files are native ModScan files.

Native ModScan verification was not run, so native verification is unavailable.
If ModScan is not available, install PyModbus and use `pymodbus-read-once.py` for
one explicit request. It requires `--request`, `--host`, `--port`, the matching
`--unit`, and `--confirm-read READ`.

Use `read-plan.csv` to create read documents with functions 01 through 04.
Protocol offsets are base zero. The common reference column is present only as
a cross-check. Use `test-message-plan.csv` when you configure a documented
ModScan test message. Enter expected data only when a reviewed test specifies
it.

The generated PDU rows contain read requests only. They do not include a serial
CRC or a Modbus TCP MBAP header because ModScan supplies the transport framing.
Review the endpoint, unit ID, function, offset, quantity, and interval before
you start polling.
"""
