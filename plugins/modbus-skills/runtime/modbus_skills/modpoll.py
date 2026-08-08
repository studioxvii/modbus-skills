"""Read-only exporters for the two products commonly called Modpoll."""

from __future__ import annotations

import csv
from fractions import Fraction
from io import StringIO
from typing import Any, Iterable, Mapping
import xml.etree.ElementTree as ET

from .exporters import (
    AREA_FUNCTION_CODES,
    Artifact,
    ExportResult,
    Finding,
    block_area,
    block_function_code,
    block_id,
    block_interval_ms,
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
    safe_slug,
    stable_json,
    target_manifest,
    write_csv_row,
)


ADAPTER_VERSION = "1.0.0"
TARGET = "modpoll"
SUPPORTED_PROFILES = frozenset(
    {"gavinying-cli", "witte-desktop", "witte-v12-xml"}
)
WITTE_DESKTOP_MIN_SCAN_INTERVAL_MS = 1000
WITTE_DESKTOP_MAX_ROUTE_READS_PER_SECOND = 5

_GAVINYING_AREA = {
    "coil": "coil",
    "discrete-input": "discrete_input",
    "input-register": "input_register",
    "holding-register": "holding_register",
}
_TRADITIONAL_BASE = {
    "coil": 0,
    "discrete-input": 10000,
    "input-register": 30000,
    "holding-register": 40000,
}
_GAVINYING_BYTE_ORDER = {
    "ABCD": "BE_BE",
    "BADC": "LE_BE",
    "CDAB": "BE_LE",
    "DCBA": "LE_LE",
    "BE_BE": "BE_BE",
    "LE_BE": "LE_BE",
    "BE_LE": "BE_LE",
    "LE_LE": "LE_LE",
}
_GAVINYING_DTYPES = {
    "bool": "bool",
    "boolean": "bool",
    "int16": "int16",
    "uint16": "uint16",
    "float16": "float16",
    "int32": "int32",
    "uint32": "uint32",
    "float32": "float32",
    "int64": "int64",
    "uint64": "uint64",
    "float64": "float64",
    "double": "float64",
}
_WITTE_METHODS = {
    1: "ReadCoils",
    2: "ReadDiscreteInputs",
    3: "ReadHoldingRegisters",
    4: "ReadInputRegisters",
}


def export_modpoll(
    canonical_map: Mapping[str, Any],
    read_plan: Mapping[str, Any],
    *,
    mode: str = "final",
    profile: str | None = None,
    options: Mapping[str, Any] | None = None,
) -> ExportResult:
    """Generate one explicit Modpoll profile.

    ``gavinying-cli`` produces the documented ``device``/``poll``/``ref`` CSV
    format.  ``witte-desktop`` produces an auditable read plan and a PowerShell
    automation script.  The Witte application, not this exporter, creates any
    native ``.mbp`` files.
    """

    mode = normalize_mode(mode)
    options = dict(options or {})
    selected_profile = str(
        profile or options.get("profile", "gavinying-cli")
    ).strip().lower()
    if selected_profile not in SUPPORTED_PROFILES:
        raise ValueError(
            f"Modpoll profile must be one of {sorted(SUPPORTED_PROFILES)}; "
            f"got {selected_profile!r}"
        )
    if selected_profile == "gavinying-cli":
        return _export_gavinying(canonical_map, read_plan, mode=mode, options=options)
    if selected_profile == "witte-v12-xml":
        return _export_witte_v12_xml(
            canonical_map, read_plan, mode=mode, options=options
        )
    return _export_witte(canonical_map, read_plan, mode=mode, options=options)


def _export_gavinying(
    canonical_map: Mapping[str, Any],
    read_plan: Mapping[str, Any],
    *,
    mode: str,
    options: Mapping[str, Any],
) -> ExportResult:
    profile = "gavinying-cli"
    findings = list(preflight_common(canonical_map, read_plan, mode=mode))
    blocks = tuple(blocks_from_plan(read_plan))
    if not has_errors(findings):
        findings.extend(_gavinying_preflight(canonical_map, blocks, mode=mode))
    if has_errors(findings):
        return held_result(
            TARGET,
            canonical_map,
            read_plan,
            mode=mode,
            adapter_version=ADAPTER_VERSION,
            findings=findings,
            profile=profile,
        )

    routes = sorted({block_route_id(block) for block in blocks})
    multiple_routes = len(routes) > 1
    artifacts: list[Artifact] = []
    route_files: list[dict[str, Any]] = []
    command_lines = [
        "# Review the endpoint values before use.",
        "# Each command performs one bounded polling pass.",
    ]
    for route in routes:
        route_blocks = [
            (index, block)
            for index, block in enumerate(blocks)
            if block_route_id(block) == route
        ]
        filename = f"{safe_slug(route, fallback='default')}.csv"
        path = f"modpoll/gavinying-cli/{filename}"
        csv_text = _gavinying_csv(canonical_map, route_blocks, mode=mode)
        artifacts.append(
            Artifact.text(path, "text/csv", csv_text, "gavinying-modpoll-config")
        )
        prefix = env_prefix_for_route(route, multiple_routes=multiple_routes)
        command_lines.append(
            "modpoll --once --tcp \"${"
            + prefix
            + "_HOST}\" --tcp-port \"${"
            + prefix
            + "_PORT}\" --config \""
            + filename
            + "\""
        )
        route_files.append(
            {
                "route_id": route,
                "config": path,
                "host_environment": f"{prefix}_HOST",
                "port_environment": f"{prefix}_PORT",
            }
        )

    command_text = "\n".join(command_lines) + "\n"
    artifacts.append(
        Artifact.text(
            "modpoll/gavinying-cli/commands.txt",
            "text/plain",
            command_text,
            "operator-command-reference",
        )
    )
    manifest = target_manifest(
        target=TARGET,
        profile=profile,
        mode=mode,
        adapter_version=ADAPTER_VERSION,
        canonical_map=canonical_map,
        read_plan=read_plan,
        findings=findings,
        extra={
            "format": "gavinying-modpoll-csv",
            "format_documentation": "https://gavinying.github.io/modpoll/configure.html",
            "routes": route_files,
        },
    )
    artifacts.extend(
        [
            Artifact.text(
                "modpoll/gavinying-cli/manifest.json",
                "application/json",
                stable_json(manifest),
                "target-manifest",
            ),
            Artifact.text(
                "modpoll/gavinying-cli/README.md",
                "text/markdown",
                _gavinying_readme(mode),
                "operator-instructions",
            ),
        ]
    )
    return ExportResult(
        target=TARGET,
        status="generated",
        mode=mode,
        map_hash=canonical_map_hash(canonical_map),
        read_plan_hash=read_plan_hash(read_plan),
        adapter_version=ADAPTER_VERSION,
        profile=profile,
        findings=tuple(findings),
        artifacts=tuple(artifacts),
    )


def _gavinying_preflight(
    canonical_map: Mapping[str, Any],
    blocks: Iterable[Mapping[str, Any]],
    *,
    mode: str,
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for block_index, block in enumerate(blocks):
        block_path = f"requests[{block_index}]"
        unit = block_unit_id(block)
        if unit is not None and not 1 <= unit <= 254:
            findings.append(
                Finding(
                    "error",
                    "MODPOLL_UNIT_UNSUPPORTED",
                    "gavinying/modpoll documents device IDs from 1 through 254.",
                    f"{block_path}.unit_id",
                )
            )
        layouts: set[str] = set()
        points = points_for_block(canonical_map, block, block_index)
        for point_index, point in enumerate(points):
            point_path = f"{block_path}.points[{point_index}]"
            if mode == "final":
                datatype = point_datatype(point)
                if datatype is None or _gavinying_dtype(
                    datatype, point_word_count(point)
                ) is None:
                    findings.append(
                        Finding(
                            "error",
                            "MODPOLL_DATATYPE_UNSUPPORTED",
                            f"gavinying/modpoll does not document datatype {datatype!r}.",
                            f"{point_path}.datatype",
                        )
                    )
                engineering_offset = point.get(
                    "engineering_offset", point.get("offset")
                )
                if engineering_offset not in (None, 0, 0.0, "0", "0.0"):
                    findings.append(
                        Finding(
                            "error",
                            "MODPOLL_OFFSET_UNSUPPORTED",
                            "gavinying/modpoll supports a multiplier but not an additive engineering offset.",
                            f"{point_path}.engineering_offset",
                        )
                    )
                if (
                    (point_word_count(point) or 1) > 1
                    and not (datatype or "").startswith("string")
                ):
                    order = point_byte_order(point)
                    mapped = _GAVINYING_BYTE_ORDER.get(order or "")
                    if mapped is None:
                        findings.append(
                            Finding(
                                "error",
                                "MODPOLL_BYTE_ORDER_UNSUPPORTED",
                                f"gavinying/modpoll cannot map byte order {order!r}.",
                                f"{point_path}.byte_order",
                            )
                        )
                    else:
                        layouts.add(mapped)
        if len(layouts) > 1:
            findings.append(
                Finding(
                    "error",
                    "MODPOLL_BLOCK_ENDIAN_CONFLICT",
                    "One gavinying/modpoll poll row cannot decode points with different byte orders.",
                    block_path,
                )
            )
    return tuple(findings)


def _gavinying_csv(
    canonical_map: Mapping[str, Any],
    blocks: list[tuple[int, Mapping[str, Any]]],
    *,
    mode: str,
) -> str:
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    blocks_by_unit: dict[int, list[tuple[int, Mapping[str, Any]]]] = {}
    for source_index, block in blocks:
        unit = block_unit_id(block)
        assert unit is not None
        blocks_by_unit.setdefault(unit, []).append((source_index, block))

    route = block_route_id(blocks[0][1]) if blocks else "default"
    for unit in sorted(blocks_by_unit):
        device_name = f"{safe_slug(route, fallback='device')}_{unit}"
        write_csv_row(writer, ["device", device_name, unit, "", ""])
        for source_index, block in sorted(
            blocks_by_unit[unit], key=lambda item: block_id(item[1], item[0])
        ):
            area = block_area(block)
            start = block_start(block)
            quantity = block_quantity(block)
            assert area is not None
            assert start is not None
            assert quantity is not None
            points = points_for_block(canonical_map, block, source_index)
            endian = "BE_BE"
            if mode == "final":
                orders = {
                    _GAVINYING_BYTE_ORDER[point_byte_order(point) or "ABCD"]
                    for point in points
                    if (point_word_count(point) or 1) > 1
                    and not (point_datatype(point) or "").startswith("string")
                }
                if orders:
                    endian = sorted(orders)[0]
            display_start = _TRADITIONAL_BASE[area] + start
            write_csv_row(
                writer,
                ["poll", _GAVINYING_AREA[area], display_start, quantity, endian]
            )
            if mode == "probe":
                _write_probe_refs(
                    writer,
                    block=block,
                    source_index=source_index,
                    area=area,
                    start=display_start,
                    quantity=quantity,
                )
            else:
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
                    point_start = point_protocol_offset(point)
                    assert point_start is not None
                    datatype = _gavinying_dtype(
                        point_datatype(point) or "", point_word_count(point)
                    )
                    assert datatype is not None
                    unit_text = point.get(
                        "engineering_unit", point.get("unit", "")
                    )
                    scale = point.get("scale", "")
                    write_csv_row(
                        writer,
                        [
                            "ref",
                            safe_slug(point_name(point, identifier), fallback=identifier).replace("-", "_"),
                            _TRADITIONAL_BASE[area] + point_start,
                            datatype,
                            "r",
                            "" if unit_text is None else unit_text,
                            "" if scale is None else scale,
                        ]
                    )
    return buffer.getvalue()


def _write_probe_refs(
    writer: Any,
    *,
    block: Mapping[str, Any],
    source_index: int,
    area: str,
    start: int,
    quantity: int,
) -> None:
    identifier = safe_slug(block_id(block, source_index), fallback="block").replace("-", "_")
    if area in {"coil", "discrete-input"}:
        for byte_index in range((quantity + 7) // 8):
            write_csv_row(
                writer,
                [
                    "ref",
                    f"raw_{identifier}_byte_{byte_index:03d}",
                    start + byte_index,
                    "bool8",
                    "r",
                    "",
                    "",
                ]
            )
        return
    for word_index in range(quantity):
        write_csv_row(
            writer,
            [
                "ref",
                f"raw_{identifier}_word_{word_index:03d}",
                start + word_index,
                "uint16",
                "r",
                "",
                "",
            ]
        )


def _gavinying_dtype(datatype: str, word_count: int | None = None) -> str | None:
    normalized = datatype.strip().lower()
    if normalized in {"string", "ascii"}:
        return f"string{word_count * 2}" if word_count else None
    if normalized.startswith("string") and normalized.removeprefix("string").isdigit():
        return normalized
    return _GAVINYING_DTYPES.get(normalized)


def _export_witte(
    canonical_map: Mapping[str, Any],
    read_plan: Mapping[str, Any],
    *,
    mode: str,
    options: Mapping[str, Any],
) -> ExportResult:
    profile = "witte-desktop"
    live_read_seconds = options.get("live_read_seconds", 10)
    if isinstance(live_read_seconds, bool) or not isinstance(live_read_seconds, int):
        raise ValueError("live_read_seconds must be an integer from 1 through 30")
    if not 1 <= live_read_seconds <= 30:
        raise ValueError("live_read_seconds must be from 1 through 30")
    findings = list(preflight_common(canonical_map, read_plan, mode=mode))
    blocks = tuple(blocks_from_plan(read_plan))
    findings.extend(_witte_polling_findings(blocks))
    for block_index, block in enumerate(blocks):
        unit = block_unit_id(block)
        if unit is not None and not 1 <= unit <= 255:
            findings.append(
                Finding(
                    "error",
                    "WITTE_UNIT_UNSUPPORTED",
                    "Witte Modbus Poll documents read slave IDs from 1 through 255.",
                    f"requests[{block_index}].unit_id",
                )
            )
    if has_errors(findings):
        return held_result(
            TARGET,
            canonical_map,
            read_plan,
            mode=mode,
            adapter_version=ADAPTER_VERSION,
            findings=findings,
            profile=profile,
        )

    routes = sorted({block_route_id(block) for block in blocks})
    multiple_routes = len(routes) > 1
    artifacts: list[Artifact] = []
    setup_routes: list[dict[str, Any]] = []
    for route in routes:
        route_blocks = [
            (index, block)
            for index, block in enumerate(blocks)
            if block_route_id(block) == route
        ]
        route_slug = safe_slug(route, fallback="default")
        prefix = env_prefix_for_route(route, multiple_routes=multiple_routes)
        script_path = f"modpoll/witte-desktop/create-{route_slug}-read-documents.ps1"
        artifacts.append(
            Artifact.text(
                script_path,
                "text/x-powershell",
                _witte_script(
                    route_blocks,
                    env_prefix=prefix,
                    live_read_seconds=live_read_seconds,
                ),
                "witte-read-automation",
            )
        )
        setup_routes.append(
            {
                "route_id": route,
                "script": script_path,
                "host_environment": f"{prefix}_HOST",
                "port_environment": f"{prefix}_PORT",
            }
        )

    plan_path = "modpoll/witte-desktop/read-plan.csv"
    artifacts.append(
        Artifact.text(
            plan_path,
            "text/csv",
            _witte_plan_csv(blocks),
            "witte-read-plan",
        )
    )
    setup_manifest = {
        "schema_version": "witte-modbus-poll-setup/v1",
        "profile": profile,
        "native_files_bundled": False,
        "native_file_creation": "Modbus Poll Save automation method",
        "com_program_ids": ["Mbpoll.Application", "Mbpoll.Document"],
        "live_read_confirmation_required": True,
        "live_read_seconds": live_read_seconds,
        "polling_safety": {
            "minimum_scan_interval_ms": WITTE_DESKTOP_MIN_SCAN_INTERVAL_MS,
            "maximum_route_reads_per_second": (
                WITTE_DESKTOP_MAX_ROUTE_READS_PER_SECOND
            ),
        },
        "routes": setup_routes,
        "read_plan": plan_path,
    }
    artifacts.append(
        Artifact.text(
            "modpoll/witte-desktop/setup-manifest.json",
            "application/json",
            stable_json(setup_manifest),
            "setup-manifest",
        )
    )
    manifest = target_manifest(
        target=TARGET,
        profile=profile,
        mode=mode,
        adapter_version=ADAPTER_VERSION,
        canonical_map=canonical_map,
        read_plan=read_plan,
        findings=findings,
        extra={
            "format": "witte-modbus-poll-automation",
            "format_documentation": "https://www.modbustools.com/mbpoll-user-manual.html",
            "native_files_bundled": False,
            "live_read_confirmation_required": True,
            "live_read_seconds": live_read_seconds,
            "polling_safety": {
                "minimum_scan_interval_ms": WITTE_DESKTOP_MIN_SCAN_INTERVAL_MS,
                "maximum_route_reads_per_second": (
                    WITTE_DESKTOP_MAX_ROUTE_READS_PER_SECOND
                ),
            },
            "routes": setup_routes,
        },
    )
    artifacts.extend(
        [
            Artifact.text(
                "modpoll/witte-desktop/manifest.json",
                "application/json",
                stable_json(manifest),
                "target-manifest",
            ),
            Artifact.text(
                "modpoll/witte-desktop/README.md",
                "text/markdown",
                _witte_readme(mode, live_read_seconds=live_read_seconds),
                "operator-instructions",
            ),
        ]
    )
    return ExportResult(
        target=TARGET,
        status="generated",
        mode=mode,
        map_hash=canonical_map_hash(canonical_map),
        read_plan_hash=read_plan_hash(read_plan),
        adapter_version=ADAPTER_VERSION,
        profile=profile,
        findings=tuple(findings),
        artifacts=tuple(artifacts),
    )


def _witte_polling_findings(
    blocks: Iterable[Mapping[str, Any]],
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    route_rates: dict[str, Fraction] = {}
    for block_index, block in enumerate(blocks):
        interval = _witte_scan_interval_ms(block)
        path = f"requests[{block_index}].poll_interval_ms"
        if interval is None:
            findings.append(
                Finding(
                    "error",
                    "WITTE_SCAN_INTERVAL_INVALID",
                    "Witte desktop scan intervals must be integer milliseconds.",
                    path,
                )
            )
            continue
        if interval < WITTE_DESKTOP_MIN_SCAN_INTERVAL_MS:
            findings.append(
                Finding(
                    "error",
                    "WITTE_SCAN_INTERVAL_TOO_SHORT",
                    (
                        "Witte desktop scan intervals must be at least "
                        f"{WITTE_DESKTOP_MIN_SCAN_INTERVAL_MS} milliseconds."
                    ),
                    path,
                )
            )
        route = block_route_id(block)
        route_rates[route] = route_rates.get(route, Fraction()) + Fraction(
            1000,
            interval,
        )

    limit = Fraction(WITTE_DESKTOP_MAX_ROUTE_READS_PER_SECOND)
    for route, rate in sorted(route_rates.items()):
        if rate > limit:
            findings.append(
                Finding(
                    "error",
                    "WITTE_ROUTE_READ_RATE_EXCEEDED",
                    (
                        f"Route {route!r} exceeds the Witte desktop limit of "
                        f"{WITTE_DESKTOP_MAX_ROUTE_READS_PER_SECOND} read "
                        "requests per second."
                    ),
                    "requests",
                )
            )
    return tuple(findings)


def _witte_scan_interval_ms(block: Mapping[str, Any]) -> int | None:
    if "poll_interval_ms" in block:
        raw_value = block["poll_interval_ms"]
    elif "interval_ms" in block:
        raw_value = block["interval_ms"]
    else:
        return WITTE_DESKTOP_MIN_SCAN_INTERVAL_MS

    if isinstance(raw_value, bool):
        return None
    if isinstance(raw_value, int):
        interval = raw_value
    elif isinstance(raw_value, str) and raw_value.strip().isdigit():
        interval = int(raw_value.strip())
    else:
        return None
    return interval if 1 <= interval <= 3_600_000 else None


def _export_witte_v12_xml(
    canonical_map: Mapping[str, Any],
    read_plan: Mapping[str, Any],
    *,
    mode: str,
    options: Mapping[str, Any],
) -> ExportResult:
    profile = "witte-v12-xml"
    findings = list(preflight_common(canonical_map, read_plan, mode=mode))
    if has_errors(findings):
        return held_result(
            TARGET,
            canonical_map,
            read_plan,
            mode=mode,
            adapter_version=ADAPTER_VERSION,
            findings=findings,
            profile=profile,
        )

    blocks = tuple(blocks_from_plan(read_plan))
    artifacts: list[Artifact] = []
    documents = []
    for index, block in sorted(
        enumerate(blocks), key=lambda item: block_id(item[1], item[0])
    ):
        identifier = block_id(block, index)
        filename = safe_slug(identifier, fallback=f"read-{index + 1:04d}") + ".xml"
        path = f"modpoll/witte-v12-xml/{filename}"
        xml_text = _witte_v12_xml(block)
        validation_findings = validate_witte_v12_xml(xml_text)
        if validation_findings:
            findings.extend(validation_findings)
            return held_result(
                TARGET,
                canonical_map,
                read_plan,
                mode=mode,
                adapter_version=ADAPTER_VERSION,
                findings=findings,
                profile=profile,
            )
        artifacts.append(
            Artifact.text(
                path,
                "application/xml",
                xml_text,
                "witte-v12-read-document",
            )
        )
        documents.append(
            {
                "request_id": identifier,
                "route_id": block_route_id(block),
                "path": path,
                "enabled": False,
            }
        )

    setup_manifest = {
        "schema_version": "witte-modbus-poll-v12-xml-setup/v1",
        "profile": profile,
        "minimum_documented_major_version": 12,
        "documents": documents,
        "connection_stored_in_documents": False,
        "operator_connection_setup_required": True,
        "opaque_native_files_bundled": False,
    }
    manifest = target_manifest(
        target=TARGET,
        profile=profile,
        mode=mode,
        adapter_version=ADAPTER_VERSION,
        canonical_map=canonical_map,
        read_plan=read_plan,
        findings=findings,
        extra={
            "format": "witte-modbus-poll-v12-xml",
            "format_documentation": "https://www.modbustools.com/pollxml.html",
            "minimum_documented_major_version": 12,
            "documents": documents,
            "opaque_native_files_bundled": False,
        },
    )
    artifacts.extend(
        [
            Artifact.text(
                "modpoll/witte-v12-xml/setup-manifest.json",
                "application/json",
                stable_json(setup_manifest),
                "setup-manifest",
            ),
            Artifact.text(
                "modpoll/witte-v12-xml/manifest.json",
                "application/json",
                stable_json(manifest),
                "target-manifest",
            ),
            Artifact.text(
                "modpoll/witte-v12-xml/README.md",
                "text/markdown",
                _witte_v12_readme(mode),
                "operator-instructions",
            ),
        ]
    )
    return ExportResult(
        target=TARGET,
        status="generated",
        mode=mode,
        map_hash=canonical_map_hash(canonical_map),
        read_plan_hash=read_plan_hash(read_plan),
        adapter_version=ADAPTER_VERSION,
        profile=profile,
        findings=tuple(findings),
        artifacts=tuple(artifacts),
    )


def _witte_v12_xml(block: Mapping[str, Any]) -> str:
    function_code = block_function_code(block)
    unit = block_unit_id(block)
    start = block_start(block)
    quantity = block_quantity(block)
    assert function_code in _WITTE_METHODS
    assert unit is not None
    assert start is not None
    assert quantity is not None

    root = ET.Element("ModbusPoll")
    ET.SubElement(root, "FileSchema", {"r": "0", "c": "0"})
    ET.SubElement(
        root,
        "Version",
        {"major": "12", "minor": "0", "patch": "0", "build": "0"},
    )
    _xml_text(root, "dpi", "96")
    ET.SubElement(
        root,
        "WP",
        {
            "left": "0",
            "right": "552",
            "top": "0",
            "bottom": "263",
            "ShowCmd": "1",
            "MaxPosX": "-1",
            "MaxPosY": "-1",
            "MinPosX": "-1",
            "MinPosY": "-1",
        },
    )
    _xml_text(root, "ScanRate", str(block_interval_ms(block)))
    _xml_text(root, "SlaveID", str(unit))
    _xml_text(root, "Enable", "0")
    for tag, value in (
        ("StopOnError", "0"),
        ("OneBased", "0"),
        ("RowsDialog", "0"),
        ("HideNames", "0"),
        ("HexMode", "0"),
        ("DisplayAddr", "0"),
        ("ColCount", "2"),
        ("RowCount", str(quantity)),
    ):
        _xml_text(root, tag, value)
    column_width = ET.SubElement(root, "ColumnWidth")
    _xml_text(column_width, "CW", "900")
    _xml_text(column_width, "CW", "900")
    row_height = ET.SubElement(root, "RowHight")
    for _ in range(quantity):
        _xml_text(row_height, "RH", "200")
    for tag in ("ScrollPosV", "ScrollPosH"):
        _xml_text(root, tag, "0")
    _xml_text(root, "FocusRow", "1")
    _xml_text(root, "FocusCol", "2")

    log_text = ET.SubElement(root, "LogText")
    for tag, value in (
        ("Eachread", "0"),
        ("Rate", "1"),
        ("LogChangedOnly", "0"),
        ("LogErrors", "0"),
        ("LogErrorsOnly", "0"),
        ("LogAddress", "1"),
        ("LogDate", "0"),
        ("TDelimiter", "0"),
        ("LogMs", "1"),
        ("Delimiter", "0"),
        ("AutoStart", "0"),
        ("Flush", "0"),
        ("Append", "0"),
        ("NewLogFileAtMidnight", "0"),
        ("InsertHeader", "0"),
        ("NameCellsInTopRow", "0"),
        ("PollDefinition", "0"),
        ("LogName", ""),
        ("FileName", ""),
    ):
        _xml_text(log_text, tag, value)

    log_excel = ET.SubElement(root, "LogExcel")
    for tag, value in (
        ("Eachread", "1"),
        ("Rate", "1"),
        ("StopAfter", "1000"),
        ("LogChangedOnly", "0"),
        ("InsertHeader", "1"),
        ("NameCellsInTopRow", "1"),
        ("PollDefinition", "1"),
        ("LogName", ""),
    ):
        _xml_text(log_excel, tag, value)

    data = ET.SubElement(root, "Data")
    _xml_text(data, "Function", str(function_code))
    _xml_text(data, "Address", str(start))
    _xml_text(data, "Quantity", str(quantity))
    _xml_text(data, "EnronMode", "0")
    formats = ET.SubElement(data, "Formats")
    for _ in range(quantity):
        ET.SubElement(formats, "F", {"f": "0", "v": "0"})
    bytes_element = ET.SubElement(data, "Bytes")
    for _ in range(quantity * 2):
        _xml_text(bytes_element, "B", "0")
    for tag in ("CellData", "Scales", "ValueNames", "ChartSeries", "BinNames"):
        ET.SubElement(data, tag)

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def validate_witte_v12_xml(xml_text: str) -> tuple[Finding, ...]:
    """Validate the documented safety-critical structure of one v12 XML file."""

    findings: list[Finding] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as error:
        return (
            Finding(
                "error",
                "WITTE_XML_INVALID",
                f"Witte v12 XML is not well formed: {error}.",
            ),
        )
    if root.tag != "ModbusPoll":
        findings.append(
            Finding(
                "error",
                "WITTE_XML_ROOT_INVALID",
                "Witte v12 XML root must be ModbusPoll.",
            )
        )
    required = (
        "Version",
        "ScanRate",
        "SlaveID",
        "Enable",
        "OneBased",
        "Data/Function",
        "Data/Address",
        "Data/Quantity",
    )
    for path in required:
        if root.find(path) is None:
            findings.append(
                Finding(
                    "error",
                    "WITTE_XML_FIELD_MISSING",
                    f"Witte v12 XML is missing {path}.",
                    path,
                )
            )
    function_text = root.findtext("Data/Function")
    try:
        function_code = int(function_text or "")
    except ValueError:
        function_code = -1
    if function_code not in _WITTE_METHODS:
        findings.append(
            Finding(
                "error",
                "WITTE_XML_FUNCTION_UNSAFE",
                "Witte v12 XML permits only read functions 01 through 04.",
                "Data/Function",
            )
        )
    if root.findtext("Enable") != "0":
        findings.append(
            Finding(
                "error",
                "WITTE_XML_NOT_DISABLED",
                "Generated Witte v12 XML must be disabled by default.",
                "Enable",
            )
        )
    return tuple(findings)


def _xml_text(parent: ET.Element, tag: str, value: str) -> ET.Element:
    element = ET.SubElement(parent, tag)
    element.text = value
    return element


def _witte_script(
    blocks: list[tuple[int, Mapping[str, Any]]],
    *,
    env_prefix: str,
    live_read_seconds: int,
) -> str:
    ordered_blocks = sorted(blocks, key=lambda item: block_id(item[1], item[0]))
    scan_intervals = [block_interval_ms(block) for _, block in ordered_blocks]
    scan_interval_literals = ", ".join(str(value) for value in scan_intervals)
    lines = [
        "$ErrorActionPreference = \"Stop\"",
        f"$deviceHost = $env:{env_prefix}_HOST",
        f"$devicePort = $env:{env_prefix}_PORT",
        "if ([string]::IsNullOrWhiteSpace($deviceHost)) { throw \"Set the Modbus host environment variable.\" }",
        "if ([string]::IsNullOrWhiteSpace($devicePort)) { throw \"Set the Modbus port environment variable.\" }",
        f"$minimumScanIntervalMilliseconds = {WITTE_DESKTOP_MIN_SCAN_INTERVAL_MS}",
        f"$maximumRouteReadsPerSecond = {WITTE_DESKTOP_MAX_ROUTE_READS_PER_SECOND}",
        f"$scanIntervalsMilliseconds = @({scan_interval_literals})",
        "$unsafeScanIntervals = @($scanIntervalsMilliseconds | Where-Object { $_ -lt $minimumScanIntervalMilliseconds })",
        "if ($unsafeScanIntervals.Count -gt 0) { throw \"The generated scan rate is below the safety minimum.\" }",
        "$configuredReadsPerSecond = ($scanIntervalsMilliseconds | ForEach-Object { 1000.0 / [double]$_ } | Measure-Object -Sum).Sum",
        "if ($configuredReadsPerSecond -gt $maximumRouteReadsPerSecond) { throw \"The generated aggregate route read rate exceeds the safety limit.\" }",
        f"$confirmation = Read-Host \"Type READ to start a bounded live read of at most {live_read_seconds} seconds\"",
        "if ($confirmation -cne \"READ\") { throw \"Live-read confirmation was not received.\" }",
        f"$maximumLiveReadSeconds = {live_read_seconds}",
        "$application = $null",
        "$connectionOpened = $false",
        "$documents = @()",
        "$liveReadStopwatch = [System.Diagnostics.Stopwatch]::StartNew()",
        "try {",
        "  $application = New-Object -ComObject \"Mbpoll.Application\"",
        "  $application.Connection = 1",
        "  $application.IPAddress = $deviceHost",
        "  $application.ServerPort = [int]$devicePort",
        "  $application.ResponseTimeout = 1000",
        "  $application.ConnectTimeout = 500",
        "  $application.DelayBetweenPolls = 20",
        "  $connectionResult = $application.OpenConnection()",
        "  if ($connectionResult -ne 0) { throw \"Modbus Poll connection failed with result $connectionResult.\" }",
        "  $connectionOpened = $true",
        "  $outputDirectory = Join-Path $PSScriptRoot \"app-created\"",
        "  New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null",
    ]
    for order_index, (source_index, block) in enumerate(ordered_blocks, start=1):
        identifier = block_id(block, source_index)
        function_code = block_function_code(block)
        unit = block_unit_id(block)
        start = block_start(block)
        quantity = block_quantity(block)
        assert function_code in _WITTE_METHODS
        assert unit is not None
        assert start is not None
        assert quantity is not None
        method = _WITTE_METHODS[function_code]
        scan_rate = block_interval_ms(block)
        filename = safe_slug(identifier, fallback=f"block-{order_index:03d}") + ".mbp"
        variable = f"$document{order_index}"
        lines.extend(
            [
                "  if ($liveReadStopwatch.Elapsed.TotalSeconds -ge $maximumLiveReadSeconds) { throw \"The bounded live-read duration expired.\" }",
                f"  {variable} = New-Object -ComObject \"Mbpoll.Document\"",
                f"  $documents += {variable}",
                f"  $readResult = {variable}.{method}({unit}, {start}, {quantity}, {scan_rate})",
                f"  if (-not $readResult) {{ throw \"Could not create read document for {filename}.\" }}",
                f"  {variable}.ShowWindow()",
                f"  $savePath = Join-Path $outputDirectory \"{filename}\"",
                f"  $saveResult = {variable}.Save($savePath)",
                f"  if (-not $saveResult) {{ throw \"Modbus Poll could not save {filename}.\" }}",
            ]
        )
    lines.extend(
        [
            "  $remainingMilliseconds = [Math]::Max(0, [Math]::Floor(($maximumLiveReadSeconds - $liveReadStopwatch.Elapsed.TotalSeconds) * 1000))",
            "  if ($remainingMilliseconds -gt 0) { Start-Sleep -Milliseconds $remainingMilliseconds }",
            "}",
            "finally {",
            "  $liveReadStopwatch.Stop()",
            "  if ($connectionOpened -and $null -ne $application) {",
            "    try { $application.CloseConnection() | Out-Null } catch { }",
            "    $connectionOpened = $false",
            "  }",
            "  foreach ($document in $documents) {",
            "    if ($null -ne $document -and [System.Runtime.InteropServices.Marshal]::IsComObject($document)) {",
            "      try { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($document) } catch { }",
            "    }",
            "  }",
            "  if ($null -ne $application -and [System.Runtime.InteropServices.Marshal]::IsComObject($application)) {",
            "    try { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($application) } catch { }",
            "  }",
            "  [GC]::Collect()",
            "  [GC]::WaitForPendingFinalizers()",
            "}",
        ]
    )
    return "\n".join(lines) + "\n"


def _witte_plan_csv(blocks: Iterable[Mapping[str, Any]]) -> str:
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
            "protocol_offset",
            "quantity",
            "scan_rate_ms",
        ]
    )
    for index, block in sorted(
        enumerate(blocks), key=lambda item: block_id(item[1], item[0])
    ):
        write_csv_row(
            writer,
            [
                block_id(block, index),
                block_route_id(block),
                block_unit_id(block),
                f"{block_function_code(block):02d}",
                block_area(block),
                block_start(block),
                block_quantity(block),
                block_interval_ms(block),
            ]
        )
    return buffer.getvalue()


def _gavinying_readme(mode: str) -> str:
    return f"""# gavinying/modpoll {mode.title()} Configuration

The CSV files use the documented `device`, `poll`, and `ref` records. Each
reference is read-only. The command reference uses `--once` so the first run is
bounded.

Review the route environment variables and every CSV row before use. Run each
route command from the directory that contains its CSV file.

Probe configurations expose raw 16-bit words or raw Boolean bytes. They do not
claim that an unknown datatype or byte order is correct.
"""


def _witte_readme(mode: str, *, live_read_seconds: int) -> str:
    return f"""# Witte Modbus Poll {mode.title()} Automation

This target does not contain a synthetic `.mbp` or `.mbw` file. The PowerShell
scripts use the documented `Mbpoll.Application` and `Mbpoll.Document`
Automation objects. Modbus Poll creates and saves each native `.mbp` document.

Use this target only on a reviewed Windows test system with a licensed or valid
evaluation copy of Modbus Poll. Set the route host and port variables. Inspect
`read-plan.csv`. Then run one route script. The script requires the operator to
type `READ` before it connects. It closes the connection after at most
{live_read_seconds} seconds and releases all Automation objects in a `finally`
block.

The generator requires a scan interval of at least
{WITTE_DESKTOP_MIN_SCAN_INTERVAL_MS} milliseconds for each read. It also limits
each route to {WITTE_DESKTOP_MAX_ROUTE_READS_PER_SECOND} read requests per
second in total. Generation stops with a hold if the plan exceeds either
limit. Each script checks both limits again before it asks for confirmation.

The scripts call only `ReadCoils`, `ReadDiscreteInputs`,
`ReadHoldingRegisters`, and `ReadInputRegisters`. They do not call Modbus write
methods or perform address scans.
"""


def _witte_v12_readme(mode: str) -> str:
    return f"""# Witte Modbus Poll v12 XML {mode.title()} Documents

These files follow the human-readable XML structure that Witte publishes for
Modbus Poll version 12. Each XML file represents one compiled read request.
The document uses base-zero protocol offsets and is disabled by default.

The files do not store a connection endpoint. Configure the reviewed Modbus
TCP or serial connection in Modbus Poll. Inspect the unit ID, function, address,
quantity, and scan rate in each XML file before you open or enable it.

Only functions 01 through 04 are present. No opaque `.mbp` or `.mbw` content is
generated. Native application acceptance still requires a Windows test with
the intended Modbus Poll version.
"""
