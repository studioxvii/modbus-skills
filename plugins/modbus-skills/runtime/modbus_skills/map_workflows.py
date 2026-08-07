"""Reviewed map normalization, linting, and evidence workflows."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .address import format_modicon_reference, resolve_address
from .models import AddressConvention, DataType, RegisterArea
from .parsers import parse_source
from .validation import READ_FUNCTION_BY_AREA, validate_points


class MapWorkflowError(ValueError):
    """Raised when a map workflow input cannot be processed safely."""


_AREA_ALIASES = {
    "coil": "coil",
    "coils": "coil",
    "0x": "coil",
    "fc01": "coil",
    "01": "coil",
    "discrete": "discrete-input",
    "discrete input": "discrete-input",
    "discrete inputs": "discrete-input",
    "discrete-input": "discrete-input",
    "1x": "discrete-input",
    "fc02": "discrete-input",
    "02": "discrete-input",
    "input": "input-register",
    "input register": "input-register",
    "input registers": "input-register",
    "input-register": "input-register",
    "3x": "input-register",
    "fc04": "input-register",
    "04": "input-register",
    "holding": "holding-register",
    "holding register": "holding-register",
    "holding registers": "holding-register",
    "holding-register": "holding-register",
    "4x": "holding-register",
    "fc03": "holding-register",
    "03": "holding-register",
}
_CONVENTION_ALIASES = {
    "protocol offset": "protocol-offset",
    "protocol-offset": "protocol-offset",
    "pdu offset": "protocol-offset",
    "pdu-offset": "protocol-offset",
    "zero based": "protocol-offset",
    "zero-based": "protocol-offset",
    "zero based offset": "protocol-offset",
    "zero-based-offset": "protocol-offset",
    "0 based": "protocol-offset",
    "0-based": "protocol-offset",
    "one based": "one-based-offset",
    "one-based": "one-based-offset",
    "one based offset": "one-based-offset",
    "one-based-offset": "one-based-offset",
    "1 based": "one-based-offset",
    "1-based": "one-based-offset",
    "modicon": "modicon-reference",
    "modicon reference": "modicon-reference",
    "modicon-reference": "modicon-reference",
    "display": "modicon-reference",
    "display address": "modicon-reference",
    "reference": "modicon-reference",
}
_DATATYPE_ALIASES = {
    "bool": "bool",
    "boolean": "bool",
    "bit": "bool",
    "uint16": "uint16",
    "unsigned 16": "uint16",
    "unsigned 16-bit": "uint16",
    "word": "uint16",
    "int16": "int16",
    "signed 16": "int16",
    "signed 16-bit": "int16",
    "uint32": "uint32",
    "unsigned 32": "uint32",
    "unsigned 32-bit": "uint32",
    "dword": "uint32",
    "int32": "int32",
    "signed 32": "int32",
    "signed 32-bit": "int32",
    "float": "float32",
    "float32": "float32",
    "real": "float32",
    "ieee754 float": "float32",
    "uint64": "uint64",
    "unsigned 64": "uint64",
    "unsigned 64-bit": "uint64",
    "int64": "int64",
    "signed 64": "int64",
    "signed 64-bit": "int64",
    "float64": "float64",
    "double": "float64",
    "ieee754 double": "float64",
}
_BYTE_ORDER_ALIASES = {
    "abcd": "ABCD",
    "big endian": "ABCD",
    "big-endian": "ABCD",
    "big_endian": "ABCD",
    "badc": "BADC",
    "byte swap": "BADC",
    "byte-swap": "BADC",
    "big endian byte swap": "BADC",
    "big-endian-byte-swap": "BADC",
    "cdab": "CDAB",
    "word swap": "CDAB",
    "word-swap": "CDAB",
    "little endian byte swap": "CDAB",
    "little-endian-byte-swap": "CDAB",
    "dcba": "DCBA",
    "little endian": "DCBA",
    "little-endian": "DCBA",
    "little_endian": "DCBA",
}
_ACCESS_ALIASES = {
    "r": "read-only",
    "ro": "read-only",
    "read": "read-only",
    "read only": "read-only",
    "read-only": "read-only",
    "rw": "read-write",
    "r/w": "read-write",
    "read write": "read-write",
    "read-write": "read-write",
    "w": "write-only",
    "wo": "write-only",
    "write": "write-only",
    "write only": "write-only",
    "write-only": "write-only",
}
_RESOLVED_DISPOSITIONS = frozenset({"accepted", "corrected", "excluded", "resolved"})

_KNOWN_SOURCE_FIELDS = {
    "logical_point_id",
    "point_id",
    "id",
    "name",
    "description",
    "route_id",
    "unit_id",
    "area",
    "protocol_offset",
    "display_address",
    "address",
    "address_convention",
    "source_address",
    "datatype",
    "word_count",
    "word_span",
    "register_width",
    "byte_order",
    "byte_layout",
    "byte_order_confirmed",
    "byte_layout_confirmed",
    "byte_order_status",
    "byte_layout_status",
    "scale",
    "offset",
    "engineering_offset",
    "engineering_unit",
    "access",
    "function_code",
    "function",
    "fc",
    "minimum",
    "maximum",
    "expected_interval_seconds",
    "stale_after_seconds",
    "rate_of_change_limit",
    "counter",
    "counter_modulus",
    "_source",
    "_extra",
}


def _text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    result = str(value).strip()
    return result or None


def _alias(value: Any, aliases: Mapping[str, str]) -> str | None:
    text = _text(value)
    if text is None:
        return None
    normalized = re.sub(r"\s+", " ", text.lower().replace("_", " ")).strip()
    return aliases.get(normalized) or aliases.get(text.lower())


def _byte_order(value: Any, word_span: int | None) -> str | None:
    """Normalize named 16/32-bit layouts or preserve an explicit permutation."""

    text = _text(value)
    if text is None:
        return None
    compact = re.sub(r"[^A-Za-z]", "", text).upper()
    if word_span is not None:
        expected = "ABCDEFGH"[: word_span * 2]
        if len(compact) == len(expected) and sorted(compact) == sorted(expected):
            return compact
    named = _alias(text, _BYTE_ORDER_ALIASES)
    if named is None:
        return None
    if word_span == 1:
        return "AB" if named == "ABCD" else "BA" if named in {"BADC", "DCBA"} else None
    if word_span == 2 or word_span is None:
        return named
    if word_span == 4 and named == "ABCD":
        return "ABCDEFGH"
    if word_span == 4 and named == "DCBA":
        return "HGFEDCBA"
    return None


def _integer(value: Any, *, minimum: int, maximum: int) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer")
    if isinstance(value, int):
        result = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("value must be a finite whole number")
        result = int(value)
    else:
        text = str(value).strip()
        if not re.fullmatch(r"[+-]?\d+", text):
            raise ValueError("value must be a decimal integer")
        result = int(text)
    if not minimum <= result <= maximum:
        raise ValueError(f"value must be from {minimum} through {maximum}")
    return result


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("value must be finite")
    return result


def _boolean(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError("value must be true or false")


def _byte_order_input(
    record: Mapping[str, Any],
    defaults: Mapping[str, Any],
) -> tuple[Any, str | None, Any, Any]:
    raw, source = _get(record, defaults, "byte_order", "byte_layout")
    nested = raw if isinstance(raw, Mapping) else {}
    layout = nested.get("layout", nested.get("value")) if nested else raw
    status = record.get("byte_order_status")
    if status is None:
        status = record.get("byte_layout_status")
    if status is None:
        status = nested.get("status")
    confirmed = record.get("byte_order_confirmed")
    if confirmed is None:
        confirmed = record.get("byte_layout_confirmed")
    if confirmed is None:
        confirmed = nested.get("confirmed")
    return layout, source, status, confirmed


def _get(record: Mapping[str, Any], defaults: Mapping[str, Any], key: str, *aliases: str) -> tuple[Any, str | None]:
    for candidate in (key, *aliases):
        if record.get(candidate) not in (None, ""):
            return record[candidate], candidate
    if defaults.get(key) not in (None, ""):
        return defaults[key], "workflow_default"
    return None, None


def _hold(
    code: str,
    message: str,
    field: str,
    *,
    point_id: str | None = None,
    source: Mapping[str, Any] | None = None,
    severity: str = "hold",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "message": message,
        "field": field,
        "blocking": True,
    }
    if point_id:
        result["point_ids"] = [point_id]
    if source:
        result["source"] = dict(source)
    return result


def _assumption(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **details}


def _disposition_status(item: Mapping[str, Any]) -> str | None:
    disposition = item.get("disposition")
    status = disposition.get("status") if isinstance(disposition, Mapping) else disposition
    return status.strip().lower() if isinstance(status, str) else None


def _source_hold_items(value: Any) -> tuple[list[Any], list[dict[str, Any]]]:
    """Copy source holds and return the unresolved blocking subset.

    A source hold stays blocking unless it has an explicit resolved disposition
    or explicitly declares ``blocking: false``. Malformed hold entries fail
    closed and remain visible in the audit copy.
    """

    if value in (None, ""):
        return [], []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        copied = [value]
    else:
        copied = [dict(item) if isinstance(item, Mapping) else item for item in value]

    unresolved: list[dict[str, Any]] = []
    for index, item in enumerate(copied):
        if not isinstance(item, Mapping):
            unresolved.append(
                {
                    "code": "source.hold-invalid",
                    "severity": "error",
                    "blocking": True,
                    "message": "A source hold is not an object and requires review.",
                    "details": {"index": index, "value": item},
                }
            )
            continue
        if item.get("blocking") is False or _disposition_status(item) in _RESOLVED_DISPOSITIONS:
            continue
        blocking_hold = dict(item)
        blocking_hold.setdefault("severity", "hold")
        blocking_hold.setdefault("blocking", True)
        unresolved.append(blocking_hold)
    return copied, unresolved


def _stable_point_id(record: Mapping[str, Any], normalized_parts: Mapping[str, Any]) -> str:
    payload = {
        "source": record.get("_source"),
        "name": record.get("name"),
        "raw_address": normalized_parts.get("raw_address"),
        "route_id": normalized_parts.get("route_id"),
        "unit_id": normalized_parts.get("unit_id"),
        "area": normalized_parts.get("area"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "point-" + hashlib.sha256(encoded).hexdigest()[:16]


def _normalize_one(
    record: Mapping[str, Any],
    defaults: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    assumptions: list[dict[str, Any]] = []
    holds: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    source = record.get("_source") if isinstance(record.get("_source"), Mapping) else {}

    route_raw, route_source = _get(record, defaults, "route_id")
    route_id = _text(route_raw)
    if route_id is None:
        holds.append(
            _hold(
                "point.route-id-unresolved",
                "Declare the connection route ID.",
                "route_id",
                source=source,
            )
        )
    elif route_source == "workflow_default":
        assumptions.append(
            _assumption(
                "workflow-default",
                "Applied the explicit workflow default for route_id.",
                field="route_id",
                value=route_id,
            )
        )
    evidence.append({"field": "route_id", "source_field": route_source, "source_value": route_raw, "value": route_id})

    unit_raw, unit_source = _get(record, defaults, "unit_id")
    try:
        unit_id = _integer(unit_raw, minimum=1, maximum=247)
    except (TypeError, ValueError) as exc:
        unit_id = None
        holds.append(_hold("point.unit-id-invalid", str(exc), "unit_id", source=source, severity="error"))
    if unit_id is None and not any(item["field"] == "unit_id" for item in holds):
        holds.append(_hold("point.unit-id-unresolved", "Declare the Modbus unit ID from 1 through 247.", "unit_id", source=source))
    if unit_source == "workflow_default":
        assumptions.append(
            _assumption(
                "workflow-default",
                "Applied the explicit workflow default for unit_id.",
                field="unit_id",
                value=unit_id,
            )
        )
    evidence.append({"field": "unit_id", "source_field": unit_source, "source_value": unit_raw, "value": unit_id})

    area_raw, area_source = _get(record, defaults, "area")
    area_value = _alias(area_raw, _AREA_ALIASES)
    if area_value is None:
        area_value = "unknown"
        code = "point.area-unresolved" if area_raw in (None, "") else "point.area-unrecognized"
        message = "Declare the Modbus area." if area_raw in (None, "") else f"Area value {area_raw!r} is not recognized."
        holds.append(_hold(code, message, "area", source=source))
    if area_source == "workflow_default":
        assumptions.append(
            _assumption(
                "workflow-default",
                "Applied the explicit workflow default for area.",
                field="area",
                value=area_value,
            )
        )
    evidence.append({"field": "area", "source_field": area_source, "source_value": area_raw, "value": area_value})

    top_convention = record.get("address_convention", defaults.get("address_convention"))
    top_convention_source = (
        "address_convention"
        if record.get("address_convention") not in (None, "")
        else "workflow_default"
        if defaults.get("address_convention") not in (None, "")
        else None
    )
    address_inputs: list[dict[str, Any]] = []
    if record.get("protocol_offset") not in (None, ""):
        address_inputs.append(
            {
                "source_field": "protocol_offset",
                "raw": record.get("protocol_offset"),
                "convention": "protocol-offset",
                "convention_source": "protocol_offset field",
            }
        )
    if record.get("display_address") not in (None, ""):
        address_inputs.append(
            {
                "source_field": "display_address",
                "raw": record.get("display_address"),
                "convention": "modicon-reference",
                "convention_source": "display_address field",
            }
        )
    if record.get("source_address") not in (None, ""):
        nested_source = record.get("source_address")
        if isinstance(nested_source, Mapping):
            nested_raw = nested_source.get("raw")
            nested_convention = nested_source.get("convention", top_convention)
            nested_convention_source = (
                "source_address.convention"
                if nested_source.get("convention") not in (None, "")
                else top_convention_source
            )
        else:
            nested_raw = nested_source
            nested_convention = top_convention
            nested_convention_source = top_convention_source
        address_inputs.append(
            {
                "source_field": "source_address",
                "raw": nested_raw,
                "convention": nested_convention,
                "convention_source": nested_convention_source,
            }
        )
    if record.get("address") not in (None, ""):
        generic_address = record.get("address")
        if isinstance(generic_address, Mapping):
            if generic_address.get("protocol_offset") not in (None, ""):
                generic_raw = generic_address.get("protocol_offset")
                generic_convention = "protocol-offset"
                generic_convention_source = "address.protocol_offset field"
            elif generic_address.get("display_address") not in (None, ""):
                generic_raw = generic_address.get("display_address")
                generic_convention = "modicon-reference"
                generic_convention_source = "address.display_address field"
            else:
                generic_raw = generic_address.get("raw")
                generic_convention = generic_address.get("convention", top_convention)
                generic_convention_source = (
                    "address.convention"
                    if generic_address.get("convention") not in (None, "")
                    else top_convention_source
                )
        else:
            generic_raw = generic_address
            generic_convention = top_convention
            generic_convention_source = top_convention_source
        address_inputs.append(
            {
                "source_field": "address",
                "raw": generic_raw,
                "convention": generic_convention,
                "convention_source": generic_convention_source,
            }
        )
    if not address_inputs:
        address_inputs.append(
            {
                "source_field": "address",
                "raw": None,
                "convention": top_convention,
                "convention_source": top_convention_source,
            }
        )
    if any(item["convention_source"] == "workflow_default" for item in address_inputs):
        assumptions.append(
            _assumption(
                "workflow-default",
                "Applied the explicit workflow default for address_convention.",
                field="source_address.convention",
                value=_alias(top_convention, _CONVENTION_ALIASES),
            )
        )

    address_representations: list[dict[str, Any]] = []
    resolutions = []
    for address_input in address_inputs:
        convention_value = _alias(address_input["convention"], _CONVENTION_ALIASES)
        resolution = resolve_address(address_input["raw"], convention_value, area_value)
        resolutions.append(resolution)
        representation = {
            "source_field": address_input["source_field"],
            "raw": address_input["raw"],
            "convention_source": address_input["convention_source"],
            "convention": resolution.source_address.convention.value,
            "area": resolution.area.value,
            "protocol_offset": resolution.protocol_offset,
            "resolved": resolution.resolved,
            "findings": [finding.to_dict() for finding in resolution.findings],
        }
        address_representations.append(representation)
        evidence.append({"field": "address_representation", **representation})
        for finding in resolution.findings:
            finding_value = finding.to_dict()
            finding_value["details"] = {
                **finding_value.get("details", {}),
                "source_field": address_input["source_field"],
            }
            holds.append(
                {**finding_value, "blocking": True, "source": dict(source)}
            )

    primary_index = next(
        (index for index, resolution in enumerate(resolutions) if resolution.resolved),
        0,
    )
    primary_resolution = resolutions[primary_index]
    primary_input = address_inputs[primary_index]
    raw_address = primary_input["raw"]
    protocol_offset = primary_resolution.protocol_offset
    source_address = primary_resolution.source_address.to_dict()
    resolved_pairs = {
        (resolution.area.value, resolution.protocol_offset)
        for resolution in resolutions
        if resolution.resolved
    }
    if len(resolved_pairs) > 1:
        holds.append(
            {
                **_hold(
                    "point.address-representation-conflict",
                    "Source address representations resolve to different protocol addresses.",
                    "protocol_offset",
                    source=source,
                    severity="error",
                ),
                "details": {"representations": address_representations},
            }
        )
    if len(address_inputs) > 1:
        for index, resolution in enumerate(resolutions):
            if not resolution.resolved:
                holds.append(
                    {
                        **_hold(
                            "point.address-secondary-unverifiable",
                            f"Address representation {address_inputs[index]['source_field']!r} cannot be verified.",
                            "protocol_offset",
                            source=source,
                        ),
                        "details": {"representation": address_representations[index]},
                    }
                )
    display_address = None
    if primary_resolution.resolved:
        try:
            display_address = format_modicon_reference(primary_resolution.area, protocol_offset)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            display_address = None

    datatype_raw, datatype_source = _get(record, defaults, "datatype")
    datatype_value = _alias(datatype_raw, _DATATYPE_ALIASES)
    if datatype_value is None:
        code = "point.datatype-unresolved" if datatype_raw in (None, "") else "point.datatype-unrecognized"
        message = "Declare the point data type." if datatype_raw in (None, "") else f"Data type {datatype_raw!r} is not recognized."
        holds.append(_hold(code, message, "datatype", source=source))
    if datatype_source == "workflow_default":
        assumptions.append(
            _assumption(
                "workflow-default",
                "Applied the explicit workflow default for datatype.",
                field="datatype",
                value=datatype_value,
            )
        )
    evidence.append(
        {"field": "datatype", "source_field": datatype_source, "source_value": datatype_raw, "value": datatype_value}
    )

    word_raw, word_source = _get(record, defaults, "word_span", "word_count", "register_width")
    try:
        word_span = _integer(word_raw, minimum=1, maximum=125)
    except (TypeError, ValueError) as exc:
        word_span = None
        holds.append(_hold("point.span-invalid", str(exc), "word_span", source=source, severity="error"))
    datatype_enum = DataType.coerce(datatype_value)
    if word_span is None and word_raw in (None, "") and datatype_enum.span is not None:
        word_span = datatype_enum.span
        assumptions.append(
            _assumption(
                "span-from-datatype",
                f"Used the fixed {datatype_enum.value} span of {word_span} register(s).",
                field="word_span",
                value=word_span,
            )
        )
        word_source = "datatype"
    evidence.append({"field": "word_span", "source_field": word_source, "source_value": word_raw, "value": word_span})

    byte_raw, byte_source, byte_status_raw, byte_confirmed_raw = _byte_order_input(
        record, defaults
    )
    byte_order = _byte_order(byte_raw, word_span)
    normalized_byte_status = (
        str(byte_status_raw).strip().lower().replace("_", "-")
        if isinstance(byte_status_raw, str) and byte_status_raw.strip()
        else None
    )
    status_confirmation = None
    if normalized_byte_status in {"confirmed", "approved", "reviewed"}:
        status_confirmation = True
    elif normalized_byte_status in {
        "pending",
        "candidate",
        "assumed",
        "unconfirmed",
        "unresolved",
    }:
        status_confirmation = False
    elif byte_status_raw not in (None, ""):
        holds.append(
            _hold(
                "point.byte-order-status-unrecognized",
                f"Byte-order status {byte_status_raw!r} is not recognized.",
                "byte_order_status",
                source=source,
            )
        )
    try:
        explicit_confirmation = _boolean(byte_confirmed_raw)
    except (TypeError, ValueError) as exc:
        explicit_confirmation = False
        holds.append(
            _hold(
                "point.byte-order-confirmation-invalid",
                str(exc),
                "byte_order_confirmed",
                source=source,
                severity="error",
            )
        )
    if (
        explicit_confirmation is not None
        and status_confirmation is not None
        and explicit_confirmation != status_confirmation
    ):
        holds.append(
            _hold(
                "point.byte-order-confirmation-conflict",
                "Byte-order confirmation and status disagree.",
                "byte_order_confirmed",
                source=source,
            )
        )
    byte_order_confirmed = (
        explicit_confirmation
        if explicit_confirmation is not None
        else status_confirmation
        if status_confirmation is not None
        else byte_order is not None
    )
    if status_confirmation is False:
        byte_order_confirmed = False
    if byte_raw not in (None, "") and byte_order is None:
        holds.append(
            _hold(
                "point.byte-order-unrecognized",
                f"Byte order {byte_raw!r} is not recognized.",
                "byte_order",
                source=source,
            )
        )
    if word_span is not None and word_span > 1 and byte_order is None:
        if byte_raw in (None, ""):
            holds.append(
                _hold(
                    "point.byte-order-unresolved",
                    "Capture raw words and confirm a byte order for this multi-register point.",
                    "byte_order",
                    source=source,
                )
            )
    if byte_order is not None and byte_order_confirmed is False:
        holds.append(
            _hold(
                "point.byte-order-unconfirmed",
                "The byte layout is evidence only until a human confirms it.",
                "byte_order_confirmed",
                source=source,
            )
        )
    if byte_source == "workflow_default":
        assumptions.append(
            _assumption(
                "workflow-default",
                "Applied the explicit workflow default for byte_order.",
                field="byte_order",
                value=byte_order,
            )
        )
    evidence.append(
        {
            "field": "byte_order",
            "source_field": byte_source,
            "source_value": byte_raw,
            "source_status": byte_status_raw,
            "source_confirmed": byte_confirmed_raw,
            "value": byte_order,
            "confirmed": byte_order_confirmed,
        }
    )

    scale_raw, scale_source = _get(record, defaults, "scale")
    engineering_offset_raw, engineering_offset_source = _get(record, defaults, "engineering_offset", "offset")
    numeric_values: dict[str, float | None] = {}
    for field, raw_value, field_source in (
        ("scale", scale_raw, scale_source),
        ("engineering_offset", engineering_offset_raw, engineering_offset_source),
        ("minimum", record.get("minimum"), "minimum" if record.get("minimum") not in (None, "") else None),
        ("maximum", record.get("maximum"), "maximum" if record.get("maximum") not in (None, "") else None),
        (
            "expected_interval_seconds",
            record.get("expected_interval_seconds"),
            "expected_interval_seconds" if record.get("expected_interval_seconds") not in (None, "") else None,
        ),
        (
            "stale_after_seconds",
            record.get("stale_after_seconds"),
            "stale_after_seconds" if record.get("stale_after_seconds") not in (None, "") else None,
        ),
        (
            "rate_of_change_limit",
            record.get("rate_of_change_limit"),
            "rate_of_change_limit" if record.get("rate_of_change_limit") not in (None, "") else None,
        ),
    ):
        try:
            numeric_values[field] = _number(raw_value)
        except (TypeError, ValueError) as exc:
            numeric_values[field] = None
            holds.append(_hold(f"point.{field.replace('_', '-')}-invalid", str(exc), field, source=source, severity="error"))
        evidence.append({"field": field, "source_field": field_source, "source_value": raw_value, "value": numeric_values[field]})
    if (
        numeric_values["minimum"] is not None
        and numeric_values["maximum"] is not None
        and numeric_values["minimum"] > numeric_values["maximum"]
    ):
        holds.append(
            _hold(
                "point.range-invalid",
                "Minimum is greater than maximum.",
                "minimum",
                source=source,
                severity="error",
            )
        )

    access_raw = record.get("access")
    access = _alias(access_raw, _ACCESS_ALIASES)
    if access_raw not in (None, "") and access is None:
        holds.append(
            _hold(
                "point.access-unrecognized",
                f"Access value {access_raw!r} is not recognized.",
                "access",
                source=source,
            )
        )

    function_raw, function_source = _get(
        record, defaults, "function_code", "function", "fc"
    )
    expected_function_code = READ_FUNCTION_BY_AREA.get(
        RegisterArea.coerce(area_value)
    )
    if function_raw in (None, ""):
        function_code = expected_function_code
        if function_code is not None:
            assumptions.append(
                _assumption(
                    "function-code-from-area",
                    f"Used FC{function_code:02d}, the read function for {area_value}.",
                    field="function_code",
                    value=function_code,
                )
            )
            function_source = "area"
    else:
        try:
            function_code = _integer(function_raw, minimum=1, maximum=255)
        except (TypeError, ValueError) as exc:
            function_code = None
            holds.append(
                _hold(
                    "function-code.invalid",
                    str(exc),
                    "function_code",
                    source=source,
                    severity="error",
                )
            )
        if function_code is not None and function_code not in {1, 2, 3, 4}:
            code = (
                "function-code.write-forbidden"
                if function_code in {5, 6, 15, 16, 22, 23}
                else "function-code.unsupported"
            )
            holds.append(
                _hold(
                    code,
                    f"FC{function_code:02d} is not permitted; workflows are read-only FC01 through FC04.",
                    "function_code",
                    source=source,
                    severity="error",
                )
            )
        elif (
            function_code is not None
            and expected_function_code is not None
            and function_code != expected_function_code
        ):
            holds.append(
                _hold(
                    "function-code.area-mismatch",
                    f"{area_value} requires FC{expected_function_code:02d}, not FC{function_code:02d}.",
                    "function_code",
                    source=source,
                    severity="error",
                )
            )
    if function_source == "workflow_default":
        assumptions.append(
            _assumption(
                "workflow-default",
                "Applied the explicit workflow default for function_code.",
                field="function_code",
                value=function_code,
            )
        )
    evidence.append(
        {
            "field": "function_code",
            "source_field": function_source,
            "source_value": function_raw,
            "value": function_code,
        }
    )

    explicit_id = _text(record.get("logical_point_id", record.get("point_id", record.get("id"))))
    id_parts = {
        "raw_address": raw_address,
        "route_id": route_id,
        "unit_id": unit_id,
        "area": area_value,
    }
    logical_point_id = explicit_id or _stable_point_id(record, id_parts)
    if explicit_id is None:
        assumptions.append(
            _assumption(
                "generated-logical-point-id",
                "Generated a stable logical point ID from source evidence and normalized identity fields.",
                field="logical_point_id",
                value=logical_point_id,
            )
        )
    for hold in holds:
        if not hold.get("point_ids"):
            hold["point_ids"] = [logical_point_id]

    unmapped_fields = {
        key: value
        for key, value in record.items()
        if key not in _KNOWN_SOURCE_FIELDS and not key.startswith("_")
    }
    point = {
        "schema_version": "modbus-map/v1",
        "logical_point_id": logical_point_id,
        "point_id": logical_point_id,
        "name": _text(record.get("name")),
        "description": _text(record.get("description")),
        "route_id": route_id,
        "unit_id": unit_id,
        "area": area_value if area_value != "unknown" else None,
        "protocol_offset": protocol_offset,
        "display_address": display_address,
        "source_address": source_address,
        "address_representations": address_representations,
        "datatype": datatype_value,
        "word_span": word_span,
        "word_count": word_span,
        "byte_order": byte_order,
        "byte_order_confirmed": byte_order_confirmed,
        "byte_order_status": "confirmed" if byte_order_confirmed is True else "pending",
        "scale": numeric_values["scale"],
        "engineering_offset": numeric_values["engineering_offset"],
        "offset": numeric_values["engineering_offset"],
        "engineering_unit": _text(record.get("engineering_unit")),
        "access": access,
        "minimum": numeric_values["minimum"],
        "maximum": numeric_values["maximum"],
        "expected_interval_seconds": numeric_values["expected_interval_seconds"],
        "stale_after_seconds": numeric_values["stale_after_seconds"],
        "rate_of_change_limit": numeric_values["rate_of_change_limit"],
        "counter": record.get("counter") is True,
        "counter_modulus": record.get("counter_modulus"),
        "function_code": function_code,
        "normalization_status": "pending" if holds else "confirmed",
        "source_evidence": evidence,
        "source_location": dict(source),
        "unmapped_fields": unmapped_fields,
    }
    return point, assumptions, holds


def normalize_map(
    source: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize explicit source fields into ``modbus-map/v1`` candidates.

    ``defaults`` are caller-supplied workflow values. Each applied default is
    recorded as an assumption. The function never selects a route, area, data
    type, address convention, unit ID, or byte order without source or workflow
    input.
    """

    defaults = dict(defaults or {})
    if isinstance(source, Mapping):
        raw_records = source.get("records", source.get("points", source.get("registers", ())))
        warnings = list(source.get("warnings", ()))
        rejected = list(source.get("rejected_rows", ()))
        assumptions = list(source.get("assumptions", ()))
        source_format = source.get("format")
        source_holds, unresolved_source_holds = _source_hold_items(
            source.get("source_holds", source.get("holds", ()))
        )
        source_findings = list(source.get("source_findings", source.get("findings", ())))
    else:
        raw_records = source
        warnings = []
        rejected = []
        assumptions = []
        source_format = None
        source_holds = []
        unresolved_source_holds = []
        source_findings = []
    if not isinstance(raw_records, Sequence) or isinstance(raw_records, (str, bytes, bytearray)):
        raise MapWorkflowError("Source must contain a records, points, or registers array.")
    if any(not isinstance(record, Mapping) for record in raw_records):
        raise MapWorkflowError("Every source record must be an object.")

    points: list[dict[str, Any]] = []
    holds: list[dict[str, Any]] = list(unresolved_source_holds)
    for record in raw_records:
        point, point_assumptions, point_holds = _normalize_one(record, defaults)
        points.append(point)
        assumptions.extend(point_assumptions)
        holds.extend(point_holds)
    return {
        "schema_version": "modbus-map/v1",
        "source_format": source_format,
        "points": points,
        "holds": holds,
        "source_holds": source_holds,
        "source_findings": source_findings,
        "warnings": warnings,
        "rejected_rows": rejected,
        "assumptions": assumptions,
        "summary": {
            "source_records": len(raw_records),
            "normalized_points": len(points),
            "confirmed_points": sum(point["normalization_status"] == "confirmed" for point in points),
            "pending_points": sum(point["normalization_status"] != "confirmed" for point in points),
            "blocking_holds": len(holds),
            "source_holds": len(source_holds),
            "unresolved_source_holds": len(unresolved_source_holds),
            "rejected_rows": len(rejected),
        },
    }


def lint_map(canonical_map: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Run the shared core validator and return JSON-safe findings."""

    if isinstance(canonical_map, Mapping):
        points = canonical_map.get("points", canonical_map.get("records", ()))
        _, workflow_holds = _source_hold_items(canonical_map.get("holds", ()))
    else:
        points = canonical_map
        workflow_holds = []
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes, bytearray)):
        raise MapWorkflowError("Canonical map must contain a points array.")
    try:
        findings = [finding.to_dict() for finding in validate_points(points)]
    except (TypeError, ValueError) as exc:
        raise MapWorkflowError(f"Canonical map cannot be validated: {exc}") from exc

    for point in points:
        if not isinstance(point, Mapping):
            continue
        access = point.get("access")
        if access in {"read-write", "write-only"}:
            findings.append(
                {
                    "code": "point.write-access-declared",
                    "severity": "warning",
                    "message": "The source declares write access. Public workflows remain read-only.",
                    "point_ids": [_text(point.get("logical_point_id")) or "<unresolved>"],
                    "field": "access",
                    "details": {"access": access},
                }
            )
    combined = workflow_holds + findings
    severity_counts = Counter(str(finding.get("severity", "unknown")) for finding in combined if isinstance(finding, Mapping))
    return {
        "contract": "modbus-map-lint/v1",
        "findings": combined,
        "summary": {
            "points": len(points),
            "holds": severity_counts["hold"],
            "errors": severity_counts["error"],
            "warnings": severity_counts["warning"],
            "info": severity_counts["info"],
            "blocking": severity_counts["hold"] + severity_counts["error"],
        },
    }


def review_parse_evidence(
    canonical_map: Mapping[str, Any],
    *,
    lint_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact human-review queue from map evidence and findings."""

    candidate_input = "points" not in canonical_map and "records" in canonical_map
    points = canonical_map.get("records", ()) if candidate_input else canonical_map.get("points", ())
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes, bytearray)):
        raise MapWorkflowError("Map evidence must contain a points or records array.")
    if any(not isinstance(point, Mapping) for point in points):
        raise MapWorkflowError("Every map evidence record must be an object.")

    source_holds, unresolved_source_holds = _source_hold_items(
        canonical_map.get("source_holds", canonical_map.get("holds", ()))
    )
    if lint_result is not None:
        lint = lint_result
    elif candidate_input:
        source_findings = canonical_map.get("findings", ())
        if not isinstance(source_findings, Sequence) or isinstance(
            source_findings, (str, bytes, bytearray)
        ):
            source_findings = ()
        candidate_findings = [
            dict(finding) for finding in source_findings if isinstance(finding, Mapping)
        ]
        candidate_findings.extend(unresolved_source_holds)
        blocking_count = sum(
            finding.get("blocking") is not False
            and finding.get("severity", "hold") in {"hold", "error"}
            for finding in candidate_findings
        )
        lint = {
            "findings": candidate_findings,
            "summary": {"blocking": blocking_count},
        }
    else:
        lint = lint_map(canonical_map)
    lint_findings = lint.get("findings", ())
    if isinstance(lint_findings, Sequence) and not isinstance(
        lint_findings, (str, bytes, bytearray)
    ):
        findings = [dict(finding) for finding in lint_findings if isinstance(finding, Mapping)]
    else:
        findings = []
    for source_hold in unresolved_source_holds:
        if source_hold not in findings:
            findings.append(source_hold)
    finding_by_point: dict[str, list[dict[str, Any]]] = {}
    global_findings: list[dict[str, Any]] = []
    for finding in findings if isinstance(findings, Sequence) else ():
        if not isinstance(finding, Mapping):
            continue
        point_ids = finding.get("point_ids", ())
        if isinstance(point_ids, Sequence) and not isinstance(point_ids, (str, bytes, bytearray)) and point_ids:
            for point_id in point_ids:
                finding_by_point.setdefault(str(point_id), []).append(dict(finding))
        else:
            global_findings.append(dict(finding))

    rejected_rows = list(canonical_map.get("rejected_rows", ()))
    unresolved_rejected_rows = []
    for rejected in rejected_rows:
        if not isinstance(rejected, Mapping):
            unresolved_rejected_rows.append(rejected)
            continue
        disposition = rejected.get("disposition")
        status = (
            disposition.get("status")
            if isinstance(disposition, Mapping)
            else disposition
        )
        if not isinstance(status, str) or status.strip().lower() not in _RESOLVED_DISPOSITIONS:
            unresolved_rejected_rows.append(rejected)
    if unresolved_rejected_rows:
        global_findings.append(
            {
                "code": "source.rejected-rows-unresolved",
                "severity": "hold",
                "message": "Rejected source rows require an explicit accepted, corrected, excluded, or resolved disposition.",
                "details": {"count": len(unresolved_rejected_rows)},
            }
        )

    review_items = []
    global_blocking = any(
        finding.get("blocking") is not False
        and finding.get("severity", "hold") in {"hold", "error"}
        for finding in global_findings
    )
    for index, point in enumerate(points):
        identifier = _text(
            point.get("logical_point_id", point.get("point_id", point.get("id")))
        ) or (f"candidate-{index + 1}" if candidate_input else "<unresolved>")
        point_findings = finding_by_point.get(identifier, [])
        if candidate_input:
            source_evidence = [
                {"field": key, "source_field": key, "source_value": value}
                for key, value in point.items()
                if key != "_source"
            ]
            review_items.append(
                {
                    "logical_point_id": identifier,
                    "name": point.get("name"),
                    "status": "blocked" if global_blocking else "review",
                    "input_stage": "extraction-candidate",
                    "normalization_performed": False,
                    "unresolved_fields": [],
                    "source_location": dict(point.get("_source", {}))
                    if isinstance(point.get("_source"), Mapping)
                    else {},
                    "source_evidence": source_evidence,
                    "candidate_record": dict(point),
                    "findings": point_findings,
                }
            )
            continue
        unresolved_fields = []
        for field in ("unit_id", "area", "protocol_offset", "datatype"):
            if point.get(field) in (None, "", "unknown"):
                unresolved_fields.append(field)
        if (point.get("word_span") or point.get("word_count") or 0) > 1 and not point.get("byte_order"):
            unresolved_fields.append("byte_order")
        status = "blocked" if any(
            finding.get("severity") in {"hold", "error"} for finding in point_findings
        ) else "review"
        review_items.append(
            {
                "logical_point_id": identifier,
                "name": point.get("name"),
                "status": status,
                "unresolved_fields": unresolved_fields,
                "source_location": point.get("source_location", {}),
                "source_evidence": point.get("source_evidence", []),
                "findings": point_findings,
            }
        )
    blocking = sum(item["status"] == "blocked" for item in review_items)
    return {
        "contract": "modbus-map-evidence-review/v1",
        "input_stage": "extraction-candidate" if candidate_input else "canonical-map",
        "normalization_performed": False,
        "review_status": "blocked"
        if blocking
        or lint.get("summary", {}).get("blocking", 0)
        or global_blocking
        or unresolved_rejected_rows
        else "ready-for-human-review",
        "summary": {
            "points": len(review_items),
            "blocked_points": blocking,
            "reviewable_points": len(review_items) - blocking,
            "rejected_rows": len(rejected_rows),
            "unresolved_rejected_rows": len(unresolved_rejected_rows),
            "assumptions": len(canonical_map.get("assumptions", ())),
        },
        "items": review_items,
        "global_findings": global_findings,
        "source_holds": source_holds,
        "assumptions": list(canonical_map.get("assumptions", ())),
        "rejected_rows": rejected_rows,
    }


def diagnose_map(
    source: Any,
    *,
    source_format: str | None = None,
    filename: str | None = None,
    delimiter: str | None = None,
    defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Parse, normalize, lint, and prepare evidence in one deterministic chain."""

    parsed = parse_source(
        source,
        source_format=source_format,
        filename=filename,
        delimiter=delimiter,
    )
    canonical = normalize_map(parsed, defaults=defaults)
    lint = lint_map(canonical)
    review = review_parse_evidence(canonical, lint_result=lint)
    return {"parsed": parsed, "canonical_map": canonical, "lint": lint, "review": review}


__all__ = [
    "MapWorkflowError",
    "diagnose_map",
    "lint_map",
    "normalize_map",
    "review_parse_evidence",
]
