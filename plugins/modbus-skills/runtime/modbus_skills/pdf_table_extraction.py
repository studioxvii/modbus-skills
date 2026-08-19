"""Grid-aware extraction for text PDFs with drawn or aligned register tables."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


class PdfTableExtractionError(ValueError):
    """Raised when the optional grid extractor cannot run safely."""


_ADDRESS_TOKEN = r"(?:0[xX][0-9A-Fa-f]+|(?:[34][xX]){1,2}\d+|\d+)"
_ADDRESS = re.compile(
    rf"^(?P<first>{_ADDRESS_TOKEN})(?:(?P<separator>[/\-])(?P<second>{_ADDRESS_TOKEN}))?(?P<footnote>\*)?$"
)
_PAIR_SUFFIX = re.compile(r"\s*\((MSR|LSR)\)$", re.IGNORECASE)
_BIT_ENUMERATION = re.compile(
    r"\b0\s*[—–-]\s*No\b.*\b1\s*[—–-]\s*Yes\b", re.IGNORECASE
)
PDF_HEADER_ALIASES = {
    "address": "address",
    "register": "address",
    "register address": "address",
    "register address (decimal)": "address",
    "register number": "address",
    "reg no": "address",
    "reg no.": "address",
    "reg addr": "address",
    "reg addr.": "address",
    "start": "address",
    "start address": "address",
    "reg": "address",
    "reg.": "address",
    "protocol offset": "protocol_offset",
    "display address": "display_address",
    "r/w": "access",
    "access": "access",
    "nv": "nonvolatile",
    "format": "format",
    "data type": "format",
    "datatype": "format",
    "type": "format",
    "size": "word_count",
    "width": "word_count",
    "units": "units",
    "unit": "units",
    "scale": "scale",
    "scale factor": "scale",
    "range": "range",
    "description": "description",
    "meaning": "description",
    "name": "name",
    "tag": "name",
    "symbolic register name": "name",
    "parameter": "name",
    "variable": "name",
    "modbus register type": "area",
    "area": "area",
    "unit id": "unit_id",
    "word count": "word_count",
    "byte order": "byte_order",
    "bit order": "bit_order",
}
_HEADER_NAMES = PDF_HEADER_ALIASES
_INHERITED_FIELDS = frozenset(
    {"access", "nonvolatile", "format", "units", "scale", "range"}
)
_AREA_BY_PREFIX = {"3": "input-register", "4": "holding-register"}
_PREFIX_BY_AREA = {value: key for key, value in _AREA_BY_PREFIX.items()}
_MAX_GRID_PAGES = 256
_MAX_GRID_RECORDS = 50_000
_MAX_GRID_OUTPUT_BYTES = 32_000_000
_GRID_TIMEOUT_SECONDS = 60


def extract_pdf_table_evidence(
    path: Path, *, pages: Sequence[int] | None = None, timeout_seconds: int = _GRID_TIMEOUT_SECONDS
) -> dict[str, list[dict[str, Any]]]:
    """Extract accepted and quarantined rows through a bounded worker."""

    selected = sorted(set(pages)) if pages is not None else None
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= _GRID_TIMEOUT_SECONDS:
        raise PdfTableExtractionError(
            f"grid extraction timeout must be from 1 through {_GRID_TIMEOUT_SECONDS} seconds"
        )
    if selected is not None:
        if any(isinstance(page, bool) or not isinstance(page, int) or page < 1 for page in selected):
            raise PdfTableExtractionError("grid extraction pages must be positive integers")
        if len(selected) > _MAX_GRID_PAGES:
            raise PdfTableExtractionError(
                f"grid extraction is limited to {_MAX_GRID_PAGES} selected pages"
            )
    return _run_grid_worker(path, selected, timeout_seconds)


def extract_pdf_table_rows(
    path: Path, *, pages: Sequence[int] | None = None, timeout_seconds: int = _GRID_TIMEOUT_SECONDS
) -> list[dict[str, Any]]:
    """Compatibility projection containing only accepted grid rows."""

    return extract_pdf_table_evidence(
        path, pages=pages, timeout_seconds=timeout_seconds
    )["records"]


def _run_grid_worker(
    path: Path, selected: Sequence[int] | None, timeout_seconds: int
) -> dict[str, list[dict[str, Any]]]:
    argv = [sys.executable, str(Path(__file__).resolve()), "--worker", str(path)]
    if selected is not None:
        argv.append(",".join(map(str, selected)))
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(argv, stdout=stdout, stderr=stderr, shell=False)
            try:
                returncode = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise PdfTableExtractionError(
                    f"grid extraction exceeded the {timeout_seconds} second limit"
                ) from exc
            stdout.seek(0, 2)
            if stdout.tell() > _MAX_GRID_OUTPUT_BYTES:
                raise PdfTableExtractionError(
                    f"grid extraction output exceeds {_MAX_GRID_OUTPUT_BYTES} bytes"
                )
            stdout.seek(0)
            payload = stdout.read(_MAX_GRID_OUTPUT_BYTES + 1)
            stderr.seek(0)
            error_text = stderr.read(4_096).decode("utf-8", errors="replace").strip()
    except OSError as exc:
        raise PdfTableExtractionError("grid extraction worker could not start") from exc
    if returncode != 0:
        raise PdfTableExtractionError(error_text or "grid extraction worker failed")
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PdfTableExtractionError("grid extraction worker returned malformed output") from exc
    if not isinstance(decoded, Mapping) or set(decoded) != {
        "records",
        "quarantined_records",
    }:
        raise PdfTableExtractionError("grid extraction worker returned invalid evidence")
    result: dict[str, list[dict[str, Any]]] = {}
    for field in ("records", "quarantined_records"):
        rows = decoded.get(field)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise PdfTableExtractionError(
                f"grid extraction worker returned an invalid {field} list"
            )
        result[field] = rows
    return result


def _extract_pdf_table_rows_in_process(
    path: Path, *, pages: Sequence[int] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Run pdfplumber inside the bounded worker process."""

    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - exercised in dependency preflight
        raise PdfTableExtractionError(
            "pdfplumber is required for grid-aware PDF table recovery"
        ) from exc

    selected = set(pages) if pages is not None else None
    evidence = {"records": [], "quarantined_records": []}
    try:
        with pdfplumber.open(path) as document:
            if selected is None and len(document.pages) > _MAX_GRID_PAGES:
                raise PdfTableExtractionError(
                    f"automatic grid extraction is limited to {_MAX_GRID_PAGES} PDF pages"
                )
            for page_number, page in enumerate(document.pages, start=1):
                if selected is not None and page_number not in selected:
                    continue
                for table_index, table in enumerate(page.extract_tables()):
                    parsed = parse_pdf_table_evidence(
                        table,
                        page_number=page_number,
                        table_index=table_index,
                    )
                    evidence["records"].extend(parsed["records"])
                    evidence["quarantined_records"].extend(
                        parsed["quarantined_records"]
                    )
                    if sum(map(len, evidence.values())) > _MAX_GRID_RECORDS:
                        raise PdfTableExtractionError(
                            f"grid extraction exceeds {_MAX_GRID_RECORDS} records"
                        )
    except PdfTableExtractionError:
        raise
    except Exception as exc:
        raise PdfTableExtractionError(
            "pdfplumber could not extract bounded table geometry"
        ) from exc
    return evidence


def parse_pdf_table(
    table: Sequence[Sequence[Any]], *, page_number: int, table_index: int
) -> list[dict[str, Any]]:
    """Compatibility projection containing accepted rows from one grid table."""

    return parse_pdf_table_evidence(
        table, page_number=page_number, table_index=table_index
    )["records"]


def parse_pdf_table_evidence(
    table: Sequence[Sequence[Any]], *, page_number: int, table_index: int
) -> dict[str, list[dict[str, Any]]]:
    """Parse one grid table and retain ambiguous rows separately."""

    if not table:
        return {"records": [], "quarantined_records": []}
    header_index, columns, extra_columns, confident = _find_header(table)
    if columns is None:
        return {"records": [], "quarantined_records": []}
    records: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    inherited: dict[str, tuple[str, dict[str, Any]]] = {}
    for row_index, raw_row in enumerate(table[header_index + 1 :], start=header_index + 1):
        row = list(raw_row)
        resolved, conflicts = _resolve_cells(row, columns)
        region = f"p{page_number}:t{table_index}:r{row_index}"
        source = {
            "format": "pdf",
            "page": page_number,
            "row": row_index,
            "region": region,
            "parser_id": "pdfplumber-table/v1",
            "method": "coordinate-derived",
            "excerpt": " | ".join(_clean_values(row))[:300],
        }
        if conflicts:
            address_text = resolved.get("address", "") or " | ".join(
                _clean_values(
                    _cell(row, column) for column, _header in columns["address"]
                )
            )
            quarantined.append(
                {
                    "code": "pdf-grid-column-ambiguous",
                    "fields": sorted(conflicts),
                    "source_register": address_text,
                    "_source": source,
                }
            )
            continue
        address_text = resolved.get("address", "")
        parsed_address = _parse_pdf_address(address_text)
        if parsed_address is None:
            continue
        values: dict[str, str] = {}
        claims: list[dict[str, Any]] = []
        for field, candidates in columns.items():
            if field == "address":
                value = address_text
            else:
                value = resolved.get(field, "")
            inherited_claim = inherited.get(field) if not value else None
            if field in _INHERITED_FIELDS and inherited_claim is not None:
                value, claim = inherited_claim
                claims.append(dict(claim))
                values[field] = value
                continue
            if field != "address":
                values[field] = value
            chosen = next(
                (
                    candidate
                    for candidate in candidates
                    if _clean(_cell(row, candidate[0])) == value
                ),
                candidates[0],
            )
            claim = {
                "parser_id": "pdfplumber-table/v1",
                "field": field,
                "value": value,
                "raw_header": chosen[1],
                "raw_value": _clean(_cell(row, chosen[0])),
                "column_index": chosen[0],
                "source_locator": {
                    "page": page_number,
                    "row": row_index,
                    "region": region,
                },
            }
            claims.append(claim)
            if field in _INHERITED_FIELDS and value:
                inherited[field] = (value, claim)
        name = values.get("name", "")
        description = values.get("description", "")
        if not name and not description:
            continue
        raw_area = values.get("area", "")
        area, area_error = _parse_register_area(raw_area)
        if area_error is not None:
            quarantined.append(
                {
                    "code": "pdf-grid-register-area-ambiguous",
                    "fields": ["area"],
                    "source_register": address_text,
                    "name": name or description,
                    "description": description or name,
                    "raw_area": raw_area,
                    "_source": source,
                }
            )
            continue
        if parsed_address["status"] != "single":
            quarantined.append(
                {
                    "code": str(parsed_address["code"]),
                    "fields": ["address"],
                    "source_register": address_text,
                    "name": name or description,
                    "description": description or name,
                    "address_parse": _address_parse_evidence(parsed_address),
                    "_source": source,
                }
            )
            continue
        parsed_area = parsed_address.get("area")
        if area is not None and parsed_area is not None and area != parsed_area:
            quarantined.append(
                {
                    "code": "pdf-grid-address-area-conflict",
                    "fields": ["address", "area"],
                    "source_register": address_text,
                    "name": name or description,
                    "description": description or name,
                    "address_area": parsed_area,
                    "register_type_area": area,
                    "_source": source,
                }
            )
            continue
        if area is not None and parsed_area is None:
            parsed_address = _address_with_area(parsed_address, area)
        if (
            not resolved.get("format")
            and not resolved.get("access")
            and _BIT_ENUMERATION.search(source["excerpt"])
        ):
            quarantined.append(
                {
                    "code": "pdf-grid-bit-list-vs-register-unresolved",
                    "fields": ["datatype", "access"],
                    "source_register": address_text,
                    "name": name or description,
                    "description": description or name,
                    "address_parse": _address_parse_evidence(parsed_address),
                    "_source": source,
                }
            )
            continue
        if not confident:
            quarantined.append(
                {
                    "code": "pdf-grid-type-unresolved",
                    "fields": ["datatype", "access"],
                    "source_register": address_text,
                    "name": name or description,
                    "description": description or name,
                    "address_parse": _address_parse_evidence(parsed_address),
                    "_source": source,
                }
            )
            continue
        description_candidates = columns.get("description", [])
        description_column = max((item[0] for item in description_candidates), default=None)
        last_header_column = max(
            [
                column
                for candidates in columns.values()
                for column, _header in candidates
            ]
            + [column for column, _header in extra_columns],
            default=-1,
        )
        trailing = []
        if description_column is not None and description_column == last_header_column:
            trailing = _clean_values(row[last_header_column + 1 :])
        if trailing and trailing[0] in {"MSR", "LSR"}:
            suffix = trailing.pop(0)
            if description:
                description = f"{description} ({suffix})"
            elif records:
                base = re.sub(
                    r"\s*\((?:MSR|LSR)\)$",
                    "",
                    str(records[-1].get("description", "")),
                ).strip()
                description = f"{base} ({suffix})" if base else suffix
        description = description or name
        name = name or description
        if not values.get("description") and values.get("name"):
            name_claim = next(
                (claim for claim in claims if claim.get("field") == "name"), None
            )
            if name_claim is not None:
                description_claim = dict(name_claim)
                description_claim["field"] = "description"
                description_claim["value"] = description
                claims.append(description_claim)
        extra_fields = {
            header: _clean(_cell(row, column))
            for column, header in extra_columns
            if _clean(_cell(row, column))
        }
        for column, header in columns.get("units", []):
            raw_unit = _clean(_cell(row, column))
            if raw_unit:
                extra_fields.setdefault(header, raw_unit)
        record: dict[str, Any] = {
            **{field: value for field, value in values.items() if value},
            **_address_record_fields(parsed_address),
            "name": name,
            "description": description,
            "_claims": claims,
            "_source": source,
        }
        if extra_fields:
            record["_extra"] = extra_fields
        if trailing:
            record["notes"] = " | ".join(trailing)
        records.append(record)
    return {"records": records, "quarantined_records": quarantined}


def prepare_pdf_records(parsed: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt PDF row evidence to normalization fields and fold explicit word pairs."""

    result = dict(parsed)
    raw_records = parsed.get("records", ())
    if not isinstance(raw_records, Sequence) or isinstance(
        raw_records, (str, bytes, bytearray)
    ):
        return result
    prepared = [
        _prepare_pdf_record(record)
        for record in raw_records
        if isinstance(record, Mapping)
    ]
    logical: list[dict[str, Any]] = []
    index = 0
    while index < len(prepared):
        current = prepared[index]
        if index + 1 < len(prepared) and _explicit_word_pair(
            current, prepared[index + 1]
        ):
            following = prepared[index + 1]
            merged = dict(current)
            first = _source_register_number(current.get("source_register"))
            assert first is not None
            merged.update(
                {
                    "source_register": (
                        f"{current['source_register']}/{following['source_register']}"
                    ),
                    "word_count": 2,
                    "address": first,
                    "datatype": "uint32",
                    "byte_order": "ABCD",
                    "byte_order_confirmed": True,
                    "name": _PAIR_SUFFIX.sub(
                        "", str(current.get("name", ""))
                    ).strip(),
                    "description": _PAIR_SUFFIX.sub(
                        "", str(current.get("description", ""))
                    ).strip(),
                }
            )
            source = dict(merged.get("_source", {}))
            second_source = following.get("_source", {})
            if isinstance(second_source, Mapping):
                regions = [
                    str(value)
                    for value in (source.get("region"), second_source.get("region"))
                    if value
                ]
                source["region"] = "+".join(regions)
            merged["_source"] = source
            merged["logical_point_id"] = _source_point_id(merged)
            logical.append(merged)
            index += 2
            continue
        logical.append(current)
        index += 1
    result["records"] = logical
    return result


def _prepare_pdf_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    record = dict(raw)
    record["logical_point_id"] = _source_point_id(record)
    description = str(record.get("description") or "").strip()
    record.setdefault("name", description or None)
    source_format = str(record.get("format") or "").strip()
    if source_format:
        record["datatype"] = source_format
    word_count = str(record.get("word_count") or "").strip()
    if word_count.isdigit():
        record["word_count"] = int(word_count)
    units = str(record.get("units") or "").strip()
    if units:
        record["engineering_unit"] = units
    scale = str(record.get("scale") or "").strip()
    if scale:
        try:
            record["scale"] = float(scale)
        except ValueError:
            record["source_scale"] = scale
            record.pop("scale", None)
    return record


def _source_point_id(record: Mapping[str, Any]) -> str:
    source = record.get("_source", {})
    region = source.get("region") if isinstance(source, Mapping) else None
    if isinstance(region, str) and region:
        suffix = re.sub(r"[^A-Za-z0-9._-]+", "-", region).strip("-")
        if suffix:
            return f"source-{suffix}"
    address = _source_register_number(record.get("source_register"))
    return f"source-{address}" if address is not None else "source-row"


def _explicit_word_pair(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    if str(first.get("format", "")).casefold() != "ulong" or str(
        second.get("format", "")
    ).casefold() != "ulong":
        return False
    first_description = str(first.get("description") or "")
    second_description = str(second.get("description") or "")
    if not first_description.upper().endswith(
        "(MSR)"
    ) or not second_description.upper().endswith("(LSR)"):
        return False
    if _PAIR_SUFFIX.sub("", first_description).strip().casefold() != _PAIR_SUFFIX.sub(
        "", second_description
    ).strip().casefold():
        return False
    first_offset = _source_register_number(first.get("source_register"))
    second_offset = _source_register_number(second.get("source_register"))
    return isinstance(first_offset, int) and second_offset == first_offset + 1


def _source_register_number(value: Any) -> int | None:
    match = re.match(r"\d+", str(value or ""))
    return int(match.group()) if match else None


def _find_header(
    table: Sequence[Sequence[Any]],
) -> tuple[
    int,
    dict[str, list[tuple[int, str]]] | None,
    list[tuple[int, str]],
    bool,
]:
    """Find one joined header without rejecting unknown columns."""

    best: tuple[
        tuple[int, int, int],
        int,
        dict[str, list[tuple[int, str]]],
        list[tuple[int, str]],
        bool,
    ] | None = None
    limit = min(5, len(table))
    for start in range(limit):
        for end in range(start, min(start + 3, limit)):
            width = max((len(row) for row in table[start : end + 1]), default=0)
            columns: dict[str, list[tuple[int, str]]] = {}
            extras: list[tuple[int, str]] = []
            for column in range(width):
                header = " ".join(
                    value
                    for value in (
                        _clean(_cell(table[row_index], column))
                        for row_index in range(start, end + 1)
                    )
                    if value
                )
                if not header:
                    continue
                name = _HEADER_NAMES.get(_header_text(header))
                if name is None:
                    extras.append((column, header))
                else:
                    if name in {"protocol_offset", "display_address"}:
                        name = "address"
                    columns.setdefault(name, []).append((column, header))
            has_name = "name" in columns or "description" in columns
            if "address" not in columns or not has_name:
                continue
            confident = bool({"access", "format", "area"} & set(columns))
            score = (int(confident), len(columns), end - start)
            if best is None or score > best[0]:
                best = (score, end, columns, extras, confident)
    if best is None:
        return 0, None, [], False
    _score, end, columns, extras, confident = best
    return end, columns, extras, confident


def _resolve_cells(
    row: Sequence[Any], columns: Mapping[str, Sequence[tuple[int, str]]]
) -> tuple[dict[str, str], set[str]]:
    values: dict[str, str] = {}
    conflicts: set[str] = set()
    for field, candidates in columns.items():
        distinct = set(
            _clean_values(_cell(row, column) for column, _header in candidates)
        )
        if len(distinct) > 1:
            conflicts.add(field)
            continue
        values[field] = next(iter(distinct), "")
    return values, conflicts


def _parse_pdf_address(value: Any) -> dict[str, Any] | None:
    """Parse one source address without creating a protocol offset."""

    raw = _clean(value)
    match = _ADDRESS.fullmatch(raw)
    if match is None:
        return None
    first = _parse_address_component(match.group("first"))
    second_text = match.group("second")
    separator = match.group("separator")
    parsed: dict[str, Any] = {
        "raw": raw,
        "first": first,
        "second": _parse_address_component(second_text) if second_text else None,
        "separator": separator,
        "footnote_marker": bool(match.group("footnote")),
        "status": "single",
    }
    if first["status"] != "single" or (
        parsed["second"] is not None and parsed["second"]["status"] != "single"
    ):
        parsed.update(
            {
                "status": "ambiguous",
                "code": "pdf-grid-address-ambiguous",
            }
        )
        return parsed
    if separator == "-":
        parsed.update(
            {
                "status": "range",
                "code": "pdf-grid-address-range-unresolved",
                "convention": first["convention"],
                "area": first.get("area"),
                "number": first["number"],
                "end_number": parsed["second"]["number"],
            }
        )
        return parsed
    second = parsed["second"]
    if second is not None:
        compatible = (
            first["convention"] == second["convention"]
            and first.get("area") == second.get("area")
            and second["number"] == first["number"] + 1
        )
        if not compatible:
            parsed.update(
                {
                    "status": "pair",
                    "code": "pdf-grid-address-pair-unresolved",
                }
            )
            return parsed
    parsed.update(
        {
            "convention": first["convention"],
            "area": first.get("area"),
            "number": first["number"],
            "display_address": first.get("display_address"),
            "word_count": 2 if second is not None else 1,
        }
    )
    return parsed


def _parse_address_component(value: str) -> dict[str, Any]:
    text = _clean(value)
    if re.fullmatch(r"0[xX][0-9A-Fa-f]+", text):
        return {
            "raw": text,
            "status": "single",
            "convention": "protocol-offset",
            "area": None,
            "number": int(text, 16),
        }
    x_match = re.fullmatch(r"((?:[34][xX])+)(\d+)", text)
    if x_match is not None:
        prefixes = re.findall(r"[34]", x_match.group(1))
        if len(prefixes) != 1:
            return {
                "raw": text,
                "status": "ambiguous",
                "convention": "unknown",
                "area": None,
                "number": int(x_match.group(2)),
            }
        number_text = x_match.group(2)
        number = int(number_text)
        display = prefixes[0] + number_text.zfill(4)
        return {
            "raw": text,
            "status": "single",
            "convention": "modicon-reference",
            "area": _AREA_BY_PREFIX[prefixes[0]],
            "number": number,
            "display_address": display,
        }
    number = int(text)
    if len(text) in {5, 6} and text[0] in _AREA_BY_PREFIX and int(text[1:]) >= 1:
        return {
            "raw": text,
            "status": "single",
            "convention": "modicon-reference",
            "area": _AREA_BY_PREFIX[text[0]],
            "number": int(text[1:]),
            "display_address": text,
        }
    return {
        "raw": text,
        "status": "single",
        "convention": "unknown",
        "area": None,
        "number": number,
    }


def _parse_register_area(value: Any) -> tuple[str | None, str | None]:
    text = _header_text(value)
    if not text:
        return None, None
    prefixes = set(re.findall(r"(?<!\w)([34])x(?!\w)", text))
    if len(prefixes) == 1:
        return _AREA_BY_PREFIX[prefixes.pop()], None
    if len(prefixes) > 1 or re.search(r"(?:[34]x){2}", text):
        return None, "ambiguous"
    aliases = {
        "input": "input-register",
        "input register": "input-register",
        "input registers": "input-register",
        "input-register": "input-register",
        "holding": "holding-register",
        "holding register": "holding-register",
        "holding registers": "holding-register",
        "holding-register": "holding-register",
    }
    return aliases.get(text), None if text in aliases else "unrecognized"


def _address_with_area(parsed: Mapping[str, Any], area: str) -> dict[str, Any]:
    result = dict(parsed)
    raw_number = str(parsed["first"]["raw"])
    if not raw_number.isdigit() or area not in _PREFIX_BY_AREA:
        return result
    prefix = _PREFIX_BY_AREA[area]
    source_register = f"{prefix}x{raw_number}"
    display = prefix + raw_number.zfill(4)
    first = dict(parsed["first"])
    first.update(
        {
            "raw": source_register,
            "convention": "modicon-reference",
            "area": area,
            "display_address": display,
        }
    )
    result.update(
        {
            "raw": source_register,
            "first": first,
            "convention": "modicon-reference",
            "area": area,
            "display_address": display,
        }
    )
    return result


def _address_parse_evidence(parsed: Mapping[str, Any]) -> dict[str, Any]:
    evidence = {
        field: parsed.get(field)
        for field in (
            "raw",
            "status",
            "convention",
            "area",
            "number",
            "display_address",
            "word_count",
            "separator",
        )
        if parsed.get(field) is not None
    }
    for field in ("first", "second"):
        component = parsed.get(field)
        if isinstance(component, Mapping):
            evidence[field] = {
                key: component.get(key)
                for key in (
                    "raw",
                    "status",
                    "convention",
                    "area",
                    "number",
                    "display_address",
                )
                if component.get(key) is not None
            }
    if parsed.get("end_number") is not None:
        evidence["end_number"] = parsed["end_number"]
    return evidence


def _address_record_fields(parsed: Mapping[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "source_register": parsed["raw"],
        "address_convention": parsed["convention"],
        "address_number": parsed["number"],
        "word_count": parsed["word_count"],
        "footnote_marker": parsed["footnote_marker"],
        "address_parse": _address_parse_evidence(parsed),
    }
    if parsed.get("area") is not None:
        fields["area"] = parsed["area"]
    if parsed.get("display_address") is not None:
        fields["display_address"] = parsed["display_address"]
    else:
        fields["source_address"] = {
            "raw": parsed["first"]["raw"],
            "convention": parsed["convention"],
        }
    return fields


def _clean_values(values: Iterable[Any]) -> list[str]:
    cleaned = (_clean(value) for value in values)
    return [value for value in cleaned if value]


def _header_text(value: Any) -> str:
    return re.sub(r"\s+", " ", _clean(value).casefold()).strip().rstrip(":")


def _cell(row: Sequence[Any], index: int) -> Any:
    return row[index] if index < len(row) else None


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


__all__ = [
    "PdfTableExtractionError",
    "extract_pdf_table_evidence",
    "extract_pdf_table_rows",
    "parse_pdf_table",
    "parse_pdf_table_evidence",
    "prepare_pdf_records",
]


def _worker_main(argv: Sequence[str]) -> int:
    if len(argv) not in {3, 4} or argv[1] != "--worker":
        return 2
    pages = [int(value) for value in argv[3].split(",")] if len(argv) == 4 and argv[3] else None
    try:
        evidence = _extract_pdf_table_rows_in_process(Path(argv[2]), pages=pages)
        payload = json.dumps(evidence, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if len(payload) > _MAX_GRID_OUTPUT_BYTES:
            raise PdfTableExtractionError(
                f"grid extraction output exceeds {_MAX_GRID_OUTPUT_BYTES} bytes"
            )
    except (PdfTableExtractionError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the parent boundary
    raise SystemExit(_worker_main(sys.argv))
