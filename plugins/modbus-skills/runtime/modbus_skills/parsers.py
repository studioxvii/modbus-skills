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
    "register_address": "address",
    "modbus_address": "address",
    "modbus_address_read": "address",
    "holding_register": "address",
    "holding_registers": "address",
    "reference": "address",
    "ref": "address",
    "protocol_offset": "protocol_offset",
    "pdu_offset": "protocol_offset",
    "zero_based_address": "protocol_offset",
    "zero_based_offset": "protocol_offset",
    "display_address": "display_address",
    "reference_number": "display_address",
    "modicon_reference": "display_address",
    "address_convention": "address_convention",
    "address_basis": "address_convention",
    "address_format": "address_convention",
    "area": "area",
    "register_area": "area",
    "register_type": "area",
    "table": "area",
    "object_type": "area",
    "name": "name",
    "tag": "name",
    "tag_name": "name",
    "point": "name",
    "point_name": "name",
    "parameter_name": "name",
    "description": "description",
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
    "registers": "word_count",
    "length_words": "word_count",
    "unit_id": "unit_id",
    "unit": "unit_id",
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
    "offset_value": "offset",
    "bias": "offset",
    "engineering_unit": "engineering_unit",
    "engineering_units": "engineering_unit",
    "units": "engineering_unit",
    "eu": "engineering_unit",
    "access": "access",
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
_KNOWN_AREAS = {
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


def _result(
    records: list[dict[str, Any]],
    *,
    source_format: str,
    warnings: list[dict[str, Any]] | None = None,
    rejected_rows: list[dict[str, Any]] | None = None,
    assumptions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "format": source_format,
        "records": records,
        "warnings": warnings or [],
        "rejected_rows": rejected_rows or [],
        "assumptions": assumptions or [],
    }


def _header_key(value: Any, column_index: int) -> str:
    text = "" if value is None else str(value).strip()
    snake_text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    normalized = re.sub(r"[^a-z0-9]+", "_", snake_text.lower()).strip("_")
    if not normalized:
        return f"column_{column_index + 1}"
    return _HEADER_ALIASES.get(normalized, normalized)


def _unique_headers(values: Sequence[Any]) -> tuple[list[str], list[dict[str, Any]]]:
    headers: list[str] = []
    warnings: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for index, value in enumerate(values):
        key = _header_key(value, index)
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


def _canonicalize_mapping(record: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    output: dict[str, Any] = {}
    warnings: list[dict[str, Any]] = []
    for index, (raw_key, value) in enumerate(record.items()):
        key = _header_key(raw_key, index)
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


def _has_address(record: Mapping[str, Any]) -> bool:
    for key in _ADDRESS_KEYS:
        value = record.get(key)
        if isinstance(value, Mapping):
            if any(item not in (None, "") for item in value.values()):
                return True
        elif value not in (None, ""):
            return True
    return False


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
            if not _has_address(record):
                rejected.append(
                    {
                        "code": "missing_address",
                        "message": "Row has no address value.",
                        "record": record,
                        **source_location,
                    }
                )
                continue
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
    elif (
        isinstance(value, Mapping)
        and isinstance(value.get("data"), Mapping)
        and isinstance(value["data"].get("registers"), list)
    ):
        items = value["data"]["registers"]
        collection = "data.registers"
    else:
        raise ParseError("JSON root must be an array or contain a registers or data array.")
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


def _xlsx_sheet_paths(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
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


def _skip_title_rows(non_empty_rows: Sequence[tuple[int, list[Any], bool]]) -> tuple[int, list[int]]:
    """Return the header row index, skipping leading single-value title rows.

    Vendor workbooks often place a merged worksheet title directly above the
    real header row. Some workbooks lay out two side-by-side table blocks that
    repeat the same title text in more than one cell of that row (e.g. a full
    table in columns A-G and a condensed duplicate in columns I-K, both titled
    "PowerLogic PM8000 Power Quality Meter"). Count *distinct* populated
    values rather than populated cells so a repeated title still counts as
    one title, not a multi-column header. Treat a leading row as a title,
    not a header, only when it has at most one distinct populated value
    *and* the next row is wider (more populated cells), so a single-column
    worksheet's genuine one-cell header is never mistaken for a title.
    """

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
        if next_filled <= len(populated):
            break
        skipped.append(row_number)
        index += 1
    return index, skipped


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
    with archive_context as archive:
        total_size = sum(item.file_size for item in archive.infolist())
        if total_size > _MAX_XLSX_UNCOMPRESSED_BYTES:
            raise ParseError("XLSX uncompressed content exceeds the 50 MiB safety limit.")
        shared_strings = _xlsx_shared_strings(archive)
        sheets = _xlsx_sheet_paths(archive)
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
            header_index, skipped_titles = _skip_title_rows(non_empty)
            header_row_number, header_values, header_formula = non_empty[header_index]
            headers, header_warnings = _unique_headers(header_values)
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
                location = {"sheet": sheet_name, "row": row_number, "format": "xlsx"}
                padded = list(row_values[: len(headers)]) + [""] * max(0, len(headers) - len(row_values))
                record = {key: _trim_text(value) for key, value in zip(headers, padded)}
                if len(row_values) > len(headers):
                    record["_extra"] = [_trim_text(value) for value in row_values[len(headers) :]]
                record["_source"] = location
                if has_formula:
                    warnings.append(
                        {
                            "code": "formula_cached_value",
                            "message": "Row contains a formula; the parser did not execute it and used its cached value.",
                            **location,
                        }
                    )
                if not _has_address(record):
                    rejected.append(
                        {
                            "code": "missing_address",
                            "message": "Worksheet row has no address value.",
                            "record": record,
                            **location,
                        }
                    )
                    continue
                warnings.extend(_enum_warnings(record, location))
                records.append(record)
    return _result(
        records,
        source_format="xlsx",
        warnings=warnings,
        rejected_rows=rejected,
        assumptions=assumptions,
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
