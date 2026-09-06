"""Deterministic, dependency-free parsers for Modbus register maps.

The parsers keep source values intact. They do not guess an address area, data
type, or byte order. Normalization is a separate reviewed workflow.
"""

from __future__ import annotations

import csv
import io
import json
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from xml.etree import ElementTree as ET


class ParseError(ValueError):
    """Raised when a source cannot be parsed safely."""


_HEADER_ALIASES = {
    "address": "address",
    "register": "address",
    "register_s": "protocol_offset",
    "register_address": "address",
    "modbus_address": "address",
    "modbus_address_read": "address",
    "modbus_register": "address",
    "holding_register": "address",
    "holding_registers": "address",
    "holding_register_1_indexed": "display_address",
    "mb_address": "address",
    "acquisuite_mb_address": "address",
    "reference": "address",
    "ref": "address",
    "protocol_offset": "protocol_offset",
    "pdu_offset": "protocol_offset",
    "zero_based_address": "protocol_offset",
    "zero_based_offset": "protocol_offset",
    "address_0_indexed": "protocol_offset",
    "register_number": "protocol_offset",
    "register_s_decimal_0_based": "protocol_offset",
    "register_s_decimal_1_based": "display_address",
    "offset": "source_offset",
    "display_address": "display_address",
    "reference_number": "display_address",
    "modicon_reference": "display_address",
    "address_convention": "address_convention",
    "address_basis": "address_convention",
    "address_format": "address_convention",
    "area": "area",
    "register_area": "area",
    "register_type": "area",
    "reg_type": "area",
    "table": "area",
    "modbus_table": "area",
    "object_type": "area",
    "name": "name",
    "tag": "name",
    "tag_name": "name",
    "point": "name",
    "point_name": "name",
    "parameter_name": "name",
    "description": "description",
    "semantics_description": "description",
    "desc": "description",
    "label": "description",
    "datatype": "datatype",
    "data_type": "datatype",
    "modbus_data_type": "datatype",
    "value_type": "datatype",
    "format": "datatype",
    "byte_order": "byte_order",
    "byteorder": "byte_order",
    "word_order": "byte_order",
    "endianness": "byte_order",
    "endian": "byte_order",
    "bit_order": "bit_order",
    "bitorder": "bit_order",
    "bit_numbering": "bit_order",
    "coil_bit_order": "bit_order",
    "packed_bit_order": "bit_order",
    "word_count": "word_count",
    "register_count": "word_count",
    "size_int16": "word_count",
    "registers": "word_count",
    "length_words": "word_count",
    "unit_id": "unit_id",
    "unit": "engineering_unit",
    "slave_id": "unit_id",
    "slave": "unit_id",
    "device_id": "unit_id",
    "route_id": "route_id",
    "route": "route_id",
    "connection": "route_id",
    "point_id": "logical_point_id",
    "logical_point_id": "logical_point_id",
    "tag_id": "logical_point_id",
    "scale": "scale",
    "multiplier": "scale",
    "gain": "scale",
    "slope": "scale",
    "offset_value": "offset",
    "bias": "offset",
    "engineering_unit": "engineering_unit",
    "engineering_units": "engineering_unit",
    "units": "engineering_unit",
    "eu": "engineering_unit",
    "access": "access",
    "r_w": "access",
    "read_write": "access",
    "function_code": "function_code",
    "modbus_function_code": "function_code",
    "function": "function_code",
    "fc": "function_code",
    "minimum": "minimum",
    "min": "minimum",
    "maximum": "maximum",
    "max": "maximum",
    "expected_interval_seconds": "expected_interval_seconds",
    "poll_interval_seconds": "expected_interval_seconds",
}

_ADDRESS_KEYS = {"address", "protocol_offset", "display_address", "source_address"}
_REGISTER_HEADER_KEYS = frozenset(
    {
        "address",
        "protocol_offset",
        "display_address",
        "modbus_address",
        "modbus_register",
        "register_address",
        "mb_address",
        "reference",
        "ref",
        "pdu_offset",
        "zero_based_address",
        "zero_based_offset",
        "register",
        "offset",
        "register_number",
    }
)
_KNOWN_AREAS = {
    "discrete-input",
    "input-register",
    "holding-register",
    "coil",
    "coils",
    "discrete input",
    "discrete inputs",
    "input register",
    "input registers",
    "holding register",
    "holding registers",
    "fc01",
    "fc02",
    "fc03",
    "fc04",
}
_KNOWN_DATATYPES = {
    "bool",
    "boolean",
    "bit",
    "uint16",
    "int16",
    "uint32",
    "int32",
    "uint64",
    "int64",
    "float",
    "float32",
    "real",
    "double",
    "float64",
    "string",
    "ascii",
}
_KNOWN_BYTE_ORDERS = {
    "abcd",
    "badc",
    "cdab",
    "dcba",
    "big endian",
    "big-endian",
    "little endian",
    "little-endian",
    "word swap",
    "byte swap",
    "big endian byte swap",
    "little endian byte swap",
}

_XML_UNSAFE = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_MAX_XLSX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
_SCALE_NOTE_LIMIT = 256
_SCALE_NOTE_CHARS = 4096
_SCALE_ASSOCIATION_LIMIT = 100000
_SCALE_RAW_NOTE_BYTES = 16 * 1024
_SCALE_EVIDENCE_BYTES = 8 * 1024 * 1024
_SCALE_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_SCALE_EQUATION = re.compile(
    rf"(?P<selector>.+?)\s+engineering value\s*=\s*raw\s*(?P<operator>[*/×÷])\s*"
    rf"(?P<operand>{_SCALE_NUMBER})(?:\.(?=\s|$)|(?=\s|$))", re.IGNORECASE,
)
_SCALE_CUE = re.compile(
    r"^(.+?\s+engineering value\s*=\s*raw\b|(?:integer\s+)?scaling\s+for\b|"
    r".+?\s+conversion direction is (?:unknown|unspecified)\b)", re.IGNORECASE,
)


def _xlsx_scale_note(value: Any, location: Mapping[str, Any], *, row_local: bool = False) -> dict[str, Any] | None:
    """Recognize bounded scalar statements, never arbitrary divide prose."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not _SCALE_CUE.match(text):
        return None
    if len(value) > _SCALE_RAW_NOTE_BYTES or len(value.encode("utf-8")) > _SCALE_RAW_NOTE_BYTES:
        raise ParseError("XLSX scalar conversion literal exceeds the 16 KiB raw UTF-8 evidence limit.")
    if len(text) > _SCALE_NOTE_CHARS:
        raise ParseError("XLSX scalar conversion statement exceeds the 4096 character evidence limit.")
    clauses: list[dict[str, Any]] = []
    position = 0
    while position < len(text):
        match = _SCALE_EQUATION.match(text, position)
        if not match:
            break
        clauses.append(match.groupdict())
        position = match.end()
        while position < len(text) and text[position].isspace():
            position += 1
    if position != len(text):
        clauses = []
        compact = re.fullmatch(
            rf"(?:Integer\s+)?scaling for (.+?)\s*\(\s*([*/×÷])\s*({_SCALE_NUMBER})\s*\)\.?",
            text, re.IGNORECASE,
        )
        unknown = re.fullmatch(
            rf"(.+?) conversion direction is (?:unknown|unspecified): factor {_SCALE_NUMBER} "
            r"may be a multiplier or divisor\.?", text, re.IGNORECASE,
        )
        if compact:
            clauses = [{"selector": compact[1], "operator": compact[2], "operand": compact[3]}]
        elif unknown:
            clauses = [{"selector": unknown[1], "direction": "unknown"}]
        else:
            # A single explicit selector remains source evidence even when the
            # operation has unsupported terms. Never let a Multiplier heading
            # exempt that named point merely because the formula is unresolved.
            named = re.match(r"(.+?)\s+(?:engineering value\s*=\s*raw\b|conversion direction is (?:unknown|unspecified)\b)", text, re.IGNORECASE)
            if named and len(re.findall(r"engineering value\s*=\s*raw\b|conversion direction is", text, re.IGNORECASE)) == 1:
                clauses = [{"selector": named[1], "direction": "unknown"}]
            else:
                named_compact = re.fullmatch(r"(?:Integer\s+)?scaling for ([^()]+?)\s*\([^()]*\)\.?", text, re.IGNORECASE)
                if named_compact:
                    # Preserve the complete exact subject only. Configured,
                    # malformed or multiple operands do not become arithmetic.
                    clauses = [{"selector": named_compact[1], "direction": "unknown"}]
    for clause in clauses:
        if "operator" in clause:
            clause["operator"] = "divide" if clause["operator"] in {"/", "÷"} else "multiply"
    return {"literal": value, "source_locator": dict(location), "row_local": row_local,
            "clauses": clauses, "scope": "stated" if clauses else "unresolved",
            "unparsed_named_equations": not clauses and "engineering value" in text.casefold()}


def _xlsx_scale_claims(
    records: list[dict[str, Any]], notes: list[dict[str, Any]],
    tables: Mapping[str, tuple[list[Any], list[str], int]],
) -> None:
    """Bind exact names/physical rows once; unresolved scope stays conservative."""
    if not notes:
        return
    # Charge complete escaped notes once, not on every point attachment. Four
    # provisioned copies, 32 bytes per formatted line of nesting allowance, and
    # 1024 bytes for bounded binding/scope metadata deliberately overestimate
    # the note-bearing association graph. This is not a whole-artifact cap:
    # unrelated point fields and other artifact content are outside this budget.
    note_costs: dict[int, int] = {}
    for note in notes:
        serialized = json.dumps(note, ensure_ascii=True, sort_keys=True, indent=2)
        note_costs[id(note)] = len(serialized.encode("utf-8")) + 32 * (serialized.count("\n") + 1) + 1024
    evidence_bytes = sum(note_costs.values())
    if evidence_bytes > _SCALE_EVIDENCE_BYTES:
        raise ParseError("XLSX conversion-note evidence exceeds the 8 MiB derived evidence budget before point association.")
    by_name: dict[str, list[int]] = {}
    by_row: dict[tuple[str, int], int] = {}
    generic: list[int] = []
    generic_sheets: set[str] = set()
    columns: dict[str, tuple[int, Any, int]] = {}
    for sheet, (raw_headers, headers, header_row) in tables.items():
        if "scale" in headers:
            index = headers.index("scale")
            columns[sheet] = (index, raw_headers[index], header_row)
            if _header_key(raw_headers[index], index, resolve_aliases=False) == "scale":
                generic_sheets.add(sheet)
    for index, record in enumerate(records):
        location = record["_source"]
        by_row[(location["sheet"], location["row"])] = index
        name = record.get("name")
        if isinstance(name, str) and name.strip():
            by_name.setdefault(" ".join(name.split()).casefold(), []).append(index)
        column = columns.get(location["sheet"])
        if column and location["sheet"] in generic_sheets and record.get("scale") not in (None, ""):
            generic.append(index)
    attached: dict[int, list[dict[str, Any]]] = {}
    association_count = 0

    def attach(index: int, evidence: dict[str, Any], original_note: dict[str, Any]) -> None:
        nonlocal association_count, evidence_bytes
        association_count += 1
        if association_count > _SCALE_ASSOCIATION_LIMIT:
            raise ParseError(f"XLSX exceeds the {_SCALE_ASSOCIATION_LIMIT} point/conversion-note evidence association limit.")
        evidence_bytes += 4 * note_costs[id(original_note)]
        if evidence_bytes > _SCALE_EVIDENCE_BYTES:
            raise ParseError(
                "XLSX conversion-note associations exceed the 8 MiB derived evidence budget; "
                f"no candidate is returned ({association_count} associations considered)."
            )
        attached.setdefault(index, []).append(evidence)
    for note in notes:
        local = note["source_locator"]
        if note["row_local"]:
            targets = [by_row[(local["sheet"], local["row"])]] if (local["sheet"], local["row"]) in by_row else []
            # A row-local cell cannot establish the scope of other named points.
            for index in targets:
                name = " ".join(str(records[index].get("name", "")).split()).casefold()
                consistent_selector = all(" ".join(clause["selector"].split()).casefold() == name for clause in note["clauses"])
                attach(index, {**note, "binding": "physical-row",
                               "scope": note["scope"] if consistent_selector else "unresolved"}, note)
            continue
        resolved: list[tuple[int, dict[str, Any]]] = []
        for clause in note["clauses"]:
            targets = by_name.get(" ".join(clause["selector"].split()).casefold(), [])
            if len(targets) != 1:
                resolved = []
                break
            resolved.append((targets[0], clause))
        if resolved:
            for index, clause in resolved:
                attach(index, {**note, "clauses": [clause], "binding": "exact-unique-name"}, note)
        else:
            possible = set(generic)
            if note.get("unparsed_named_equations"):
                possible.update(index for index, record in enumerate(records)
                                if record.get("scale") not in (None, ""))
            for clause in note["clauses"]:
                possible.update(by_name.get(" ".join(clause["selector"].split()).casefold(), []))
            for index in sorted(possible):
                attach(index, {**note, "scope": "unresolved", "binding": "possible-workbook-scale-scope"}, note)
    for index, evidence in attached.items():
        record = records[index]
        location = record["_source"]
        column = columns.get(location["sheet"])
        if not column:
            record.setdefault("_claims", []).append({
                "parser_id": "structured.xlsx-scale/v1", "field": "scale",
                "scale_source": "absent-column", "source_locator": dict(location),
                "conversion_notes": evidence,
            })
            continue
        column_index, raw_header, header_row = column
        record.setdefault("_claims", []).append({
            "parser_id": "structured.xlsx-scale/v1", "field": "scale", "value": record.get("scale"),
            "raw_header": str(raw_header), "raw_value": record.get("scale"),
            "source_locator": {**location, "column": column_index + 1},
            "header_locator": {**location, "row": header_row, "column": column_index + 1},
            "conversion_notes": evidence,
        })


def _result(
    records: list[dict[str, Any]],
    *,
    source_format: str,
    warnings: list[dict[str, Any]] | None = None,
    rejected_rows: list[dict[str, Any]] | None = None,
    assumptions: list[dict[str, Any]] | None = None,
    source_holds: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "format": source_format,
        "records": records,
        "warnings": warnings or [],
        "rejected_rows": rejected_rows or [],
        "assumptions": assumptions or [],
        **({"source_holds": source_holds} if source_holds else {}),
    }


def _header_key(value: Any, column_index: int, *, resolve_aliases: bool = True) -> str:
    text = "" if value is None else str(value).strip()
    lower = text.casefold()
    zero_based = bool(
        re.search(r"\(0\s*indexed\)|0[-\s]?based|zero[-\s]?based", lower)
    )
    one_based = bool(
        re.search(r"\(1\s*indexed\)|1[-\s]?based|one[-\s]?based", lower)
    )
    # Drop parenthetical notes: "Holding Register # (1 indexed)" → holding_register
    text = re.sub(r"\([^)]*\)", " ", text)
    # Drop common trailing basis tokens after the primary label.
    text = re.sub(
        r"\b(?:0|1|zero|one)[-\s]?based\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bdecimal\b", " ", text, flags=re.IGNORECASE)
    snake_text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    normalized = re.sub(r"[^a-z0-9]+", "_", snake_text.lower()).strip("_")
    if not normalized:
        return f"column_{column_index + 1}"
    if not resolve_aliases:
        return normalized
    if normalized in _HEADER_ALIASES:
        aliased = _HEADER_ALIASES[normalized]
    else:
        # Retry without trailing indexed/basis crumbs: holding_register_1_indexed
        stripped = re.sub(
            r"_(?:1|0|one|zero)_indexed$",
            "",
            normalized,
        )
        stripped = re.sub(r"_indexed$", "", stripped)
        aliased = _HEADER_ALIASES.get(stripped, normalized)
    # Preserve 0-based vs 1-based address semantics when both columns alias
    # to the same generic address field.
    if aliased in {"address", "display_address", "protocol_offset", "register", "source_offset"}:
        if zero_based and not one_based:
            return "protocol_offset"
        if one_based and not zero_based and aliased == "address":
            return "display_address"
    return aliased


def _unique_headers(values: Sequence[Any]) -> tuple[list[str], list[dict[str, Any]]]:
    headers: list[str] = []
    warnings: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for index, value in enumerate(values):
        key = _header_key(value, index)
        if key == "source_offset":
            warnings.append({"code": "ambiguous_offset_header", "column": index + 1,
                             "message": "Offset is ambiguous between register address and engineering bias; the raw value is preserved as source_offset."})
        counts[key] = counts.get(key, 0) + 1
        if counts[key] > 1:
            replacement = f"{key}_{counts[key]}"
            warnings.append(
                {
                    "code": "duplicate_header",
                    "message": f"Header {key!r} occurs more than once; later value is {replacement!r}.",
                    "column": index + 1,
                }
            )
            key = replacement
        if str(value).strip() == "":
            warnings.append(
                {
                    "code": "blank_header",
                    "message": f"Blank header at column {index + 1} is named {key!r}.",
                    "column": index + 1,
                }
            )
        headers.append(key)
    return headers, warnings


def _trim_text(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def _compound_header_field(raw_header: Any, index: int) -> str | None:
    field = _header_key(raw_header, index)
    spelling = re.sub(r"[^a-z0-9]", "", str(raw_header).casefold())
    if ((field == "area" and spelling.startswith("modbustable"))
            or (field == "description" and spelling.startswith("semanticsdescription"))):
        return field
    return None


def _compound_header_columns(
    raw_headers: Sequence[Any], headers: Sequence[str],
) -> tuple[list[tuple[int, Any, str]], list[tuple[int, Any, str]]]:
    """Resolve the table's compound evidence columns once, not for every row."""
    compounds = []
    has_compound_area = False
    for index, (raw_header, field) in enumerate(zip(raw_headers, headers)):
        compound = _compound_header_field(raw_header, index)
        if compound is not None:
            compounds.append((index, raw_header, field))
            has_compound_area |= compound == "area"
    areas = [(i, raw, field) for i, (raw, field) in enumerate(zip(raw_headers, headers))
             if re.fullmatch(r"area(?:_[0-9]+)?", field)] if has_compound_area else []
    return compounds, areas if len(areas) > 1 else []


def _compound_header_claims(
    columns: Sequence[tuple[int, Any, str]], values: Sequence[Any],
    location: Mapping[str, Any], *, header_row: int,
) -> list[dict[str, Any]]:
    """Keep literal evidence for the two compound table/description headings.

    These claims describe source cells, not confirmed engineering values. The
    unique normalized field (including duplicate suffixes) prevents a later
    compound heading from claiming the first column's value.
    """
    claims = []
    for index, raw_header, field in columns:
        value = values[index]
        claims.append({
            "parser_id": "structured.compound-header/v1",
            "field": field, "value": _trim_text(value),
            "raw_header": str(raw_header), "raw_value": value,
            "source_locator": {**location, "column": index + 1},
            "header_locator": {**location, "row": header_row, "column": index + 1},
        })
    return claims


def _compound_area_conflicts(
    columns: Sequence[tuple[int, Any, str]], values: Sequence[Any],
    location: Mapping[str, Any], *, header_row: int,
) -> list[dict[str, Any]]:
    """Block conflicting area columns introduced by explicit Modbus Table.

    Equivalence is used only to compare source claims; it never replaces their
    values or establishes an area/address convention. These are source-level
    holds with physical row evidence, not invented normalized point identities.
    """
    groups = (
        {"coil", "coils", "0x", "fc01", "01", "0x coil"},
        {"discrete", "discrete input", "discrete inputs", "1x", "fc02", "02", "1x discrete"},
        {"input", "input register", "input registers", "3x", "fc04", "04", "3x input"},
        {"holding", "holding register", "holding registers", "4x", "fc03", "03", "4x holding"},
    )
    claims = []
    identities = set()
    for index, raw_header, field in columns:
        value = values[index]
        if value in (None, ""):
            continue
        spelling = re.sub(r"[\s_-]+", " ", str(value).strip().casefold())
        if not spelling:
            continue
        identity = next((f"known:{i}" for i, aliases in enumerate(groups) if spelling in aliases), f"unknown:{spelling}")
        identities.add(identity)
        claims.append({"field": field, "raw_header": str(raw_header), "raw_value": value,
                       "source_locator": {**location, "column": index + 1},
                       "header_locator": {**location, "row": header_row, "column": index + 1}})
    if len(identities) < 2:
        return []
    return [{"code": "source.area-columns-conflict", "severity": "hold", "blocking": True,
             "field": "area", "source": dict(location),
             "message": "Conflicting nonblank area columns require source correction; no column takes precedence.",
             "details": {"columns": claims}}]


def _holding_header_columns(raw_headers: Sequence[Any], headers: Sequence[str]) -> list[tuple[int, Any]]:
    """Only an exact selected address heading conveys this explicit area."""
    return [(i, raw) for i, (raw, field) in enumerate(zip(raw_headers, headers))
            if field in {"address", "display_address", "protocol_offset"}
            and re.sub(r"\s+", " ", str(raw).strip().casefold()) in {"holding register", "holding registers"}]


def _apply_holding_header(
    record: dict[str, Any], columns: Sequence[tuple[int, Any]],
    location: Mapping[str, Any], *, header_row: int,
) -> list[dict[str, Any]]:
    claims = [{"parser_id": "structured.holding-header/v1", "field": "area",
               "value": "holding-register", "raw_header": str(raw), "raw_value": raw,
               "source_locator": {**location, "row": header_row, "column": i + 1}}
              for i, raw in columns if record.get("address", record.get("display_address", record.get("protocol_offset"))) not in (None, "")]
    if not claims:
        return []
    record.setdefault("_claims", []).extend(claims)
    conflicting = {key: value for key, value in record.items()
                   if re.fullmatch(r"area(?:_[0-9]+)?", key) and value not in (None, "")
                   and re.sub(r"[\s_-]+", " ", str(value).strip().casefold()) not in
                   {"holding", "holding register", "holding registers", "4x", "fc03", "03", "4x holding"}}
    if conflicting:
        return [{"code": "source.area-columns-conflict", "severity": "hold", "blocking": True,
                 "field": "area", "source": dict(location),
                 "message": "An explicit holding-register heading conflicts with a row area; no claim takes precedence.",
                 "details": {"header_claims": claims, "row_area_claims": conflicting}}]
    if record.get("area") in (None, ""):
        record["area"] = "holding-register"
    return []


def _canonicalize_mapping(record: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    output: dict[str, Any] = {}
    warnings: list[dict[str, Any]] = []
    for index, (raw_key, value) in enumerate(record.items()):
        key = _header_key(raw_key, index)
        if key == "source_offset":
            warnings.append({"code": "ambiguous_offset_header", "field": str(raw_key),
                             "message": "Offset is ambiguous between register address and engineering bias; the raw value is preserved as source_offset."})
        if key in output:
            suffix = 2
            while f"{key}_{suffix}" in output:
                suffix += 1
            replacement = f"{key}_{suffix}"
            warnings.append(
                {
                    "code": "duplicate_field",
                    "message": f"Fields normalize to the same name {key!r}; later value is {replacement!r}.",
                }
            )
            key = replacement
        output[key] = _trim_text(value)
    return output, warnings


def _has_offset_table_header(headers: Sequence[str]) -> bool:
    """Recognize a structured point table without resolving a plain Offset."""

    keys = set(headers)
    return (
        "source_offset" in keys
        and bool(keys & {"name", "description"})
        and bool(keys & {"access", "function_code", "area"})
    )


def _sheet_has_register_header(headers: Sequence[str]) -> bool:
    return any(header in _REGISTER_HEADER_KEYS for header in headers) or _has_offset_table_header(headers)


def _has_address(record: Mapping[str, Any]) -> bool:
    for key in _ADDRESS_KEYS:
        value = record.get(key)
        if isinstance(value, Mapping):
            if any(item not in (None, "") for item in value.values()):
                return True
        elif value not in (None, ""):
            return True
    return False


def _has_structured_address(record: Mapping[str, Any], headers: Sequence[str]) -> bool:
    """Keep contextual Offset rows as candidates, never as protocol addresses.

    Limit this path to delimited/XLSX tables. A name/description and an Offset
    alone also describe ordinary non-register data; require a recognizable
    access, function, or area value as row-level corroboration.
    """

    if _has_address(record):
        return True
    if not _has_offset_table_header(headers):
        return False
    offset = str(record.get("source_offset", "")).strip()
    if not re.fullmatch(r"[+-]?(?:0[xX][0-9a-fA-F]+|[0-9]+)", offset):
        return False
    if not any(record.get(field) not in (None, "") for field in ("name", "description")):
        return False
    access = re.sub(r"[\s_/-]+", "", str(record.get("access", "")).lower())
    function = str(record.get("function_code", "")).strip()
    area = str(record.get("area", "")).strip().lower()
    return (
        access in {"r", "ro", "read", "readonly", "rw", "readwrite", "w", "wo", "write", "writeonly"}
        or function in {"1", "01", "2", "02", "3", "03", "4", "04", "5", "05", "6", "06", "15", "16"}
        or area in _KNOWN_AREAS
    )


def _enum_warnings(record: Mapping[str, Any], source: Mapping[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    checks = (
        ("area", _KNOWN_AREAS, "unrecognized_area"),
        ("datatype", _KNOWN_DATATYPES, "unrecognized_datatype"),
        ("byte_order", _KNOWN_BYTE_ORDERS, "unrecognized_byte_order"),
    )
    for field, known, code in checks:
        value = record.get(field)
        if value in (None, ""):
            continue
        normalized = re.sub(
            r"\s+", " ", str(value).strip().lower().replace("_", " ")
        )
        if normalized not in known:
            warnings.append(
                {
                    "code": code,
                    "message": f"The source value {value!r} for {field} is not recognized and was preserved.",
                    **source,
                }
            )
    return warnings


def _decode_utf8(source: str | bytes, label: str) -> str:
    if isinstance(source, str):
        return source
    try:
        return source.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError(f"{label} must use UTF-8 encoding: {exc}") from exc


def parse_csv(source: str | bytes, *, delimiter: str | None = None) -> dict[str, Any]:
    """Parse a register-map CSV, TSV, PSV, or semicolon-delimited document."""

    text = _decode_utf8(source, "Delimited text")
    assumptions: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    source_holds: list[dict[str, Any]] = []
    if delimiter is None:
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
            delimiter = dialect.delimiter
            assumptions.append(
                {
                    "code": "detected_delimiter",
                    "message": f"Detected {delimiter!r} as the field delimiter.",
                    "value": delimiter,
                }
            )
        except csv.Error:
            delimiter = ","
            assumptions.append(
                {
                    "code": "delimiter_fallback",
                    "message": "Could not detect a delimiter; used comma for parsing.",
                    "value": delimiter,
                    "requires_review": True,
                }
            )
    if delimiter not in {",", ";", "\t", "|"}:
        raise ParseError("Delimiter must be comma, semicolon, tab, or pipe.")

    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    try:
        raw_headers = next(reader)
    except StopIteration:
        return _result([], source_format="csv", assumptions=assumptions)
    except csv.Error as exc:
        raise ParseError(f"CSV header is invalid: {exc}") from exc

    headers, header_warnings = _unique_headers(raw_headers)
    compound_columns, area_columns = _compound_header_columns(raw_headers, headers)
    holding_columns = _holding_header_columns(raw_headers, headers)
    warnings.extend(header_warnings)
    records: list[dict[str, Any]] = []
    try:
        for row in reader:
            source_location = {"row": reader.line_num, "format": "csv"}
            if not row or all(str(value).strip() == "" for value in row):
                continue
            if len(row) > len(headers):
                warnings.append(
                    {
                        "code": "extra_columns",
                        "message": "Row has values beyond the declared headers; they were preserved in _extra.",
                        **source_location,
                    }
                )
            padded = list(row[: len(headers)]) + [""] * max(0, len(headers) - len(row))
            record = {key: _trim_text(value) for key, value in zip(headers, padded)}
            if len(row) > len(headers):
                record["_extra"] = [_trim_text(value) for value in row[len(headers) :]]
            record["_source"] = source_location
            if compound_columns:
                record["_claims"] = _compound_header_claims(compound_columns, padded, source_location, header_row=1)
            if not _has_structured_address(record, headers):
                rejected.append(
                    {
                        "code": "missing_address",
                        "message": "Row has no address value.",
                        "record": record,
                        **source_location,
                    }
                )
                continue
            if holding_columns:
                source_holds.extend(_apply_holding_header(record, holding_columns, source_location, header_row=1))
            if area_columns:
                source_holds.extend(_compound_area_conflicts(
                    area_columns, padded, source_location, header_row=1,
                ))
            warnings.extend(_enum_warnings(record, source_location))
            records.append(record)
    except csv.Error as exc:
        raise ParseError(f"CSV data is invalid near physical line {reader.line_num}: {exc}") from exc
    return _result(
        records,
        source_format="csv",
        warnings=warnings,
        rejected_rows=rejected,
        assumptions=assumptions,
        source_holds=source_holds,
    )


def parse_json(source: str | bytes | Sequence[Any] | Mapping[str, Any]) -> dict[str, Any]:
    """Parse JSON arrays and objects that contain ``registers`` or ``data``."""

    if isinstance(source, (str, bytes)):
        try:
            value = json.loads(_decode_utf8(source, "JSON"))
        except json.JSONDecodeError as exc:
            raise ParseError(f"JSON is invalid: {exc.msg} at line {exc.lineno}, column {exc.colno}.") from exc
    else:
        value = source

    assumptions: list[dict[str, Any]] = []
    if isinstance(value, list):
        items = value
        collection = "$"
    elif isinstance(value, Mapping) and isinstance(value.get("registers"), list):
        items = value["registers"]
        collection = "registers"
    elif isinstance(value, Mapping) and isinstance(value.get("data"), list):
        items = value["data"]
        collection = "data"
    elif isinstance(value, Mapping) and isinstance(value.get("records"), list):
        items = value["records"]
        collection = "records"
    elif isinstance(value, Mapping) and isinstance(value.get("points"), list):
        items = value["points"]
        collection = "points"
    elif (
        isinstance(value, Mapping)
        and isinstance(value.get("data"), Mapping)
        and isinstance(value["data"].get("registers"), list)
    ):
        items = value["data"]["registers"]
        collection = "data.registers"
    else:
        raise ParseError("JSON root must be an array or contain a registers, data, records, or points array.")
    if collection != "$":
        assumptions.append(
            {
                "code": "selected_json_collection",
                "message": f"Used the {collection!r} array as register records.",
                "value": collection,
            }
        )

    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        location = {"index": index, "collection": collection, "format": "json"}
        if not isinstance(item, Mapping):
            rejected.append(
                {
                    "code": "record_not_object",
                    "message": "JSON register record is not an object.",
                    "value": item,
                    **location,
                }
            )
            continue
        record, record_warnings = _canonicalize_mapping(item)
        warnings.extend({**entry, **location} for entry in record_warnings)
        record["_source"] = location
        if not _has_address(record):
            rejected.append(
                {
                    "code": "missing_address",
                    "message": "Record has no address value.",
                    "record": record,
                    **location,
                }
            )
            continue
        warnings.extend(_enum_warnings(record, location))
        records.append(record)
    return _result(
        records,
        source_format="json",
        warnings=warnings,
        rejected_rows=rejected,
        assumptions=assumptions,
    )


def _safe_xml_root(data: bytes, label: str) -> ET.Element:
    if _XML_UNSAFE.search(data):
        raise ParseError(f"{label} contains a DTD or entity declaration; these declarations are prohibited.")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ParseError(f"{label} is invalid XML: {exc}") from exc


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_record(element: ET.Element) -> dict[str, Any]:
    raw: dict[str, Any] = {_local_name(key): value for key, value in element.attrib.items()}
    for child in list(element):
        key = _local_name(child.tag)
        if list(child):
            value: Any = "".join(child.itertext()).strip()
        else:
            value = (child.text or "").strip()
        if key in raw:
            previous = raw[key]
            raw[key] = previous + [value] if isinstance(previous, list) else [previous, value]
        else:
            raw[key] = value
    return raw


def parse_xml(source: str | bytes) -> dict[str, Any]:
    """Parse simple XML register collections without resolving external data."""

    data = source.encode("utf-8") if isinstance(source, str) else source
    root = _safe_xml_root(data, "XML")
    record_names = {"register", "point", "tag", "row"}
    elements = [element for element in root.iter() if _local_name(element.tag).lower() in record_names]
    assumptions: list[dict[str, Any]] = []
    if not elements:
        candidates = [element for element in list(root) if list(element) or element.attrib]
        if candidates:
            elements = candidates
            assumptions.append(
                {
                    "code": "generic_xml_records",
                    "message": "No register elements were present; direct child elements were treated as records.",
                    "requires_review": True,
                }
            )

    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for index, element in enumerate(elements):
        location = {
            "index": index,
            "element": _local_name(element.tag),
            "format": "xml",
        }
        record, record_warnings = _canonicalize_mapping(_xml_record(element))
        warnings.extend({**entry, **location} for entry in record_warnings)
        record["_source"] = location
        if not _has_address(record):
            rejected.append(
                {
                    "code": "missing_address",
                    "message": "XML record has no address value.",
                    "record": record,
                    **location,
                }
            )
            continue
        warnings.extend(_enum_warnings(record, location))
        records.append(record)
    return _result(
        records,
        source_format="xml",
        warnings=warnings,
        rejected_rows=rejected,
        assumptions=assumptions,
    )


def _read_source_bytes(source: bytes | bytearray | str | Path) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    return Path(source).read_bytes()


def _xlsx_xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        data = archive.read(name)
    except KeyError as exc:
        raise ParseError(f"XLSX is missing required part {name!r}.") from exc
    return _safe_xml_root(data, f"XLSX part {name}")


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _xlsx_xml(archive, "xl/sharedStrings.xml")
    strings: list[str] = []
    for item in root.iter():
        if _local_name(item.tag) != "si":
            continue
        strings.append("".join(node.text or "" for node in item.iter() if _local_name(node.tag) == "t"))
    return strings


def _xlsx_sheet_paths(archive: zipfile.ZipFile, *, hidden_sheets: set[str] | None = None) -> list[tuple[str, str]]:
    names = set(archive.namelist())
    if {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"}.issubset(names):
        workbook = _xlsx_xml(archive, "xl/workbook.xml")
        relationships = _xlsx_xml(archive, "xl/_rels/workbook.xml.rels")
    else:
        paths = sorted(name for name in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name))
        if not paths:
            raise ParseError("XLSX is missing its workbook relationships and has no worksheet parts.")
        return [(Path(path).stem, path) for path in paths]

    # Real workbooks route many relationship types (customXml, calcChain, theme, ...)
    # through xl/_rels/workbook.xml.rels, and some of those legitimately target parts
    # outside xl/ (e.g. Target="../customXml/item1.xml"). Only worksheet relationships
    # are ever used to load a sheet below, so only those need the anti-traversal check.
    relation_targets: dict[str, str] = {}
    for relationship in relationships.iter():
        if _local_name(relationship.tag) != "Relationship":
            continue
        relation_id = relationship.attrib.get("Id")
        target = relationship.attrib.get("Target")
        if relation_id and target:
            relation_targets[relation_id] = posixpath.normpath(posixpath.join("xl", target))

    output: list[tuple[str, str]] = []
    for sheet in workbook.iter():
        if _local_name(sheet.tag) != "sheet":
            continue
        name = sheet.attrib.get("name", f"Sheet{len(output) + 1}")
        if hidden_sheets is not None and sheet.attrib.get("state") in {"hidden", "veryHidden"}:
            hidden_sheets.add(name)
        relation_id = next((value for key, value in sheet.attrib.items() if _local_name(key) == "id"), None)
        if relation_id and relation_id in relation_targets:
            path = relation_targets[relation_id]
            if not path.startswith("xl/") or path.startswith("xl/../"):
                raise ParseError("XLSX worksheet relationship leaves the workbook directory.")
            output.append((name, path))
    if not output:
        raise ParseError("XLSX workbook has no readable worksheets.")
    return output


def _column_index(cell_reference: str) -> int:
    match = re.match(r"([A-Za-z]+)", cell_reference)
    if not match:
        raise ParseError(f"Invalid XLSX cell reference {cell_reference!r}.")
    result = 0
    for character in match.group(1).upper():
        result = result * 26 + ord(character) - ord("A") + 1
    return result - 1


def _xlsx_cell_value(cell: ET.Element, shared_strings: Sequence[str]) -> tuple[Any, bool]:
    cell_type = cell.attrib.get("t")
    formula = next((child for child in cell if _local_name(child.tag) == "f"), None)
    value_node = next((child for child in cell if _local_name(child.tag) == "v"), None)
    inline = next((child for child in cell if _local_name(child.tag) == "is"), None)
    raw = value_node.text if value_node is not None else None
    if cell_type == "inlineStr" and inline is not None:
        value: Any = "".join(node.text or "" for node in inline.iter() if _local_name(node.tag) == "t")
    elif cell_type == "s" and raw is not None:
        try:
            value = shared_strings[int(raw)]
        except (ValueError, IndexError) as exc:
            raise ParseError(f"XLSX shared-string index {raw!r} is invalid.") from exc
    elif cell_type == "b":
        value = raw == "1"
    elif cell_type in {"str", "e"}:
        value = raw or ""
    elif raw in (None, ""):
        value = f"={formula.text or ''}" if formula is not None else ""
    else:
        try:
            number = float(raw)
            value = int(number) if number.is_integer() else number
        except ValueError:
            value = raw
    return value, formula is not None


def _xlsx_rows(root: ET.Element, shared_strings: Sequence[str]) -> Iterable[tuple[int, list[Any], bool]]:
    for row in root.iter():
        if _local_name(row.tag) != "row":
            continue
        values: dict[int, Any] = {}
        has_formula = False
        for cell in row:
            if _local_name(cell.tag) != "c":
                continue
            index = _column_index(cell.attrib.get("r", "A1"))
            value, formula = _xlsx_cell_value(cell, shared_strings)
            values[index] = value
            has_formula = has_formula or formula
        width = max(values, default=-1) + 1
        row_number = int(row.attrib.get("r", "0") or 0)
        yield row_number, [values.get(index, "") for index in range(width)], has_formula


def _xlsx_headers(
    values: Sequence[Any], preceding_rows: Sequence[tuple[int, list[Any], bool]]
) -> tuple[list[str], list[dict[str, Any]]]:
    """Resolve narrowly contextual column labels without inventing address basis."""

    headers, warnings = _unique_headers(values)
    keys = set(headers)
    if _sheet_has_register_header(headers):
        return headers, warnings

    # Dec/Hex alone also occurs in non-map number tables. Require the surrounding
    # point-table roles before treating Dec as a generic source address. Hex stays
    # raw evidence; neither radix establishes zero/one-based protocol semantics.
    decimal_table = (
        {"dec", "hex", "name", "datatype"}.issubset(keys)
        and bool(keys & {"count", "word_count", "size"})
    )
    register_context = False
    for _, previous_values, _ in reversed(preceding_rows):
        populated = [str(value).strip() for value in previous_values if value not in (None, "")]
        if any(re.search(r"\bDNP3\b", value, re.IGNORECASE) for value in populated):
            break
        if len(populated) <= 2 and any(
            re.match(r"^(?:(?:holding|input)\s+registers?\b|modbus\s+registers?\b)", value, re.IGNORECASE)
            for value in populated
        ):
            register_context = True
            break
    indexed_table = (
        register_context
        and {"index", "name", "datatype", "function_code"}.issubset(keys)
    )
    if decimal_table or indexed_table:
        source_key = "dec" if decimal_table else "index"
        headers[headers.index(source_key)] = "address"
        warnings.append(
            {
                "code": "contextual_xlsx_address_header",
                "column": headers.index("address") + 1,
                "message": (
                    f"Used {source_key!r} as a source address from the surrounding register-table columns; "
                    "address area and zero/one-based convention remain unresolved."
                ),
                "requires_review": True,
            }
        )
    return headers, warnings


def _skip_title_rows(non_empty_rows: Sequence[tuple[int, list[Any], bool]]) -> tuple[int, list[int]]:
    """Return the header row index, skipping leading title / metadata rows.

    Vendor workbooks often place a merged worksheet title, a 2-column
    label/value preamble, or a group banner directly above the real header
    row. Prefer coherent register-table columns over a banner that merely
    contains a register word. Fall back to the legacy
    single-distinct-value title skip when no register header is found in the
    first several rows.
    """

    look_ahead = min(len(non_empty_rows), 16)
    fallback_index: int | None = None
    fallback_address_columns: list[int] = []
    semantic_columns = {"name", "description", "datatype", "function_code", "access", "area", "word_count", "scale"}
    for index in range(look_ahead):
        _, values, _ = non_empty_rows[index]
        if fallback_index is not None and any(
            column < len(values)
            and re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)", str(values[column]).strip())
            for column in fallback_address_columns
        ):
            # Numeric rows prove that an address-only header was a real table,
            # not a banner. Do not jump over its data to a later richer table.
            return fallback_index, [non_empty_rows[i][0] for i in range(fallback_index)]
        headers, _ = _xlsx_headers(values, non_empty_rows[:index])
        if _sheet_has_register_header(headers):
            if fallback_index is None:
                fallback_index = index
                fallback_address_columns = [i for i, header in enumerate(headers) if header in _ADDRESS_KEYS]
            if set(headers) & semantic_columns:
                # A later, denser table must not displace an earlier real header
                # and silently drop its data. Only bypass address-only banners.
                return index, [non_empty_rows[i][0] for i in range(index)]
    if fallback_index is not None:
        index = fallback_index
        skipped = [non_empty_rows[i][0] for i in range(index)]
        return index, skipped

    skipped: list[int] = []
    index = 0
    while index < len(non_empty_rows) - 1:
        row_number, values, _ = non_empty_rows[index]
        populated = [value for value in values if value not in (None, "")]
        distinct = len(set(populated))
        if distinct > 1:
            break
        _, next_values, _ = non_empty_rows[index + 1]
        next_filled = sum(1 for value in next_values if value not in (None, ""))
        # Allow equal-width single-value title chains (stacked sheet titles).
        if next_filled < len(populated):
            break
        if next_filled == len(populated) and distinct == 1:
            # Only keep skipping when the next row is also a single-value title
            # candidate; a same-width multi-value row is treated as the header.
            next_distinct = len({v for v in next_values if v not in (None, "")})
            if next_distinct > 1:
                skipped.append(row_number)
                index += 1
                break
        skipped.append(row_number)
        index += 1
    return index, skipped


def _xlsx_datatype_legends(
    rows: Sequence[tuple[int, list[Any], bool]],
) -> list[list[tuple[int, list[Any], bool]]]:
    """Identify explicitly headed datatype dictionaries, not register names.

    A datatype heading followed by Type/Description/Range columns establishes
    a different table. Only contiguous, recognizable datatype-definition rows
    belong to it; arbitrary addresses or later register tables are not masked.
    """
    groups: list[list[tuple[int, list[Any], bool]]] = []
    for index in range(len(rows) - 1):
        if rows[index][2] or rows[index + 1][2]:
            continue
        populated = [str(v).strip() for v in rows[index][1] if v not in (None, "")]
        if len(populated) != 1 or not re.fullmatch(
            r"(?:modbus\s+)?data\s*types\s*:?", populated[0], re.IGNORECASE
        ):
            continue
        values = rows[index + 1][1]
        headers = [_header_key(value, column) for column, value in enumerate(values)]
        if not {"datatype", "description", "range"}.issubset(headers) or set(headers) & _ADDRESS_KEYS:
            continue
        columns = [headers.index(field) for field in ("datatype", "description", "range")]
        group = [rows[index], rows[index + 1]]
        for row in rows[index + 2 :]:
            if row[2]:
                break
            values = row[1]
            if any(column >= len(values) or values[column] in (None, "") for column in columns):
                break
            if any(value not in (None, "") for column, value in enumerate(values) if column not in columns):
                break
            if not re.fullmatch(
                r"(?:u?int(?:8|16|32|64)?|float(?:16|32|64)?|bool(?:ean)?|double|word|dword|string|ascii)",
                str(values[columns[0]]).strip(), re.IGNORECASE,
            ):
                break
            group.append(row)
        # Require an actual definition after the heading/header, not a title
        # whose coincidental column labels happen to resemble a dictionary.
        if len(group) > 2:
            groups.append(group)
    return groups


def parse_xlsx(source: bytes | bytearray | str | Path) -> dict[str, Any]:
    """Parse basic XLSX worksheets with shared, inline, numeric, and formula cells."""

    data = _read_source_bytes(source)
    try:
        archive_context = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ParseError("XLSX source is not a valid ZIP workbook.") from exc

    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    assumptions: list[dict[str, Any]] = []
    source_holds: list[dict[str, Any]] = []
    scale_notes: list[dict[str, Any]] = []
    scale_tables: dict[str, tuple[list[Any], list[str], int]] = {}

    def keep_scale_note(note: dict[str, Any] | None) -> None:
        if note is not None:
            if len(scale_notes) >= _SCALE_NOTE_LIMIT:
                raise ParseError("XLSX exceeds the 256 scalar conversion statement evidence limit.")
            scale_notes.append(note)
    with archive_context as archive:
        total_size = sum(item.file_size for item in archive.infolist())
        if total_size > _MAX_XLSX_UNCOMPRESSED_BYTES:
            raise ParseError("XLSX uncompressed content exceeds the 50 MiB safety limit.")
        shared_strings = _xlsx_shared_strings(archive)
        hidden_sheets: set[str] = set()
        sheets = _xlsx_sheet_paths(archive, hidden_sheets=hidden_sheets)
        for sheet_name, sheet_path in sheets:
            root = _xlsx_xml(archive, sheet_path)
            rows = list(_xlsx_rows(root, shared_strings))
            non_empty = [row for row in rows if any(value not in (None, "") for value in row[1])]
            if not non_empty:
                warnings.append(
                    {
                        "code": "empty_worksheet",
                        "message": f"Worksheet {sheet_name!r} is empty.",
                        "sheet": sheet_name,
                    }
                )
                continue
            legend_rows: set[int] = set()
            for group in _xlsx_datatype_legends(non_empty):
                legend_rows.update(row_number for row_number, _, _ in group)
                assumptions.append(
                    {
                        "code": "excluded_xlsx_datatype_legend",
                        "message": "Excluded an explicitly headed datatype dictionary, not register points.",
                        "sheet": sheet_name,
                        "rows": [row_number for row_number, _, _ in group],
                        "source_rows": [
                            {"row": row_number, "values": values}
                            for row_number, values, _ in group
                        ],
                    }
                )
            header_index, skipped_titles = _skip_title_rows(non_empty)
            header_row_number, header_values, header_formula = non_empty[header_index]
            headers, header_warnings = _xlsx_headers(header_values, non_empty[:header_index])
            compound_columns, area_columns = _compound_header_columns(header_values, headers)
            holding_columns = _holding_header_columns(header_values, headers)
            if not _sheet_has_register_header(headers):
                # A bounded scalar statement needs conversion-topic context;
                # merely dividing a display or mentioning a number is not one.
                for row_number, values, has_formula in non_empty:
                    if sheet_name in hidden_sheets or not any(
                        isinstance(value, str) and value.strip().casefold() in {"scaling", "conversion"}
                        for value in values
                    ):
                        continue
                    for column, value in enumerate(values, 1):
                        note = _xlsx_scale_note(value, {
                            "format": "xlsx", "sheet": sheet_name, "row": row_number, "column": column,
                        })
                        if note is not None and has_formula:
                            note.update({"scope": "unresolved", "cached_formula_row": True})
                        keep_scale_note(note)
                warnings.append(
                    {
                        "code": "skipped_non_register_worksheet",
                        "message": (
                            f"Worksheet {sheet_name!r} has no register-map header; "
                            "it was skipped."
                        ),
                        "sheet": sheet_name,
                        "row": header_row_number,
                    }
                )
                continue
            scale_tables[sheet_name] = (list(header_values), headers, header_row_number)
            note_columns = [index for index, header in enumerate(headers) if header in {"notes", "description"}] if sheet_name not in hidden_sheets else []
            warnings.extend({**entry, "sheet": sheet_name, "row": header_row_number} for entry in header_warnings)
            if skipped_titles:
                assumptions.append(
                    {
                        "code": "skipped_title_row",
                        "message": (
                            f"Skipped single-cell title row(s) {skipped_titles} above the header in worksheet "
                            f"{sheet_name!r}."
                        ),
                        "sheet": sheet_name,
                        "rows": skipped_titles,
                    }
                )
            assumptions.append(
                {
                    "code": "xlsx_header_row",
                    "message": f"Used row {header_row_number} of worksheet {sheet_name!r} as the header.",
                    "sheet": sheet_name,
                    "row": header_row_number,
                }
            )
            if header_formula:
                warnings.append(
                    {
                        "code": "formula_in_header",
                        "message": "Header row contains a formula; only its cached value was used.",
                        "sheet": sheet_name,
                        "row": header_row_number,
                    }
                )
            for row_number, row_values, has_formula in non_empty[header_index + 1 :]:
                if row_number in legend_rows:
                    continue
                location = {"sheet": sheet_name, "row": row_number, "format": "xlsx"}
                padded = list(row_values[: len(headers)]) + [""] * max(0, len(headers) - len(row_values))
                record = {key: _trim_text(value) for key, value in zip(headers, padded)}
                if len(row_values) > len(headers):
                    record["_extra"] = [_trim_text(value) for value in row_values[len(headers) :]]
                record["_source"] = location
                if compound_columns:
                    record["_claims"] = _compound_header_claims(
                        compound_columns, padded, location, header_row=header_row_number,
                    )
                if has_formula:
                    warnings.append(
                        {
                            "code": "formula_cached_value",
                            "message": "Row contains a formula; the parser did not execute it and used its cached value.",
                            **location,
                        }
                    )
                if not _has_structured_address(record, headers):
                    rejected.append(
                        {
                            "code": "missing_address",
                            "message": "Worksheet row has no address value.",
                            "record": record,
                            **location,
                        }
                    )
                    continue
                for column in note_columns:
                    note = _xlsx_scale_note(padded[column], {**location, "column": column + 1}, row_local=True)
                    if note is not None and has_formula:
                        note.update({"scope": "unresolved", "cached_formula_row": True})
                    keep_scale_note(note)
                if holding_columns:
                    source_holds.extend(_apply_holding_header(record, holding_columns, location, header_row=header_row_number))
                if area_columns:
                    source_holds.extend(_compound_area_conflicts(
                        area_columns, padded, location, header_row=header_row_number,
                    ))
                warnings.extend(_enum_warnings(record, location))
                records.append(record)
    _xlsx_scale_claims(records, scale_notes, scale_tables)
    if scale_notes:
        assumptions.append({"code": "xlsx-scalar-conversion-evidence",
                            "message": "Retained bounded source conversion statements; direction and scope are not inferred.",
                            "notes": scale_notes})
    return _result(
        records,
        source_format="xlsx",
        warnings=warnings,
        rejected_rows=rejected,
        assumptions=assumptions,
        source_holds=source_holds,
    )


def parse_source(
    source: Any,
    *,
    source_format: str | None = None,
    filename: str | Path | None = None,
    delimiter: str | None = None,
) -> dict[str, Any]:
    """Dispatch a source to one of the supported deterministic parsers."""

    inferred = source_format.lower().lstrip(".") if source_format else None
    if inferred is None and filename is not None:
        inferred = Path(filename).suffix.lower().lstrip(".")
    if inferred in {"csv", "tsv", "psv"}:
        selected_delimiter = delimiter
        if inferred == "tsv" and delimiter is None:
            selected_delimiter = "\t"
        elif inferred == "psv" and delimiter is None:
            selected_delimiter = "|"
        return parse_csv(source, delimiter=selected_delimiter)
    if inferred == "json":
        return parse_json(source)
    if inferred == "xml":
        return parse_xml(source)
    if inferred == "xlsx":
        return parse_xlsx(source)
    raise ParseError("Source format must be csv, tsv, psv, json, xml, or xlsx.")


__all__ = [
    "ParseError",
    "parse_csv",
    "parse_json",
    "parse_source",
    "parse_xlsx",
    "parse_xml",
]
