"""Deterministic, read-only Node-RED flow exporter."""

from __future__ import annotations

import hashlib
import math
from typing import Any, Mapping

from .exporters import (
    Artifact,
    ExportResult,
    Finding,
    block_area,
    block_function_code,
    block_id,
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
    point_area,
    point_datatype,
    point_id,
    point_name,
    point_protocol_offset,
    point_word_count,
    points_for_block,
    points_from_map,
    preflight_common,
    read_plan_hash,
    safe_slug,
    stable_json,
    target_manifest,
)


ADAPTER_VERSION = "2.2.0"
TARGET = "node-red"
_PROBE_BYTE_ORDER_LAYOUTS = ("ABCD", "BADC", "CDAB", "DCBA")
_PROBE_32_BIT_INTERPRETATIONS = ("uint32", "int32", "float32")
_FINAL_WIDTHS = {"int16": 1, "uint16": 1, "int32": 2, "uint32": 2, "float32": 2, "bool": 1, "boolean": 1}
_LAYOUT32 = {"ABCD": "ABCD", "BADC": "BADC", "CDAB": "CDAB", "DCBA": "DCBA", "BE_BE": "ABCD", "LE_BE": "BADC", "BE_LE": "CDAB", "LE_LE": "DCBA"}
_LAYOUT16 = {None: "AB", "AB": "AB", "ABCD": "AB", "BE_BE": "AB", "BA": "BA", "BADC": "BA", "LE_BE": "BA"}


def _finite_transform(value: Any) -> bool:
    if value is None:
        return True  # Optional absent/null transforms mean identity, never zero.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _final_decode_findings(canonical_map: Mapping[str, Any]) -> list[Finding]:
    findings = []
    for index, point in enumerate(points_from_map(canonical_map)):
        path = f"points[{index}]"
        datatype = point_datatype(point)
        width = point_word_count(point)
        area = point_area(point)
        order = point_byte_order(point)
        if datatype not in _FINAL_WIDTHS:
            if datatype is not None:
                findings.append(Finding("error", "NODE_RED_FINAL_DATATYPE_UNSUPPORTED", "Final decoding supports int16, uint16, int32, uint32, float32 and scalar FC01/02 bool only. Use an explicit raw probe for other semantics.", f"{path}.datatype"))
            continue
        if width != _FINAL_WIDTHS[datatype]:
            findings.append(Finding("error", "NODE_RED_FINAL_WIDTH_MISMATCH", "Point width does not match its final datatype.", f"{path}.word_span"))
        bit = datatype in {"bool", "boolean"}
        if bit != (area in {"coil", "discrete-input"}):
            findings.append(Finding("error", "NODE_RED_FINAL_AREA_DATATYPE_MISMATCH", "Scalar Boolean decoding requires FC01/02; numeric decoding requires FC03/04.", f"{path}.datatype"))
        supported_order = order in (None, "AB", "ABCD", "BE_BE") if bit else order in (_LAYOUT16 if width == 1 else _LAYOUT32)
        if order is not None and not supported_order:
            findings.append(Finding("error", "NODE_RED_FINAL_LAYOUT_UNSUPPORTED", "Final byte layout is not supported for this datatype width.", f"{path}.byte_order"))
        scale = point.get("scale")
        offset = point.get("engineering_offset", point.get("offset"))
        for key, value in (("scale", scale), ("engineering_offset", offset)):
            if not _finite_transform(value):
                findings.append(Finding("error", "NODE_RED_FINAL_TRANSFORM_INVALID", "A supplied transform must be a finite number; missing/null means identity.", f"{path}.{key}"))
        if bit and (scale not in (None, 1) or offset not in (None, 0)):
            findings.append(Finding("error", "NODE_RED_FINAL_BIT_TRANSFORM_UNSUPPORTED", "Scalar Boolean final values require identity transforms.", path))
    return findings


def export_node_red(
    canonical_map: Mapping[str, Any],
    read_plan: Mapping[str, Any],
    *,
    mode: str = "final",
    options: Mapping[str, Any] | None = None,
) -> ExportResult:
    """Generate a disabled Node-RED flow from one canonical read plan.

    Each flow uses one trigger node and a response-driven sequencer. Probe
    flows keep that trigger manual and one-shot; final flows use one bounded
    five-second poll trigger. The sequencer shares one ``modbus-flex-getter``
    per route and never has more than one request in flight. Final flows decode
    only confirmed layouts.
    """

    mode = normalize_mode(mode)
    options = dict(options or {})
    findings = list(preflight_common(canonical_map, read_plan, mode=mode))
    if mode == "final":
        findings.extend(_final_decode_findings(canonical_map))
    if has_errors(findings):
        return held_result(
            TARGET,
            canonical_map,
            read_plan,
            mode=mode,
            adapter_version=ADAPTER_VERSION,
            findings=findings,
        )

    map_digest = canonical_map_hash(canonical_map)
    plan_digest = read_plan_hash(read_plan)
    seed = f"{TARGET}:{map_digest}:{plan_digest}:{mode}"
    flow_id = _node_id(seed, "tab")
    flow_label = str(
        options.get("flow_label", f"Modbus Tool Pack - {mode.title()}")
    ).strip() or f"Modbus Tool Pack - {mode.title()}"

    blocks = sorted(
        enumerate(blocks_from_plan(read_plan)),
        key=lambda item: block_id(item[1], item[0]),
    )
    routes = sorted({block_route_id(block) for _, block in blocks})
    multiple_routes = len(routes) > 1
    route_env = {
        route: env_prefix_for_route(route, multiple_routes=multiple_routes)
        for route in routes
    }
    routes_by_prefix: dict[str, list[str]] = {}
    for route, prefix in route_env.items():
        routes_by_prefix.setdefault(prefix, []).append(route)
    for prefix, matching_routes in sorted(routes_by_prefix.items()):
        if len(matching_routes) > 1:
            findings.append(
                Finding(
                    "error",
                    "NODE_RED_ENV_PREFIX_COLLISION",
                    f"Routes {matching_routes!r} collapse to the same environment prefix {prefix!r}.",
                    "routes",
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
        )

    nodes: list[dict[str, Any]] = [
        {
            "id": flow_id,
            "type": "tab",
            "label": flow_label,
            "disabled": True,
            "info": (
                "Generated by modbus-skills. This flow is disabled by default. "
                "Review the endpoint, unit IDs, and ranges before enabling it. "
                "Open the modbus-dashboard endpoint for a read-only live capture view."
            ),
            "env": [],
        }
    ]

    clients: dict[str, str] = {}
    for route in routes:
        prefix = route_env[route]
        client_id = _node_id(seed, f"client:{route}")
        clients[route] = client_id
        nodes.append(
            {
                "id": client_id,
                "type": "modbus-client",
                "name": f"Modbus route: {route}",
                "clienttype": "tcp",
                "bufferCommands": True,
                "stateLogEnabled": False,
                "queueLogEnabled": False,
                "failureLogEnabled": True,
                "tcpHost": "${" + prefix + "_HOST}",
                "tcpPort": "${" + prefix + "_PORT}",
                "tcpType": "DEFAULT",
                "serialPort": "",
                "serialType": "RTU-BUFFERD",
                "serialBaudrate": "9600",
                "serialDatabits": "8",
                "serialStopbits": "1",
                "serialParity": "none",
                "serialConnectionDelay": "100",
                "unit_id": 1,
                "commandDelay": 1,
                "clientTimeout": 1000,
                "reconnectOnTimeout": True,
                "reconnectTimeout": 2000,
                "parallelUnitIdsAllowed": False,
                "showWarnings": True,
                "showLogs": False,
            }
        )

    block_specs: list[dict[str, Any]] = []
    for order_index, (source_index, block) in enumerate(blocks):
        identifier = block_id(block, source_index)
        route = block_route_id(block)
        area = block_area(block)
        unit = block_unit_id(block)
        start = block_start(block)
        quantity = block_quantity(block)
        function_code = block_function_code(block)
        assert area is not None
        assert unit is not None
        assert start is not None
        assert quantity is not None
        assert function_code is not None

        specs = []
        for point_index, point in enumerate(
            points_for_block(canonical_map, block, source_index)
        ):
            identifier_for_point = point_id(point, point_index) or f"point-{point_index + 1}"
            point_start = point_protocol_offset(point)
            specs.append(
                {
                    "point_id": identifier_for_point,
                    "name": point_name(point, identifier_for_point),
                    "relative_offset": (point_start - start) if point_start is not None else None,
                    "word_count": point_word_count(point),
                    "datatype": point_datatype(point),
                    "byte_order": (
                        (_LAYOUT16 if point_word_count(point) == 1 else _LAYOUT32).get(point_byte_order(point), point_byte_order(point))
                        if mode == "final" else point_byte_order(point)
                    ),
                    "byte_order_confirmed": point.get("byte_order_confirmed", point.get("byte_layout_confirmed", True)),
                    "scale": 1 if point.get("scale") is None else point.get("scale"),
                    "offset": 0 if point.get("engineering_offset", point.get("offset")) is None else point.get("engineering_offset", point.get("offset")),
                    "engineering_unit": point.get(
                        "engineering_unit", point.get("unit")
                    ),
                }
            )

        block_specs.append(
            {
                "block_id": identifier,
                "route_id": route,
                "route_index": routes.index(route),
                "unit_id": unit,
                "area": area,
                "function_code": function_code,
                "start_offset": start,
                "quantity": quantity,
                "mode": mode,
                "point_specs": specs,
            }
        )

    inject_id = _node_id(seed, "inject:plan")
    sequencer_id = _node_id(seed, "sequencer")
    continue_id = _node_id(seed, "continue-plan")
    read_error_id = _node_id(seed, "read-error-lane")
    response_gate_id = _node_id(seed, "response-gate")
    decode_id = _node_id(seed, "derive")
    capture_id = _node_id(seed, "capture")
    terminal_gate_id = _node_id(seed, "terminal-gate")
    capture_file_id = _node_id(seed, "capture-file")
    watchdog_id = _node_id(seed, "watchdog")
    watchdog_reset_id = _node_id(seed, "watchdog-reset")
    error_debug_id = _node_id(seed, "errors")
    watchdog_debug_id = _node_id(seed, "watchdog-debug")
    plan_status_debug_id = _node_id(seed, "plan-status")
    dashboard_http_id = _node_id(seed, "dashboard-http")
    dashboard_render_id = _node_id(seed, "dashboard-render")
    dashboard_response_id = _node_id(seed, "dashboard-response")
    primary_group_id = _node_id(seed, "group-primary")
    health_group_id = _node_id(seed, "group-health")
    retry_group_id = _node_id(seed, "group-retry")
    dashboard_group_id = _node_id(seed, "group-dashboard")
    primary_comment_id = _node_id(seed, "comment-primary")
    health_comment_id = _node_id(seed, "comment-health")
    retry_comment_id = _node_id(seed, "comment-retry")
    dashboard_comment_id = _node_id(seed, "comment-dashboard")
    safety_comment_id = _node_id(seed, "comment-safety")
    read_ids = [_node_id(seed, f"read:{route}") for route in routes]
    route_function_codes = {
        route: sorted(
            {
                int(block["function_code"])
                for block in block_specs
                if block["route_id"] == route
            }
        )
        for route in routes
    }
    polling_enabled = mode == "final"
    route_span = max(0, len(routes) - 1) * 70
    main_y = 180 + route_span // 2
    capture_y = 380 + route_span
    terminal_y = capture_y - 80
    retry_y = capture_y + 160
    lower_y = retry_y + 220
    dashboard_y = lower_y
    safety_y = dashboard_y + 300

    nodes.extend(
        [
            {
                "id": inject_id,
                "type": "inject",
                "z": flow_id,
                "g": primary_group_id,
                "modbusSkillsRole": "live-poll" if polling_enabled else "manual-start",
                "name": "01 Live poll (5s)" if polling_enabled else "01 Start bounded plan",
                "props": [{"p": "payload"}],
                "repeat": "5" if polling_enabled else "",
                "crontab": "",
                "once": False,
                "onceDelay": 0.1,
                "payload": stable_json({"action": "start"}, pretty=False),
                "payloadType": "json",
                "x": 200,
                "y": main_y,
                "wires": [[sequencer_id]],
            },
            {
                "id": sequencer_id,
                "type": "function",
                "z": flow_id,
                "g": primary_group_id,
                "modbusSkillsRole": "sequencer",
                "name": "02 Sequence read blocks",
                "func": _sequencer_function(block_specs, len(routes), retry_limit=0 if mode == "probe" else 1),
                "modbusSkillsBlocks": block_specs,
                "outputs": len(routes) + 1,
                "timeout": 0,
                "noerr": 0,
                "initialize": "",
                "finalize": "",
                "libs": [],
                "x": 450,
                "y": main_y,
                "wires": [
                    [read_ids[index], watchdog_id] for index in range(len(routes))
                ]
                + [[plan_status_debug_id, capture_id]],
            },
        ]
    )
    for route_index, route in enumerate(routes):
        nodes.append(
            {
                "id": read_ids[route_index],
                "type": "modbus-flex-getter",
                "z": flow_id,
                "g": primary_group_id,
                "name": f"03 Read route: {route}",
                "showStatusActivities": True,
                "showErrors": True,
                "showWarnings": True,
                "server": clients[route],
                "modbusSkillsAllowedFunctionCodes": route_function_codes[route],
                "useIOFile": False,
                "ioFile": "",
                "useIOForPayload": False,
                "emptyMsgOnFail": True,
                "keepMsgProperties": True,
                "x": 760,
                "y": 180 + route_index * 70,
                "wires": [[response_gate_id], [read_error_id]],
            }
        )

    nodes.extend(
        [
            {
                "id": read_error_id,
                "type": "function",
                "z": flow_id,
                "g": primary_group_id,
                "name": "03a Read error lane",
                "func": (
                    "if (!(msg.modbusSkillsReadError || msg.error || msg.modbusError)) return null;\n"
                    "return msg;"
                ),
                "outputs": 1,
                "timeout": 0,
                "noerr": 0,
                "initialize": "",
                "finalize": "",
                "libs": [],
                "x": 760,
                "y": terminal_y,
                "wires": [[terminal_gate_id]],
            },
            {
                "id": response_gate_id,
                "type": "function",
                "z": flow_id,
                "g": primary_group_id,
                "name": "04 Validate response",
                "func": _response_gate_function(),
                "outputs": 2,
                "timeout": 0,
                "noerr": 0,
                "initialize": "",
                "finalize": "",
                "libs": [],
                "x": 1030,
                "y": main_y,
                "wires": [[decode_id], [terminal_gate_id]],
            },
            {
                "id": decode_id,
                "type": "function",
                "z": flow_id,
                "g": primary_group_id,
                "name": "05 Decode points",
                "func": _derive_function(),
                "outputs": 1,
                "timeout": 0,
                "noerr": 0,
                "initialize": "",
                "finalize": "",
                "libs": [],
                "x": 1270,
                "y": main_y,
                "wires": [[terminal_gate_id]],
            },
            {
                "id": terminal_gate_id,
                "type": "function",
                "z": flow_id,
                "g": primary_group_id,
                "name": "06 Terminal gate",
                "func": _terminal_gate_function(),
                "outputs": 2,
                "timeout": 0,
                "noerr": 0,
                "initialize": "",
                "finalize": "",
                "libs": [],
                "x": 1510,
                "y": terminal_y,
                "wires": [[watchdog_reset_id], [error_debug_id]],
            },
            {
                "id": capture_id,
                "type": "function",
                "z": flow_id,
                "g": primary_group_id,
                "name": "07 Build capture/v1",
                "func": _capture_function(
                    map_digest,
                    plan_digest,
                    [block["block_id"] for block in block_specs],
                    sorted({block["unit_id"] for block in block_specs}),
                    retry_limit=0 if mode == "probe" else 1,
                ),
                "outputs": 2,
                "timeout": 0,
                "noerr": 0,
                "initialize": "",
                "finalize": "",
                "libs": [],
                "x": 1780,
                "y": capture_y,
                "wires": [[capture_file_id], [continue_id]],
            },
            {
                "id": continue_id,
                "type": "function",
                "z": flow_id,
                "g": primary_group_id,
                "name": "07a Continue plan",
                "func": "return msg;",
                "outputs": 1,
                "timeout": 0,
                "noerr": 0,
                "initialize": "",
                "finalize": "",
                "libs": [],
                "x": 450,
                "y": capture_y,
                "wires": [[sequencer_id]],
            },
            {
                "id": capture_file_id,
                "type": "file",
                "z": flow_id,
                "g": primary_group_id,
                "name": "08 Write capture.json",
                "filename": "filename",
                "filenameType": "msg",
                "appendNewline": False,
                "createDir": True,
                "overwriteFile": "true",
                "encoding": "none",
                "x": 2050,
                "y": capture_y,
                "wires": [[]],
            },
            {
                "id": watchdog_reset_id,
                "type": "function",
                "z": flow_id,
                "g": retry_group_id,
                "name": "Retry / watchdog handoff",
                "func": (
                    "const failed = Boolean(msg.modbusSkillsReadError || msg.error || msg.modbusError);\n"
                    "msg.reset = true;\n"
                    "return [msg, failed ? msg : null];"
                ),
                "outputs": 2,
                "timeout": 0,
                "noerr": 0,
                "initialize": "",
                "finalize": "",
                "libs": [],
                "x": 600,
                "y": retry_y,
                "wires": [[watchdog_id, capture_id], [error_debug_id]],
            },
            {
                "id": watchdog_id,
                "type": "trigger",
                "z": flow_id,
                "g": retry_group_id,
                "name": "Watchdog timeout",
                "op1": "",
                "op2": stable_json({"state": "timeout"}, pretty=False),
                "op1type": "nul",
                "op2type": "json",
                "duration": "${MODBUS_WATCHDOG_MS}",
                "extend": True,
                "overrideDelay": False,
                "units": "ms",
                "reset": "",
                "bytopic": "all",
                "topic": "topic",
                "outputs": 1,
                "x": 900,
                "y": retry_y,
                "wires": [[terminal_gate_id, watchdog_debug_id]],
            },
        ]
    )

    status_id = _node_id(seed, "status")
    queue_id = _node_id(seed, "queue-signal")
    queue_debug_id = _node_id(seed, "queue-debug")
    catch_id = _node_id(seed, "catch")
    catch_debug_id = _node_id(seed, "catch-debug")
    nodes.extend(
        [
            {
                "id": status_id,
                "type": "status",
                "z": flow_id,
                "g": health_group_id,
                "name": "Status events",
                "scope": read_ids,
                "x": 220,
                "y": lower_y + 70,
                "wires": [[queue_id]],
            },
            {
                "id": queue_id,
                "type": "function",
                "z": flow_id,
                "g": health_group_id,
                "name": "Normalize queue status",
                "func": (
                    "const status = msg.status || {};\n"
                    "msg.topic = 'modbus/queue-status';\n"
                    "msg.payload = {\n"
                    "  source_id: status.source ? status.source.id : null,\n"
                    "  fill: status.fill || null,\n"
                    "  shape: status.shape || null,\n"
                    "  text: status.text || null\n"
                    "};\n"
                    "return msg;"
                ),
                "outputs": 1,
                "timeout": 0,
                "noerr": 0,
                "initialize": "",
                "finalize": "",
                "libs": [],
                "x": 500,
                "y": lower_y + 70,
                "wires": [[queue_debug_id]],
            },
            {
                "id": catch_id,
                "type": "catch",
                "z": flow_id,
                "g": health_group_id,
                "name": "Caught Modbus errors",
                "scope": read_ids,
                "uncaught": False,
                "x": 220,
                "y": lower_y + 140,
                "wires": [[catch_debug_id]],
            },
            {
                "id": error_debug_id,
                "type": "debug",
                "z": flow_id,
                "name": "Errors → Debug",
                "active": True,
                "tosidebar": True,
                "console": False,
                "tostatus": False,
                "complete": "true",
                "targetType": "full",
                "g": retry_group_id,
                "x": 600,
                "y": retry_y + 150,
                "wires": [],
            },
            {
                "id": plan_status_debug_id,
                "type": "debug",
                "z": flow_id,
                "g": health_group_id,
                "name": "Plan status → Debug",
                "active": True,
                "tosidebar": True,
                "console": False,
                "tostatus": False,
                "complete": "payload",
                "targetType": "msg",
                "x": 800,
                "y": lower_y + 210,
                "wires": [],
            },
            {
                "id": queue_debug_id,
                "type": "debug",
                "z": flow_id,
                "g": health_group_id,
                "name": "Queue → Debug",
                "active": False,
                "tosidebar": True,
                "console": False,
                "tostatus": False,
                "complete": "payload",
                "targetType": "msg",
                "x": 1000,
                "y": lower_y + 210,
                "wires": [],
            },
            {
                "id": catch_debug_id,
                "type": "debug",
                "z": flow_id,
                "g": health_group_id,
                "name": "Caught errors → Debug",
                "active": True,
                "tosidebar": True,
                "console": False,
                "tostatus": False,
                "complete": "true",
                "targetType": "full",
                "x": 500,
                "y": lower_y + 140,
                "wires": [],
            },
            {
                "id": watchdog_debug_id,
                "type": "debug",
                "z": flow_id,
                "g": retry_group_id,
                "name": "Watchdog → Debug",
                "active": True,
                "tosidebar": True,
                "console": False,
                "tostatus": False,
                "complete": "payload",
                "targetType": "msg",
                "x": 1050,
                "y": retry_y + 150,
                "wires": [],
            },
        ]
    )

    dashboard_nodes = [
        {
            "id": dashboard_http_id,
            "type": "http in",
            "z": flow_id,
            "g": dashboard_group_id,
            "name": "Dashboard GET /modbus-dashboard",
            "url": "/modbus-dashboard",
            "method": "get",
            "upload": False,
            "swaggerDoc": "",
            "x": 1500,
            "y": dashboard_y + 70,
            "wires": [[dashboard_render_id]],
        },
        {
            "id": dashboard_render_id,
            "type": "function",
            "z": flow_id,
            "g": dashboard_group_id,
            "name": "Render live dashboard",
            "func": _dashboard_function(polling_enabled=polling_enabled),
            "outputs": 1,
            "timeout": 0,
            "noerr": 0,
            "initialize": "",
            "finalize": "",
            "libs": [],
            "x": 1770,
            "y": dashboard_y + 70,
            "wires": [[dashboard_response_id]],
        },
        {
            "id": dashboard_response_id,
            "type": "http response",
            "z": flow_id,
            "g": dashboard_group_id,
            "name": "Dashboard response",
            "statusCode": "",
            "headers": {},
            "x": 2070,
            "y": dashboard_y + 70,
            "wires": [],
        },
    ]
    nodes.extend(dashboard_nodes)

    primary_nodes = [
        inject_id,
        sequencer_id,
        *read_ids,
        read_error_id,
        response_gate_id,
        decode_id,
        terminal_gate_id,
        continue_id,
        capture_id,
        capture_file_id,
    ]
    health_nodes = [
        status_id,
        queue_id,
        catch_id,
        catch_debug_id,
        plan_status_debug_id,
        queue_debug_id,
    ]
    retry_nodes = [watchdog_id, watchdog_reset_id, watchdog_debug_id, error_debug_id]
    nodes.extend(
        [
            {
                "id": primary_comment_id,
                "type": "comment",
                "z": flow_id,
                "name": "PRIMARY READ PATH · one request in flight",
                "info": "Poll or manual trigger → sequenced FC01-FC04 reads → validated decode → capture/v1.",
                "x": 300,
                "y": 65,
                "wires": [],
            },
            {
                "id": health_comment_id,
                "type": "comment",
                "z": flow_id,
                "name": "HEALTH & FAILURE HANDLING · status / timeout / errors",
                "info": "Diagnostics are isolated below the primary read path and never create extra reads.",
                "x": 360,
                "y": lower_y - 35,
                "wires": [],
            },
            {
                "id": retry_comment_id,
                "type": "comment",
                "z": flow_id,
                "name": "RETRY & WATCHDOG · bounded recovery",
                "info": ("No probe retries." if mode == "probe" else "One retry per failed block.") + " Watchdog is reset only after a validated response.",
                "x": 560,
                "y": retry_y - 35,
                "wires": [],
            },
            {
                "id": dashboard_comment_id,
                "type": "comment",
                "z": flow_id,
                "name": "LIVE CAPTURE DASHBOARD · GET /modbus-dashboard",
                "info": "Read-only HTML view of the latest capture rows. Refreshes every three seconds.",
                "x": 1550,
                "y": dashboard_y - 35,
                "wires": [],
            },
            {
                "id": safety_comment_id,
                "type": "comment",
                "z": flow_id,
                "name": (
                    "SAFETY: disabled by default · one bounded 5s poll · no writes · max in-flight = 1"
                    if polling_enabled
                    else "SAFETY: disabled by default · manual only · no writes · max in-flight = 1"
                ),
                "info": "Review endpoint, unit IDs, ranges, and MODBUS_CAPTURE_PATH before enabling.",
                "x": 500,
                "y": safety_y,
                "wires": [],
            },
            {
                "id": primary_group_id,
                "type": "group",
                "z": flow_id,
                "name": "PRIMARY READ PATH",
                "style": {
                    "fill": "#d8ecff",
                    "fill-opacity": 0.35,
                    "label": True,
                    "color": "#1769aa",
                },
                "nodes": primary_nodes,
                "x": 100,
                "y": 115,
                "w": 2200,
                "h": capture_y - 55,
            },
            {
                "id": health_group_id,
                "type": "group",
                "z": flow_id,
                "name": "HEALTH & FAILURE HANDLING",
                "style": {
                    "fill": "#fff1d6",
                    "fill-opacity": 0.35,
                    "label": True,
                    "color": "#9a6700",
                },
                "nodes": health_nodes,
                "x": 100,
                "y": lower_y,
                "w": 1100,
                "h": 260,
            },
            {
                "id": retry_group_id,
                "type": "group",
                "z": flow_id,
                "name": "RETRY & WATCHDOG",
                "style": {
                    "fill": "#f4e3ff",
                    "fill-opacity": 0.35,
                    "label": True,
                    "color": "#6b3f8f",
                },
                "nodes": retry_nodes,
                "x": 350,
                "y": retry_y,
                "w": 900,
                "h": 180,
            },
            {
                "id": dashboard_group_id,
                "type": "group",
                "z": flow_id,
                "name": "LIVE CAPTURE DASHBOARD",
                "style": {
                    "fill": "#dff5e1",
                    "fill-opacity": 0.35,
                    "label": True,
                    "color": "#28743c",
                },
                "nodes": [node["id"] for node in dashboard_nodes],
                "x": 1400,
                "y": dashboard_y,
                "w": 1000,
                "h": 140,
            },
        ]
    )

    node_ids = [node["id"] for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        findings.append(
            Finding(
                "error",
                "NODE_ID_COLLISION",
                "Deterministic Node-RED node IDs collided.",
            )
        )
        return held_result(
            TARGET,
            canonical_map,
            read_plan,
            mode=mode,
            adapter_version=ADAPTER_VERSION,
            findings=findings,
        )

    environment = []
    for route in routes:
        prefix = route_env[route]
        environment.extend([f"{prefix}_HOST", f"{prefix}_PORT"])
    environment.extend(["MODBUS_WATCHDOG_MS", "MODBUS_CAPTURE_PATH"])
    manifest = target_manifest(
        target=TARGET,
        profile=None,
        mode=mode,
        adapter_version=ADAPTER_VERSION,
        canonical_map=canonical_map,
        read_plan=read_plan,
        findings=findings,
        extra={
            "disabled_by_default": True,
            "required_node_module": "node-red-contrib-modbus",
            "environment": environment,
            "physical_read_nodes": len(read_ids),
            "read_block_ids": [block_id(block, index) for index, block in blocks],
            "read_node_type": "modbus-flex-getter",
            "trigger_type": "sequenced-live-poll" if polling_enabled else "sequenced-manual-plan",
            "trigger_nodes": 1,
            **({"poll_interval_seconds": 5} if polling_enabled else {}),
            "sequencing": "response-driven",
            "max_in_flight": 1,
            "capture_contract": "capture/v1",
            "scheduled_polling": polling_enabled,
            "numeric_representation": {
                "kind": "validated-final-scalars" if mode == "final" else "raw-only-with-layout-candidates",
                "final_datatypes": sorted(_FINAL_WIDTHS) if mode == "final" else [],
                "raw_values_preserved": True,
                "semantic_decode_errors_retried": False,
            },
            "dashboard": {
                "type": "core-http",
                "endpoint": "/modbus-dashboard",
                "nodes": 3,
                "read_only": True,
                "auto_refresh_seconds": 3,
            },
            **(
                {
                    "probe_trigger_type": "sequenced-manual-plan",
                    "probe_trigger_nodes": 1,
                    "probe_polling": False,
                    "probe_byte_order_candidates": list(
                        _PROBE_BYTE_ORDER_LAYOUTS
                    ),
                    "probe_candidate_interpretations": list(
                        _PROBE_32_BIT_INTERPRETATIONS
                    ),
                    "probe_candidate_source": "one-read-immutable-raw-words",
                }
                if mode == "probe"
                else {}
            ),
        },
    )
    artifacts = (
        Artifact.text(
            "node-red/flow.json",
            "application/json",
            stable_json(nodes),
            "node-red-flow",
        ),
        Artifact.text(
            "node-red/manifest.json",
            "application/json",
            stable_json(manifest),
            "target-manifest",
        ),
        Artifact.text(
            "node-red/README.md",
            "text/markdown",
            _readme(mode=mode, environment=environment),
            "operator-instructions",
        ),
    )
    return ExportResult(
        target=TARGET,
        status="generated",
        mode=mode,
        map_hash=map_digest,
        read_plan_hash=plan_digest,
        adapter_version=ADAPTER_VERSION,
        findings=tuple(findings),
        artifacts=artifacts,
    )


def _dashboard_function(*, polling_enabled: bool) -> str:
    """Render a small read-only dashboard using only Node-RED core nodes."""

    empty_message = (
        "No capture rows yet. Enable the flow to start live polling."
        if polling_enabled
        else "No capture rows yet. Start the bounded plan once."
    )
    return """const samples = Array.isArray(flow.get('modbusSkillsCapture'))
  ? flow.get('modbusSkillsCapture') : [];
const running = Boolean(flow.get('modbusSkillsRunning'));
const runId = flow.get('modbusSkillsRunId') || 'not started';
const queue = Array.isArray(flow.get('modbusSkillsQueue'))
  ? flow.get('modbusSkillsQueue').length : 0;
const active = flow.get('modbusSkillsActiveBlockId') || 'idle';
const escapeHtml = (value) => String(value == null ? '' : value)
  .split('&').join('&amp;').split('<').join('&lt;')
  .split('>').join('&gt;').split('\\"').join('&quot;')
  .split("'").join('&#39;');
const rows = samples.slice(-50).reverse().map((sample) => {
  const state = sample.success === false ? 'ERROR' : 'OK';
  const raw = Array.isArray(sample.raw_words) ? sample.raw_words.join(', ') : '';
  const derived = sample.derived_values || {};
  const engineering = derived.engineering_value;
  const validValue = typeof engineering === 'boolean' ||
    (typeof engineering === 'number' && Number.isFinite(engineering));
  const decoded = sample.success === true && derived.decode_status === 'decoded' && validValue;
  const value = decoded ? String(engineering)
    : (sample.success === false ? 'Unavailable (error)' : 'Unavailable (raw only)');
  const unit = decoded && derived.engineering_unit ? ` ${derived.engineering_unit}` : '';
  return `<tr><td>${escapeHtml(state)}</td><td>${escapeHtml(sample.unit_id)}</td>` +
    `<td>${escapeHtml(sample.point_id)}</td><td>${escapeHtml(raw)}</td>` +
    `<td>${escapeHtml(value)}${escapeHtml(unit)}</td><td>${escapeHtml(sample.timestamp)}</td></tr>`;
}).join('');
const empty = '<tr><td colspan=\"6\" class=\"empty\">""" + empty_message + """</td></tr>';
msg.headers = {'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store'};
msg.payload = `<!doctype html><html><head><meta charset=\"utf-8\">` +
  `<meta http-equiv=\"refresh\" content=\"3\"><title>Modbus live dashboard</title>` +
  `<style>body{font:14px system-ui;margin:24px;background:#f7fafc;color:#1f2937}` +
  `h1{margin:0 0 6px;color:#163b5c}.meta{display:flex;gap:18px;flex-wrap:wrap}` +
  `.pill{background:#e8f1f8;border-radius:999px;padding:6px 10px}` +
  `table{border-collapse:collapse;width:100%;margin-top:20px;background:white}` +
  `th,td{border:1px solid #d9e2ec;padding:7px;text-align:left}` +
  `th{background:#d8ecff;color:#163b5c}.ok{color:#28743c}.empty{color:#64748b;text-align:center}` +
  `small{color:#64748b}</style></head><body>` +
  `<h1>Live generator readings</h1><small>Read-only view · validated final scalar values only; probes stay raw · refreshes every 3 seconds</small>` +
  `<div class=\"meta\"><span class=\"pill\">Run: ${escapeHtml(runId)}</span>` +
  `<span class=\"pill\">State: ${running ? 'RUNNING' : 'IDLE'}</span>` +
  `<span class=\"pill\">Pending reads: ${escapeHtml(queue)}</span>` +
  `<span class=\"pill\">Current read: ${escapeHtml(active)}</span>` +
  `<span class=\"pill\">Samples: ${samples.length}</span></div>` +
  `<table><thead><tr><th>Read status</th><th>Generator</th><th>Point</th><th>Register data</th>` +
  `<th>Engineering value</th><th>Sample time</th></tr></thead><tbody>${rows || empty}</tbody></table>` +
  `</body></html>`;
return msg;"""


def _sequencer_function(block_specs: list[dict[str, Any]], route_count: int, *, retry_limit: int = 1) -> str:
    return (
        f"const blocks = {stable_json(block_specs, pretty=False)};\n"
        f"const routeCount = {route_count};\n"
        "const statusIndex = routeCount;\n"
        "const output = (index, value) => {\n"
        "  const values = Array(routeCount + 1).fill(null);\n"
        "  values[index] = value;\n"
        "  return values;\n"
        "};\n"
        f"const retryLimit = {retry_limit};\n"
        "const finalize = (state) => {\n"
        "  flow.set('modbusSkillsRunning', false);\n"
        "  flow.set('modbusSkillsQueue', []);\n"
        "  flow.set('modbusSkillsActiveBlockId', null);\n"
        "  return output(statusIndex, {\n"
        "    modbusSkillsFinalize: true,\n"
        f"    payload: {{state, request_count: blocks.length, max_in_flight: 1, retry_limit: {retry_limit}}}\n"
        "  });\n"
        "};\n"
        "const send = (block, attempt) => {\n"
        "  if (flow.get('modbusSkillsActiveBlockId')) {\n"
        "    return output(statusIndex, {payload: {state: 'in-flight'}});\n"
        "  }\n"
        "  const request = {\n"
        "    block_id: block.block_id, route_id: block.route_id, unit_id: block.unit_id,\n"
        "    area: block.area, function_code: block.function_code,\n"
        "    start_offset: block.start_offset, quantity: block.quantity,\n"
        "    mode: block.mode, point_specs: block.point_specs, attempt,\n"
        "    max_attempts: retryLimit + 1, started_at_ms: Date.now()\n"
        "  };\n"
        "  flow.set('modbusSkillsActiveBlockId', block.block_id);\n"
        "  return output(block.route_index, {\n"
        "    topic: block.block_id, modbusSkillsRequest: request,\n"
        "    payload: {fc: block.function_code, unitid: block.unit_id, address: block.start_offset, quantity: block.quantity}\n"
        "  });\n"
        "};\n"
        "const next = () => {\n"
        "  const queue = flow.get('modbusSkillsQueue') || [];\n"
        "  if (queue.length === 0) {\n"
        "    return finalize('drained');\n"
        "  }\n"
        "  const block = queue.shift();\n"
        "  flow.set('modbusSkillsQueue', queue);\n"
        "  return send(block, 0);\n"
        "};\n"
        "const action = msg && msg.payload ? msg.payload.action : null;\n"
        "if (action === 'cancel') return finalize('cancelled');\n"
        "if (action === 'start') {\n"
        "  if (flow.get('modbusSkillsRunning')) {\n"
        "    return output(statusIndex, {payload: {state: 'already-running'}});\n"
        "  }\n"
        "  flow.set('modbusSkillsRunning', true);\n"
        "  flow.set('modbusSkillsQueue', blocks.slice());\n"
        "  flow.set('modbusSkillsCapture', []);\n"
        "  flow.set('modbusSkillsCompletedRequestIds', []);\n"
        "  flow.set('modbusSkillsActiveBlockId', null);\n"
        "  flow.set('modbusSkillsRunId', `run-${Date.now()}`);\n"
        "  return next();\n"
        "}\n"
        "if (msg && msg.modbusSkillsRetry === true) {\n"
        "  const request = msg.modbusSkillsRequest || {};\n"
        "  const block = blocks.find((candidate) => candidate.block_id === request.block_id);\n"
        "  const attempt = Number.isInteger(request.attempt) ? request.attempt + 1 : 1;\n"
        "  if (!block || attempt > retryLimit) return next();\n"
        "  return send(block, attempt);\n"
        "}\n"
        "if (msg && msg.modbusSkillsContinue === true) return next();\n"
        "return output(statusIndex, {payload: {state: 'ignored-message'}});"
    )


def _derive_function() -> str:
    return """const block = msg.modbusSkillsRequest || {};
const pointSpecs = Array.isArray(block.point_specs) ? block.point_specs : [];
const byteLayouts32 = Object.freeze({
  ABCD: Object.freeze([0, 1, 2, 3]),
  BADC: Object.freeze([1, 0, 3, 2]),
  CDAB: Object.freeze([2, 3, 0, 1]),
  DCBA: Object.freeze([3, 2, 1, 0])
});
const decode32 = (sourceBytes, layout) => {
  const order = byteLayouts32[layout];
  if (!order) throw Error('unsupported-byte-layout');
  const buffer = Buffer.from(order.map((index) => sourceBytes[index]));
  const float32 = buffer.readFloatBE(0);
  return Object.freeze({
    bytes: Object.freeze(Array.from(buffer.values())),
    uint32: buffer.readUInt32BE(0),
    int32: buffer.readInt32BE(0),
    float32: Number.isFinite(float32) ? float32 : null
  });
};
const candidate = Array.isArray(msg.payload)
  ? msg.payload
  : (msg.payload && Array.isArray(msg.payload.data) ? msg.payload.data : []);
const rawValues = Object.freeze(candidate.slice());
msg.modbusSkills = { block, raw_values: rawValues };
const bitArea = block.function_code === 1 || block.function_code === 2;
const validRaw = (value) => bitArea
  ? (value === true || value === false || value === 0 || value === 1)
  : (typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 65535);
msg.payload = pointSpecs.map((spec) => {
  const offset = spec.relative_offset;
  const width = spec.word_count;
  const validSpan = Number.isInteger(offset) && offset >= 0 && Number.isInteger(width) && width > 0;
  const pointRawValues = Object.freeze(validSpan ? rawValues.slice(offset, offset + width) : []);
  const derived = Object.assign({}, spec, {raw_values: pointRawValues, decode_status: 'raw', decoded_value: null, engineering_value: null});
  try {
    if (!validSpan || pointRawValues.length !== width) throw Error('incomplete-point-data');
    if (!pointRawValues.every(validRaw)) throw Error('invalid-raw-value');
    const sourceBytes = bitArea ? [] : pointRawValues.flatMap((word) => [(word >>> 8) & 255, word & 255]);
    if (block.mode === 'probe') {
      if (!bitArea && width === 2) {
        derived.byte_order_candidates = Object.freeze(Object.fromEntries(
          Object.keys(byteLayouts32).map((layout) => [layout, decode32(sourceBytes, layout)])
        ));
      }
      return derived;
    }
    if (block.mode !== 'final') throw Error('unsupported-decode-mode');
    const type = spec.datatype;
    let value;
    if (bitArea) {
      if (width !== 1 || !['bool', 'boolean'].includes(type)) throw Error('unsupported-bit-datatype');
      value = pointRawValues[0] === true || pointRawValues[0] === 1;
    } else if (['int16', 'uint16'].includes(type) && width === 1) {
      if (!['AB', 'BA'].includes(spec.byte_order)) throw Error('unsupported-byte-layout');
      const buffer = Buffer.from(spec.byte_order === 'BA' ? sourceBytes.slice().reverse() : sourceBytes);
      value = type === 'int16' ? buffer.readInt16BE(0) : buffer.readUInt16BE(0);
    } else if (['int32', 'uint32', 'float32'].includes(type) && width === 2) {
      if (spec.byte_order_confirmed === false) throw Error('unconfirmed-byte-layout');
      value = decode32(sourceBytes, spec.byte_order)[type];
    } else throw Error('unsupported-final-datatype');
    if (typeof value !== 'boolean' && (typeof value !== 'number' || !Number.isFinite(value))) throw Error('nonfinite-decoded-value');
    const scale = spec.scale == null ? 1 : spec.scale;
    const additive = spec.offset == null ? 0 : spec.offset;
    if (typeof scale !== 'number' || !Number.isFinite(scale) || typeof additive !== 'number' || !Number.isFinite(additive)) throw Error('invalid-engineering-transform');
    if (bitArea && (scale !== 1 || additive !== 0)) throw Error('unsupported-bit-transform');
    const engineering = bitArea ? value : value * scale + additive;
    if (!bitArea && (!Number.isFinite(engineering) ||
        (type !== 'float32' && Number.isInteger(engineering) && !Number.isSafeInteger(engineering)))) throw Error('unsafe-engineering-value');
    derived.decoded_value = value;
    derived.engineering_value = engineering;
    derived.decode_status = 'decoded';
  } catch (error) {
    derived.decode_status = 'error';
    derived.decode_error = error.message;
  }
  return derived;
});
return msg;"""


def _capture_function(
    map_digest: str,
    plan_digest: str,
    block_ids: list[str],
    unit_ids: list[int],
    *, retry_limit: int = 1,
) -> str:
    return (
        f"const mapHash = {stable_json(map_digest, pretty=False)};\n"
        f"const planHash = {stable_json(plan_digest, pretty=False)};\n"
        f"const expectedBlockIds = {stable_json(block_ids, pretty=False)};\n"
        f"const expectedUnitIds = {stable_json(unit_ids, pretty=False)};\n"
        "const runId = flow.get('modbusSkillsRunId');\n"
        "if (msg.modbusSkillsFinalize === true) {\n"
        "  const completedRequestIds = flow.get('modbusSkillsCompletedRequestIds') || [];\n"
        "  const capture = {\n"
        "    schema_version: \"capture/v1\", capture_id: runId,\n"
        "    canonical_map_hash: mapHash, read_plan_hash: planHash,\n"
        "    expected_request_ids: expectedBlockIds.map((id) => `${runId}:${id}`),\n"
        "    expected_unit_ids: expectedUnitIds,\n"
        "    completed_request_ids: completedRequestIds,\n"
        "    runtime_metadata: {\n"
        f"      target: 'node-red', adapter_version: {stable_json(ADAPTER_VERSION, pretty=False)},\n"
        "      terminal_state: msg.payload && msg.payload.state, queue_depth: 0,\n"
        f"      max_in_flight: 1, retry_limit: {retry_limit}\n"
        "    },\n"
        "    samples: flow.get('modbusSkillsCapture') || []\n"
        "  };\n"
        "  return [{\n"
        "    filename: env.get('MODBUS_CAPTURE_PATH') || 'modbus-capture.json',\n"
        "    payload: JSON.stringify(capture, null, 2)\n"
        "  }, null];\n"
        "}\n"
        "const request = msg.modbusSkillsRequest || {};\n"
        "const requestId = `${runId}:${request.block_id}`;\n"
        "const timestamp = new Date().toISOString();\n"
        "const elapsed = Number.isFinite(request.started_at_ms) ? Date.now() - request.started_at_ms : null;\n"
        "const derived = Array.isArray(msg.payload) ? msg.payload : [];\n"
        "const successful = derived.length > 0 && !msg.error && !msg.modbusError && !msg.modbusSkillsReadError;\n"
        "const points = Array.isArray(request.point_specs) ? request.point_specs : [];\n"
        "const rawResponse = Array.isArray(msg.modbusSkillsRawValues) ? msg.modbusSkillsRawValues : [];\n"
        "const attemptSuffix = Number.isInteger(request.attempt) && request.attempt > 0 ? `:attempt-${request.attempt}` : '';\n"
        "const baseSample = (point, suffix = '') => ({\n"
        "  sample_id: `${requestId}:${point.point_id}${suffix}${attemptSuffix}`, request_id: requestId,\n"
        "  point_id: point.point_id, block_id: request.block_id, route: request.route_id, route_id: request.route_id,\n"
        "  unit_id: request.unit_id, area: request.area,\n"
        "  protocol_offset: request.start_offset + (Number.isInteger(point.relative_offset) ? point.relative_offset : 0),\n"
        "  timestamp, response_time_ms: elapsed\n"
        "});\n"
        "const samples = successful ? derived.map((point) => ({\n"
        "  ...baseSample(point),\n"
        "  status: point.decode_status === 'error' ? 'error' : 'success',\n"
        "  success: point.decode_status !== 'error', raw_words: point.raw_values || [],\n"
        "  ...((request.function_code === 1 || request.function_code === 2) ? {raw_response: rawResponse,\n"
        "    ...(Array.isArray(msg.modbusSkillsRawBytes) ? {raw_response_bytes: msg.modbusSkillsRawBytes} : {})} : {}),\n"
        "  ...(point.decode_status === 'error' ? {error: point.decode_error || 'decode-failed'} : {derived_values: point})\n"
        "})) : points.map((point) => ({\n"
        "  ...baseSample(point, ':error'),\n"
        "  status: 'error', success: false,\n"
        "  raw_words: rawResponse.slice(point.relative_offset, point.relative_offset + point.word_count),\n"
        "  raw_response: rawResponse,\n"
        "  ...(Array.isArray(msg.modbusSkillsRawBytes) ? {raw_response_bytes: msg.modbusSkillsRawBytes} : {}),\n"
        "  error: (msg.modbusSkillsReadError && msg.modbusSkillsReadError.reason) ||\n"
        "    (msg.payload && msg.payload.state) || (msg.error && msg.error.message) || 'read-failed'\n"
        "}));\n"
        "const existing = flow.get('modbusSkillsCapture') || [];\n"
        "existing.push(...samples);\n"
        "flow.set('modbusSkillsCapture', existing);\n"
        f"const retry = !successful && Number(request.attempt || 0) < {retry_limit};\n"
        "if (!retry) {\n"
        "  const completedRequestIds = flow.get('modbusSkillsCompletedRequestIds') || [];\n"
        "  if (!completedRequestIds.includes(requestId)) completedRequestIds.push(requestId);\n"
        "  flow.set('modbusSkillsCompletedRequestIds', completedRequestIds);\n"
        "}\n"
        "flow.set('modbusSkillsActiveBlockId', null);\n"
        "return [null, {\n"
        "  modbusSkillsContinue: !retry, modbusSkillsRetry: retry,\n"
        "  modbusSkillsRequest: request, payload: {action: retry ? 'retry' : 'next'}\n"
        "}];"
    )


def _terminal_gate_function() -> str:
    return (
        "const request = msg.modbusSkillsRequest || {};\n"
        "const active = flow.get('modbusSkillsActiveBlockId');\n"
        "const explicitFailure = Boolean(\n"
        "  msg.error || msg.modbusError || msg.modbusSkillsReadError ||\n"
        "  (msg.payload && !Array.isArray(msg.payload) && msg.payload.error)\n"
        ");\n"
        "const decodedPoints = Array.isArray(msg.payload) && msg.payload.length > 0;\n"
        "if (!explicitFailure && !decodedPoints) {\n"
        "  msg.modbusSkillsIgnoredTerminal = {reason: 'duplicate-raw-response'};\n"
        "  return [null, msg];\n"
        "}\n"
        "if (!request.block_id || request.block_id !== active) {\n"
        "  msg.modbusSkillsIgnoredTerminal = {block_id: request.block_id || null, active_block_id: active || null};\n"
        "  return [null, msg];\n"
        "}\n"
        "flow.set('modbusSkillsActiveBlockId', null);\n"
        "return [msg, null];"
    )


def _response_gate_function() -> str:
    """Accept only a complete, successful read response.

    The success output feeds both derivation and the watchdog reset. The error
    output feeds only the error debug node, so an empty or failed response
    cannot look like a completed read.
    """

    return (
        "const request = msg.modbusSkillsRequest || {};\n"
        "const expected = {block_id: request.block_id, expected_quantity: request.quantity};\n"
        "const payloadData = msg.payload && Array.isArray(msg.payload.data)\n"
        "  ? msg.payload.data\n"
        "  : null;\n"
        "const originalValues = Array.isArray(msg.payload) ? msg.payload : payloadData;\n"
        "let values = originalValues;\n"
        "msg.modbusSkillsRawValues = Array.isArray(values) ? values.slice() : [];\n"
        "const explicitFailure = Boolean(\n"
        "  msg.error || msg.modbusError ||\n"
        "  (msg.payload && !Array.isArray(msg.payload) && msg.payload.error)\n"
        ");\n"
        "const bitArea = request.function_code === 1 || request.function_code === 2;\n"
        "const validBit = (value) => value === true || value === false || value === 0 || value === 1;\n"
        "let bitResponseValid = true;\n"
        "if (bitArea) {\n"
        "  const quantity = expected.expected_quantity;\n"
        "  const paddedLength = Math.ceil(quantity / 8) * 8;\n"
        "  bitResponseValid = Number.isInteger(quantity) && quantity > 0 && Array.isArray(values) &&\n"
        "    (values.length === quantity || values.length === paddedLength) && values.every(validBit) &&\n"
        "    values.slice(quantity).every((value) => value === false || value === 0);\n"
        "  const source = msg.responseBuffer !== undefined ? msg.responseBuffer :\n"
        "    (msg.payload && !Array.isArray(msg.payload) && Object.prototype.hasOwnProperty.call(msg.payload, 'buffer') ? msg.payload : undefined);\n"
        "  if (source !== undefined) {\n"
        "    const bytes = source && source.buffer;\n"
        "    const bufferValid = Buffer.isBuffer(bytes) && bytes.length === paddedLength / 8;\n"
        "    if (Buffer.isBuffer(bytes)) msg.modbusSkillsRawBytes = Array.from(bytes);\n"
        "    bitResponseValid = bitResponseValid && bufferValid &&\n"
        "      Array.isArray(source.data) && source.data.length === values.length &&\n"
        "      source.data.every((value, index) => validBit(value) && Boolean(value) === Boolean(values[index]));\n"
        "    if (bitResponseValid) {\n"
        "      for (let index = 0; index < paddedLength; index++) {\n"
        "        const bit = (bytes[index >> 3] >> (index % 8)) & 1;\n"
        "        if (index < quantity ? Boolean(bit) !== Boolean(values[index]) : bit !== 0) bitResponseValid = false;\n"
        "      }\n"
        "    }\n"
        "  }\n"
        "  if (bitResponseValid) values = values.slice(0, quantity);\n"
        "}\n"
        "const complete = Array.isArray(values) && values.length === expected.expected_quantity;\n"
        "const validValues = complete && values.every((value) => bitArea\n"
        "  ? validBit(value)\n"
        "  : (typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 65535));\n"
        "if (explicitFailure || !complete || !validValues || !bitResponseValid) {\n"
        "  msg.modbusSkillsReadError = {\n"
        "    state: 'invalid-read-response',\n"
        "    block_id: expected.block_id,\n"
        "    expected_quantity: expected.expected_quantity,\n"
        "    received_quantity: Array.isArray(values) ? values.length : 0,\n"
        "    reason: explicitFailure ? 'read-failed' : (!complete ? 'read-wrong-length' : 'read-invalid-values')\n"
        "  };\n"
        "  return [null, msg];\n"
        "}\n"
        "if (bitArea) msg.payload = values;\n"
        "return [msg, null];"
    )


def _node_id(seed: str, role: str) -> str:
    return hashlib.sha256(f"{seed}:{role}".encode("utf-8")).hexdigest()[:16]


def _readme(*, mode: str, environment: list[str]) -> str:
    environment_lines = "\n".join(f"- `{name}`" for name in environment)
    run_instructions = (
        "After you review the endpoint, enable the tab to start the shared "
        "`01 Live poll (5s)` trigger. It starts one bounded response-driven plan "
        "every five seconds while the tab stays enabled. The flow sends the next "
        "request only after the current request returns or times out, and ignores "
        "a new tick while a plan is active. The tab is disabled by default and "
        "does not run at deploy. Disable the tab to stop polling."
        if mode == "final"
        else
        "After you enable the tab, click `01 Start bounded plan` once. The flow "
        "sends the next request only after the current request returns or times "
        "out. Probe mode is manual one-shot and does not poll or retry."
    )
    return f"""# Node-RED {mode.title()} Flow

This target contains one read path for each compiled read block. It does not
contain Modbus write nodes or network discovery logic.

The flow tab is disabled by default. Review all settings before you enable it.

The canvas is organized into four labeled sections: the primary read path,
health/failure handling, bounded retry/watchdog recovery, and a read-only live
capture dashboard. The dashboard
is available via `GET /modbus-dashboard` on the Node-RED host and
refreshes every three seconds. It uses Node-RED core HTTP nodes and never
generates Modbus traffic itself.

## Requirements

- Node-RED.
- `node-red-contrib-modbus`.
- The environment variables below.

{environment_lines}

Set each port to a decimal TCP port. {run_instructions} Import `flow.json`,
review it, and deploy it while it is disabled. Enable the tab only when the
endpoint is safe for the selected mode.

Set `MODBUS_CAPTURE_PATH` to the local path for `capture.json`. The flow writes
one complete `capture/v1` document only after the queue drains or the run is
cancelled. The flow uses one shared reader per route, keeps one request in
flight. {"Final mode retries a failed block at most once." if mode == "final" else "Probe mode makes one physical attempt per block, with no retry."}

The derive nodes keep an immutable copy of the raw values. Final mode supports
int16/uint16, int32/uint32, float32, and identity bool/boolean FC01/02 scalars.
Register values are decoded using the confirmed supported layout before numeric
scale and engineering offset are applied once. Missing/null transforms mean
identity; zero scale is valid. Unsupported 64-bit, string, bitfield, width,
layout, and transform semantics remain held, not silently replaced with raw output.
Malformed raw responses and nonfinite/unsafe decoded values are errors, not
successful engineering values. A per-point semantic decode error preserves raw
evidence and does not trigger another physical read. The dashboard renders only
validated final engineering values; it never scales individual raw register words.

Probe mode does not decode a selected engineering value or apply transforms.
It attaches raw values and datatype/layout metadata without choosing a layout. For each
two-register point, probe mode derives `ABCD`, `BADC`, `CDAB`, and `DCBA`
candidates from the same raw words. Each candidate includes unsigned integer,
signed integer, and float interpretations. The flow does not choose a winner.
"""
