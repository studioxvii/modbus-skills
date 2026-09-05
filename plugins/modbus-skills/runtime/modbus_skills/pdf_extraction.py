"""Bounded, evidence-preserving PDF register-map extraction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import chain
from fractions import Fraction
import os
import re
import selectors
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .artifacts import (
    ArtifactContractError,
    artifact_envelope,
    assert_artifact_envelope,
    stable_input_hash,
)
from .pdf_table_extraction import (
    PDF_HEADER_ALIASES,
    PdfTableExtractionError,
    _address_record_fields,
    _address_with_area,
    _parse_pdf_address,
    _parse_source_offset,
    _parse_register_area,
    extract_pdf_table_evidence,
)


class PdfExtractionError(ValueError):
    """Raised when supplied PDF evidence violates the public contract."""


_CONTINUATION_ROW = re.compile(
    r"(?im)^\s*(?:\d+|0x[0-9a-f]+|[34]x\d+)\b"
    r"(?=.*\b[A-Za-z][A-Za-z0-9_ /()-]*\b)"
    r".*(?:\b(?:bool(?:ean)?|bit|u?int(?:16|32|64)?|sint|word|dword|ulong|float(?:32|64)?|real|double|string|ascii|r/w|ro|rw|r)\b|\b0\s*[—–-]\s*No\b.*\b1\s*[—–-]\s*Yes\b)"
)


_HEADER_ALIASES = PDF_HEADER_ALIASES
_ADDRESS_FIELDS = ("address", "protocol_offset", "display_address", "source_offset")
_MATERIAL_FIELDS = frozenset(
    {"address", "protocol_offset", "display_address", "name", "area", "word_count", "datatype", "access", "engineering_offset"}
)
_PAGE_TOKEN = re.compile(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?")
_VERSION = re.compile(r"pdftotext version ([0-9]+(?:\.[0-9]+){1,3})", re.IGNORECASE)
_REQUIRED_FLAGS = ("-f", "-l", "-layout", "-bbox-layout", "-enc")
_MAX_PAGE = 100_000
_MAX_PAGE_SPAN = 256
_MAX_TOOL_OUTPUT_BYTES = 32_000_000
_MAX_CHUNK_SCAN_PAGES = 4_096
_MAX_CHUNK_RECORDS = 50_000
_MAX_OCR_EVIDENCE_BYTES = 10_000_000
_MAX_OCR_PAGE_TEXT_BYTES = 1_000_000
_GRID_RECOVERY_FINDING = {
    "code": "pdf-grid-recovery-used",
    "severity": "info",
    "blocking": False,
    "message": "Grid-aware table extraction supplied register-table structure alongside text parsing.",
}
_QUARANTINE_HOLD_MESSAGES = {
    "pdf-address-width-conflict": "Resolve the conflict between the explicit address pair and printed word count.",
    "pdf-prior-source-quarantine": "This source row already has unresolved parser evidence; later claims cannot release it.",
    "pdf-grid-column-ambiguous": "Resolve conflicting grid columns before these rows become map points.",
    "pdf-grid-type-unresolved": "Declare the datatype or access meaning for this address-and-name table.",
    "pdf-grid-register-area-ambiguous": "Declare one Modbus register area for these rows.",
    "pdf-grid-address-area-conflict": "Resolve the conflict between the address prefix and register-type column.",
    "pdf-grid-address-ambiguous": "Resolve the mixed or ambiguous display-address form.",
    "pdf-grid-address-range-unresolved": "Split or define the register range before it becomes a single map point.",
    "pdf-grid-address-pair-unresolved": "Confirm how the nonconsecutive or mixed address pair maps to one point.",
    "pdf-grid-bit-list-vs-register-unresolved": "Confirm whether this row is a register point or a bit-value list.",
}


def parse_page_range(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    if not value or any(character.isspace() for character in value):
        raise PdfExtractionError("--pages must use comma-separated page numbers or ranges without spaces")
    selected: set[int] = set()
    selected_count = 0
    for token in value.split(","):
        match = _PAGE_TOKEN.fullmatch(token)
        if match is None:
            raise PdfExtractionError("--pages must use syntax such as 42-48 or 42,43-48")
        first = int(match.group(1))
        last = int(match.group(2) or first)
        if last < first:
            raise PdfExtractionError("--pages ranges must increase")
        if last > _MAX_PAGE:
            raise PdfExtractionError(f"--pages must not exceed {_MAX_PAGE}")
        span = last - first + 1
        if span > _MAX_PAGE_SPAN:
            raise PdfExtractionError(f"--pages can select at most {_MAX_PAGE_SPAN} contiguous pages")
        selected_count += span
        selected.update(range(first, last + 1))
    if len(selected) != selected_count:
        raise PdfExtractionError("--pages must not contain duplicate or overlapping pages")
    first, last = min(selected), max(selected)
    if len(selected) != last - first + 1:
        raise PdfExtractionError("--pages must resolve to one contiguous range")
    if len(selected) > _MAX_PAGE_SPAN:
        raise PdfExtractionError(f"--pages can select at most {_MAX_PAGE_SPAN} contiguous pages")
    return first, last


def _header(line: str) -> list[str] | None:
    raw = re.split(r"\s{2,}", line.strip())
    if len(raw) < 2:
        return None
    names = [_layout_field(item, index) for index, item in enumerate(raw)]
    semantic = {name for name in names if not name.startswith("_extra:")}
    if not (set(_ADDRESS_FIELDS) & semantic) or not (
        {"name", "description"} & semantic
    ):
        return None
    return names


def _layout_field(value: str, index: int) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().casefold()).rstrip(":")
    name = _HEADER_ALIASES.get(normalized)
    if name == "format":
        return "datatype"
    if name == "units":
        return "engineering_unit"
    if name is not None:
        return name
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return f"_extra:{slug or f'column_{index + 1}'}"


def _layout_segments(line: str) -> list[tuple[int, str]]:
    segments: list[tuple[int, str]] = []
    cursor = 0
    for separator in re.finditer(r"\s{2,}", line.rstrip()):
        raw = line[cursor : separator.start()]
        value = raw.strip()
        if value:
            segments.append((cursor + len(raw) - len(raw.lstrip()), value))
        cursor = separator.end()
    raw = line[cursor:]
    value = raw.strip()
    if value:
        segments.append((cursor + len(raw) - len(raw.lstrip()), value))
    return segments


def _layout_header_at(
    lines: Sequence[str], start: int
) -> tuple[int, list[tuple[int, str, str]]] | None:
    best: tuple[tuple[int, int], int, list[tuple[int, str, str]]] | None = None
    token_columns = _tokenized_header(lines[start])
    token_semantic = {
        field for _x, field, _raw in token_columns if not field.startswith("_extra:")
    }
    if not token_semantic and lines[start].strip().casefold() not in {
        "protocol", "display", "holding", "read",
    }:
        # Do not absorb a running title into the next real header. Otherwise
        # "Example 8123 Modbus Notes" + "Name ... Offset" silently turns Name
        # into an unknown extra column and substitutes Description for it.
        return None
    if (set(_ADDRESS_FIELDS) & token_semantic) and (
        {"name", "description"} & token_semantic
    ):
        best = ((len(token_semantic), 0), start, token_columns)
    for end in range(start, min(start + 3, len(lines))):
        clusters: list[dict[str, Any]] = []
        for row_offset, line in enumerate(lines[start : end + 1]):
            for x, text in _layout_segments(line):
                nearby = [
                    cluster
                    for cluster in clusters
                    if row_offset not in cluster["rows"]
                    and abs(int(cluster["x"]) - x) <= 8
                ]
                if nearby:
                    cluster = min(nearby, key=lambda item: abs(int(item["x"]) - x))
                    cluster["parts"].append(text)
                    cluster["rows"].add(row_offset)
                    cluster["x"] = min(int(cluster["x"]), x)
                else:
                    clusters.append({"x": x, "parts": [text], "rows": {row_offset}})
        columns: list[tuple[int, str, str]] = []
        for index, cluster in enumerate(sorted(clusters, key=lambda item: int(item["x"]))):
            raw = " ".join(str(value) for value in cluster["parts"])
            columns.append((int(cluster["x"]), _layout_field(raw, index), raw))
        semantic = {field for _x, field, _raw in columns if not field.startswith("_extra:")}
        if not (set(_ADDRESS_FIELDS) & semantic) or not (
            {"name", "description"} & semantic
        ):
            continue
        score = (len(semantic), end - start)
        if best is None or score > best[0]:
            best = (score, end, columns)
    return None if best is None else (best[1], best[2])


def _tokenized_header(line: str) -> list[tuple[int, str, str]]:
    words = list(re.finditer(r"\S+", line))
    columns: list[tuple[int, str, str]] = []
    index = 0
    while index < len(words):
        matched: tuple[int, str, str] | None = None
        consumed = 1
        for width in range(min(4, len(words) - index), 0, -1):
            raw = " ".join(word.group() for word in words[index : index + width])
            field = _layout_field(raw, len(columns))
            if not field.startswith("_extra:"):
                matched = (words[index].start(), field, raw)
                consumed = width
                break
        if matched is None:
            raw = words[index].group()
            matched = (
                words[index].start(),
                _layout_field(raw, len(columns)),
                raw,
            )
        columns.append(matched)
        index += consumed
    return columns


def _claim(parser_id: str, field: str, value: str, locator: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "parser_id": parser_id,
        "field": field,
        "value": value,
        "source_locator": dict(locator),
    }


def _function_table_mask(lines: Sequence[str]) -> list[bool]:
    """Exclude explicitly labelled operation tables, not small register offsets.

    Scope is one page. A new register heading or address/name header ends the
    function-table context; no address convention is inferred from this filter.
    """
    masked: list[bool] = []
    in_function_table = False
    for index, line in enumerate(lines):
        label = re.sub(r"\s+", " ", line.strip()).casefold().rstrip(":")
        # Dotted section numbers are headings; a register row such as
        # "40001 Function code" is not a section title.
        label = re.sub(r"^\d+(?:\.\d+)+\.?\s+", "", label)
        function_heading = re.fullmatch(
            r"(?:(?:supported|modbus) )?function codes?", label
        ) is not None
        function_header = re.fullmatch(
            r"(?:function )?code (?:name action|description|name description)", label
        ) is not None
        if function_heading or function_header:
            in_function_table = True
        elif in_function_table and (
            re.fullmatch(
                r"(?:(?:holding|input|modbus) )?registers?(?: map| table)?", label
            )
            or _layout_header_at(lines, index) is not None
        ):
            in_function_table = False
        masked.append(in_function_table)
    return masked


def _non_register_context_mask(lines: Sequence[str]) -> list[bool]:
    """Exclude explicit option lists without assigning their values addresses.

    A baud settings label followed solely by multiple numeric choices (and
    optional navigation prompts) is menu evidence, not a headerless register.
    A real address/name header takes precedence: a name-first register row may
    legitimately include an address, minimum, maximum and default. Context is
    page-local and explicit serial-settings headings end stale register columns.
    No individual number, baud-related point name or address form is banned.
    """
    masked = _function_table_mask(lines)
    in_register_table = False
    in_serial_settings = False
    for index, line in enumerate(lines):
        if masked[index]:
            in_register_table = False
            in_serial_settings = False
            continue
        label = re.sub(r"\s+", " ", line.strip()).casefold().rstrip(":")
        label = re.sub(r"^\d+(?:\.\d+)+\.?\s+", "", label)
        if re.fullmatch(
            r"(?:serial(?: communication| port)?|communication) (?:settings|configuration)",
            label,
        ):
            masked[index] = True
            in_register_table = False
            in_serial_settings = True
            continue
        if in_register_table:
            continue
        if _layout_header_at(lines, index) is not None:
            in_register_table = True
            in_serial_settings = False
            continue
        options = re.fullmatch(
            r"baud(?: rate)? (settings?|options?|choices?)\s*:?\s*(.+)", label
        )
        if options is None:
            continue
        values = options.group(2)
        if options.group(1).startswith("setting") and not (
            in_serial_settings or re.search(r"\bpress\b", values)
        ):
            # A bare point name plus several numbers could be address/min/max;
            # require menu context rather than inventing those column roles.
            continue
        # Require a list, not a lone register address or an address/default pair.
        # Other words (datatype, units, descriptions) retain ordinary parsing.
        if len(re.findall(r"\b\d+\b", values)) >= 3 and not re.sub(
            r"\b(?:\d+|press)\b|[\s,;|/<>←→↑↓]+", "", values
        ):
            masked[index] = True
    return masked


def parse_layout_rows(
    text: str, *, first_page: int = 1, pages: set[int] | None = None, parser_id: str = "pdftotext-layout/v1"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for page_number, page in enumerate(text.split("\f"), start=first_page):
        if pages is not None and page_number not in pages:
            continue
        header: list[tuple[int, str, str]] | None = None
        header_end = -1
        lines = page.splitlines()
        function_mask = _non_register_context_mask(lines)
        header_lines = ["" if masked else line for line, masked in zip(lines, function_mask)]
        for line_index, line in enumerate(lines):
            line_number = line_index + 1
            if function_mask[line_index]:
                header = None
                header_end = -1
                continue
            if line_index <= header_end:
                continue
            candidate = _layout_header_at(header_lines, line_index)
            if candidate is not None:
                header_end, header = candidate
                continue
            if not line.strip():
                continue
            if header is None:
                headerless = _headerless_layout_row(
                    line,
                    page_number=page_number,
                    line_number=line_number,
                    parser_id=parser_id,
                )
                if headerless is not None:
                    records.append(headerless)
                elif _unresolved_tabular_row(line):
                    # A missed plausible table row is not proof that the page is
                    # exhausted. Preserve it as one localized source exception;
                    # do not guess column inheritance from the preceding page.
                    rejected.append(
                        {
                            "code": "pdf-row-structure-unresolved",
                            "page": page_number,
                            "line": line_number,
                            "parser_id": parser_id,
                            "_source": {
                                "format": "pdf",
                                "page": page_number,
                                "line": line_number,
                                "region": f"p{page_number}:l{line_number}",
                                "parser_id": parser_id,
                                "excerpt": line.strip()[:300],
                            },
                        }
                    )
                continue
            cells: dict[str, list[str]] = {field: [] for _x, field, _raw in header}
            segments = _layout_segments(line)
            if len(segments) < 2 and len(header) > 1:
                continue
            if len(segments) == len(header):
                for (_x, value), (_anchor, field, _raw) in zip(
                    segments, header, strict=True
                ):
                    cells[field].append(value)
            else:
                for x, value in segments:
                    _anchor, field, _raw = min(
                        header, key=lambda item: (abs(item[0] - x), -item[0])
                    )
                    cells[field].append(value)
            values = {
                field: " ".join(parts).strip()
                for field, parts in cells.items()
                if parts
            }
            address_field = next(
                (
                    field
                    for field in _ADDRESS_FIELDS
                    if values.get(field)
                ),
                None,
            )
            address = values.get(address_field, "") if address_field else ""
            parsed_address = (
                _parse_source_offset(address)
                if address_field == "source_offset"
                else _parse_pdf_address(address)
            )
            if parsed_address is None or parsed_address.get("status") != "single":
                rejected.append(
                    {
                        "code": (
                            str(parsed_address.get("code"))
                            if parsed_address is not None and parsed_address.get("code")
                            else "pdf-row-address-invalid"
                        ),
                        "page": page_number,
                        "line": line_number,
                        "parser_id": parser_id,
                    }
                )
                continue
            extra = {
                field.split(":", 1)[1]: value
                for field, value in values.items()
                if field.startswith("_extra:")
            }
            for _anchor, field, raw_header in header:
                if field == "engineering_unit" and values.get(field):
                    slug = re.sub(
                        r"[^a-z0-9]+", "_", raw_header.casefold()
                    ).strip("_")
                    extra.setdefault(slug or "unit", values[field])
            record = {
                field: value
                for field, value in values.items()
                if not field.startswith("_extra:")
                and field not in _ADDRESS_FIELDS
            }
            record.update(_address_record_fields(parsed_address, explicit_word_count=record.get("word_count")))
            if values.get("source_offset"):
                record["source_offset"] = values["source_offset"]
            if address_field == "protocol_offset":
                record.pop("display_address", None)
                record["address_convention"] = "protocol-offset"
                record["source_address"] = {
                    # A validated pair supplies one starting address and a
                    # width; the complete token remains in source_register,
                    # address_parse, and the original field claims.
                    "raw": parsed_address["first"]["raw"] if parsed_address.get("second") is not None else address,
                    "convention": "protocol-offset",
                }
            if record.get("name") in (None, ""):
                record["name"] = record.get("description")
            if record.get("description") in (None, ""):
                record["description"] = record.get("name")
            if not record.get("name"):
                continue
            if extra:
                record["_extra"] = extra
            locator = {"page": page_number, "line": line_number, "region": f"p{page_number}:l{line_number}"}
            record["_claims"] = [_claim(parser_id, field, str(value), locator) for field, value in record.items() if not field.startswith("_") and field != "code"]
            record["_source"] = {
                "format": "pdf",
                "page": page_number,
                "line": line_number,
                "region": locator["region"],
                "parser_id": parser_id,
                "method": "exact" if parser_id == "pdftotext-layout/v1" else "ocr-derived",
                "excerpt": line.strip()[:300],
            }
            records.append(record)
    return records, rejected


def _unresolved_tabular_row(line: str) -> bool:
    """Recognize residual multi-column row evidence, not arbitrary prose numbers."""
    segments = _layout_segments(line)
    if len(segments) < 3 or re.search(r"[.…·]{3,}", line):
        return False
    if re.match(r"^\s*\d+\s*:", line):
        return False
    if any(value.strip().casefold() == "reserved" for _x, value in segments):
        return False
    return _named_address_row_count([line]) > 0


def _headerless_tokens(line: str) -> list[str]:
    """Tokenize a layout or OCR line into candidate register-row cells."""

    segments = [value for _x, value in _layout_segments(line) if value not in {"•", "·", "●"}]
    if len(segments) >= 2:
        return segments
    # OCR evidence is often single-spaced with ``|`` column markers.
    pipe_parts = [part.strip() for part in re.split(r"\s*\|\s*", line) if part.strip()]
    if len(pipe_parts) >= 2:
        tokens: list[str] = []
        for part in pipe_parts:
            tokens.extend(token for token in re.split(r"\s+", part) if token)
        return [token for token in tokens if token not in {"•", "·", "●"}]
    return [token for token in re.split(r"\s+", line.strip()) if token and token not in {"•", "·", "●"}]


def _headerless_address_token(token: str) -> dict[str, Any] | None:
    """Parse one layout token that may embed Modicon/hex forms."""

    cleaned = token.strip().rstrip(":;,.")
    if not cleaned or cleaned in {"•", "·", "*", "-"}:
        return None
    # ``(register=414)`` virtual-register forms used by some HMI manuals.
    register_eq = re.fullmatch(r"\(register\s*=\s*(\d+)\)", cleaned, re.IGNORECASE)
    if register_eq is not None:
        return _parse_pdf_address(register_eq.group(1))
    # Require a clean address atom (optional nested ``40059(0x003A)`` form).
    atom = re.fullmatch(
        r"([0-4]\d{4,5}|0[xX][0-9A-Fa-f]+|(?:[34][xX])\d+|\d{3,6})(?:\([^)]*\))?",
        cleaned,
    )
    if atom is None:
        return None
    parsed = _parse_pdf_address(atom.group(1))
    if parsed is not None and parsed.get("status") == "single":
        return parsed
    return None


def _headerless_layout_row(
    line: str,
    *,
    page_number: int,
    line_number: int,
    parser_id: str,
) -> dict[str, Any] | None:
    """Accept obvious register rows even when no table header locked on the page.

    OEM manuals often list ``40056  16-bit int  Flow rate`` style lines without a
    formal header row. Discovery already flagged the page; recover the row.
    """

    # TOC / section leaders are not register rows.
    if re.search(r"[.…·]{3,}|\.{2,}\s*\d+\s*$", line):
        return None
    # Enum/bit legends like ``0: Off`` / ``1: Check mode active``.
    if re.match(r"^\s*\d+\s*:", line):
        return None
    segments = _headerless_tokens(line)
    if len(segments) < 2:
        return None
    address_indexes: list[int] = []
    parsed_by_index: dict[int, dict[str, Any]] = {}
    for index, token in enumerate(segments):
        parsed = _headerless_address_token(token)
        if parsed is not None and parsed.get("status") == "single":
            address_indexes.append(index)
            parsed_by_index[index] = parsed
    if not address_indexes:
        return None
    # Prefer Modicon/display forms over bare menu numbers when both appear.
    def _address_rank(parsed: Mapping[str, Any]) -> tuple[int, int]:
        convention = str(parsed.get("convention") or "")
        number = int(parsed.get("number") or 0)
        if convention == "modicon-reference":
            return (3, number)
        if convention == "protocol-offset":
            return (2, number)
        display = str(parsed.get("display_address") or "")
        if display.startswith(("3", "4")):
            return (3, number)
        return (1, number)

    address_index = max(
        address_indexes, key=lambda index: _address_rank(parsed_by_index[index])
    )
    parsed_address = parsed_by_index[address_index]
    names = [
        token
        for index, token in enumerate(segments)
        if index not in address_indexes
        and re.search(r"[A-Za-z]", token)
        and not re.fullmatch(
            r"(?:r/?w|ro|rw|r|w|[34]x|signed|unsigned|\d+)",
            token,
            re.IGNORECASE,
        )
    ]
    name_token = next(
        (
            token
            for token in names
            if not re.fullmatch(
                r"(?i)(?:u?int(?:16|32|64)?|float(?:32|64)?|bool(?:ean)?|bit|word|dword|ulong|real|string|ascii|signed|unsigned)",
                token,
            )
        ),
        names[0] if names else None,
    )
    if not names or name_token is None:
        return None
    if re.match(
        r"(?i)^(?:manual|appendix|page|table|contents|copyright|revision)\b",
        name_token,
    ):
        return None
    label_parts = [
        token
        for token in names
        if not re.fullmatch(
            r"(?i)(?:u?int(?:16|32|64)?|float(?:32|64)?|bool(?:ean)?|bit|word|dword|ulong|real|string|ascii|signed|unsigned)",
            token,
        )
    ]
    point_name = " ".join(label_parts) if label_parts else name_token
    datatype_token = next(
        (
            token
            for token in segments
            if re.search(
                r"(?i)\b(?:u?int(?:16|32|64)?|float(?:32|64)?|bool(?:ean)?|bit|word|dword|ulong|real|string|ascii|signed|unsigned)\b",
                token,
            )
        ),
        None,
    )
    if (
        datatype_token is None
        and address_index > 0
        and len(_layout_segments(line)) < 2
    ):
        # A single-spaced title such as "Example 8123 Modbus Notes" has a
        # number and a keyword, but no address-leading or tabular row evidence.
        return None
    # Require a datatype cue so enum/bit legend lines (``0 No / 1 Yes``) are not
    # promoted into register rows when no table header is locked.
    if datatype_token is None and len(segments) < 3:
        return None
    keyword_hit = re.search(
        r"(?i)\b(?:register|modbus|holding|input|coil|status|value|rate|temp|volt|amp|power|flow|energy|control|sync|load|mode|state)\b",
        " ".join(names),
    )
    if datatype_token is None and keyword_hit is None and len(address_indexes) < 2:
        return None
    # Titles like ``MODBUS REGISTER 40001`` are not point rows: every name token
    # is a generic table word with no concrete measurement label.
    generic_only = all(
        re.fullmatch(
            r"(?i)(?:modbus|register|registers|address|addresses|holding|input|coil|"
            r"table|map|parameter|parameters|data|type|area|access|name|description|"
            r"unit|units|scale|range|format|size|width|value|values|status)",
            token,
        )
        for token in label_parts
    )
    if datatype_token is None and generic_only and len(address_indexes) < 2:
        return None
    # Prefer an explicit nested Modicon display form when present on the line.
    for token in segments:
        nested = re.search(r"\b([0-4]\d{4,5})\(", token)
        if nested:
            nested_parsed = _parse_pdf_address(nested.group(1))
            if nested_parsed is not None and nested_parsed.get("status") == "single":
                parsed_address = nested_parsed
                break
    record = _address_record_fields(parsed_address)
    record["name"] = point_name
    record["description"] = " ".join(names)
    if datatype_token:
        record["datatype"] = datatype_token
        record["format"] = datatype_token
    locator = {
        "page": page_number,
        "line": line_number,
        "region": f"p{page_number}:l{line_number}",
    }
    record["_claims"] = [
        _claim(parser_id, field, str(value), locator)
        for field, value in record.items()
        if not field.startswith("_")
    ]
    record["_source"] = {
        "format": "pdf",
        "page": page_number,
        "line": line_number,
        "region": locator["region"],
        "parser_id": parser_id,
        "method": "exact" if parser_id == "pdftotext-layout/v1" else "ocr-derived",
        "excerpt": line.strip()[:300],
    }
    return record


def discover_register_pages(text: str, *, first_page: int = 1) -> list[int]:
    pages: list[int] = []
    previous_was_register = False
    for page_number, page in enumerate(text.split("\f"), start=first_page):
        lines = page.splitlines()
        lines = [
            "" if masked else line
            for line, masked in zip(lines, _non_register_context_mask(lines))
        ]
        has_header = any(
            _layout_header_at(lines, index) is not None for index in range(len(lines))
        )
        named_rows = _named_address_row_count(lines)
        has_register_signal = named_rows >= 1
        is_index_page = bool(
            re.search(r"(?im)^\s*(?:table of )?contents\s*$", page)
            or re.search(r"(?im)^\s*(?:register\s+)?overview\s*$", page)
        ) and named_rows == 0
        is_continuation = previous_was_register and not (
            has_header or has_register_signal
        ) and _CONTINUATION_ROW.search("\n".join(lines)) is not None
        is_register = not is_index_page and (
            has_header or has_register_signal or is_continuation
        )
        if is_register:
            pages.append(page_number)
        previous_was_register = is_register
    return pages


def _named_address_row_count(lines: Sequence[str]) -> int:
    count = 0
    for line in lines:
        segments = [value for _x, value in _layout_segments(line)]
        if len(segments) < 2:
            continue
        tokens = segments
        address_indexes = [
            index
            for index, token in enumerate(tokens)
            if (parsed := _parse_pdf_address(token.rstrip(":;,."))) is not None
            and parsed.get("status") == "single"
        ]
        if not address_indexes:
            continue
        names = [
            token
            for index, token in enumerate(tokens)
            if index not in address_indexes
            and re.search(r"[A-Za-z]", token)
            and not re.fullmatch(r"(?:r/?w|ro|rw|r|w|[34]x)", token, re.IGNORECASE)
        ]
        if names:
            count += 1
    return count


def parse_bbox_rows(xml_text: str, *, first_page: int = 1) -> list[dict[str, Any]]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise PdfExtractionError("pdftotext -bbox-layout returned malformed XML") from exc
    records: list[dict[str, Any]] = []
    page_nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "page"]
    for page_number, page in enumerate(page_nodes, start=first_page):
        lines: dict[float, list[tuple[float, float, float, str]]] = {}
        for word in page.iter():
            if word.tag.rsplit("}", 1)[-1] != "word" or not (word.text or "").strip():
                continue
            try:
                x_min = float(word.attrib["xMin"])
                x_max = float(word.attrib["xMax"])
                y_min = float(word.attrib["yMin"])
                y_max = float(word.attrib["yMax"])
            except (KeyError, ValueError) as exc:
                raise PdfExtractionError("pdftotext -bbox-layout word coordinates are invalid") from exc
            key = min(lines, key=lambda value: abs(value - y_min), default=y_min)
            if abs(key - y_min) > 2.5:
                key = y_min
            lines.setdefault(key, []).append((x_min, x_max, y_max, word.text.strip()))

        line_items = [(y_min, sorted(lines[y_min])) for y_min in sorted(lines)]
        function_mask = _non_register_context_mask(
            [" ".join(word[3] for word in words) for _y, words in line_items]
        )
        header_items = [
            (y, [] if masked else words)
            for (y, words), masked in zip(line_items, function_mask)
        ]
        columns: list[tuple[float, str, str]] | None = None
        header_end = -1
        for line_index, (y_min, words) in enumerate(line_items):
            if function_mask[line_index]:
                columns = None
                header_end = -1
                continue
            if line_index <= header_end:
                continue
            candidate = _bbox_header_at(header_items, line_index)
            if candidate is not None:
                header_end, columns = candidate
                continue
            if columns is None:
                continue
            cells: dict[str, list[str]] = {name: [] for _, name, _raw in columns}
            cell_regions: dict[str, list[float]] = {}
            for x_min, x_max, y_max, text in words:
                candidates = [(x, name) for x, name, _raw in columns if x <= x_min + 3]
                fallback = (columns[0][0], columns[0][1])
                _anchor, name = max(candidates or [fallback], key=lambda item: item[0])
                cells[name].append(text)
                bounds = cell_regions.setdefault(name, [x_min, y_min, x_max, y_max])
                bounds[0] = min(bounds[0], x_min)
                bounds[2] = max(bounds[2], x_max)
                bounds[3] = max(bounds[3], y_max)
            values = {name: " ".join(items).strip() for name, items in cells.items() if items}
            address_field = next(
                (
                    field
                    for field in _ADDRESS_FIELDS
                    if values.get(field)
                ),
                None,
            )
            address = values.get(address_field, "") if address_field else ""
            parsed_address = (
                _parse_source_offset(address)
                if address_field == "source_offset"
                else _parse_pdf_address(address)
            )
            if parsed_address is None or parsed_address.get("status") != "single":
                continue
            area, area_error = _parse_register_area(values.get("area"))
            if area_error is not None:
                continue
            if (
                area is not None
                and parsed_address.get("area") is not None
                and parsed_address.get("area") != area
            ):
                continue
            if area is not None and parsed_address.get("area") is None:
                parsed_address = _address_with_area(parsed_address, area)
            extra = {
                field.split(":", 1)[1]: value
                for field, value in values.items()
                if field.startswith("_extra:")
            }
            for _anchor, field, raw_header in columns:
                if field == "engineering_unit" and values.get(field):
                    slug = re.sub(
                        r"[^a-z0-9]+", "_", raw_header.casefold()
                    ).strip("_")
                    extra.setdefault(slug or "unit", values[field])
            record = {
                field: value
                for field, value in values.items()
                if not field.startswith("_extra:")
                and field not in _ADDRESS_FIELDS
            }
            record.update(_address_record_fields(parsed_address, explicit_word_count=record.get("word_count")))
            if values.get("source_offset"):
                record["source_offset"] = values["source_offset"]
            if address_field == "protocol_offset":
                record.pop("display_address", None)
                record["address_convention"] = "protocol-offset"
                record["source_address"] = {
                    "raw": parsed_address["first"]["raw"] if parsed_address.get("second") is not None else address,
                    "convention": "protocol-offset",
                }
            if record.get("name") in (None, ""):
                record["name"] = record.get("description")
            if record.get("description") in (None, ""):
                record["description"] = record.get("name")
            if not record.get("name"):
                continue
            if extra:
                record["_extra"] = extra
            region = f"p{page_number}:y{y_min:g}"
            record["_claims"] = [
                _claim(
                    "pdftotext-bbox-layout/v1",
                    field,
                    str(value),
                    {"page": page_number, "region": region, "bbox": cell_regions[field]},
                )
                for field, value in values.items()
                if not field.startswith("_extra:") and field in cell_regions
            ]
            if "description" not in values and values.get("name") and "name" in cell_regions:
                record["_claims"].append(
                    _claim(
                        "pdftotext-bbox-layout/v1",
                        "description",
                        str(values["name"]),
                        {
                            "page": page_number,
                            "region": region,
                            "bbox": cell_regions["name"],
                        },
                    )
                )
            record["_source"] = {
                "format": "pdf",
                "page": page_number,
                "region": region,
                "parser_id": "pdftotext-bbox-layout/v1",
                "method": "coordinate-derived",
                "excerpt": " | ".join(
                    str(record[name]) for _, name, _raw in columns if name in record
                )[:300],
            }
            records.append(record)
    return records


def _bbox_header_at(
    line_items: Sequence[tuple[float, list[tuple[float, float, float, str]]]],
    start: int,
) -> tuple[int, list[tuple[float, str, str]]] | None:
    first_line = " ".join(word[3] for word in line_items[start][1])
    if not any(
        not field.startswith("_extra:")
        for _x, field, _raw in _tokenized_header(first_line)
    ) and first_line.strip().casefold() not in {
        "protocol", "display", "holding", "read",
    }:
        return None
    best: tuple[tuple[int, int], int, list[tuple[float, str, str]]] | None = None
    for end in range(start, min(start + 3, len(line_items))):
        clusters: list[dict[str, Any]] = []
        for row_offset, (_y, words) in enumerate(line_items[start : end + 1]):
            phrases: list[tuple[float, float, str]] = []
            for x_min, x_max, _y_max, text in words:
                if phrases and x_min - phrases[-1][1] <= 6:
                    old_x, _old_max, old_text = phrases[-1]
                    phrases[-1] = (old_x, x_max, f"{old_text} {text}")
                else:
                    phrases.append((x_min, x_max, text))
            for x_min, _x_max, text in phrases:
                nearby = [
                    cluster
                    for cluster in clusters
                    if row_offset not in cluster["rows"]
                    and abs(float(cluster["x"]) - x_min) <= 12
                ]
                if nearby:
                    cluster = min(nearby, key=lambda item: abs(float(item["x"]) - x_min))
                    cluster["parts"].append(text)
                    cluster["rows"].add(row_offset)
                    cluster["x"] = min(float(cluster["x"]), x_min)
                else:
                    clusters.append(
                        {"x": x_min, "parts": [text], "rows": {row_offset}}
                    )
        columns = []
        for index, cluster in enumerate(sorted(clusters, key=lambda item: float(item["x"]))):
            raw = " ".join(str(value) for value in cluster["parts"])
            columns.append((float(cluster["x"]), _layout_field(raw, index), raw))
        semantic = {field for _x, field, _raw in columns if not field.startswith("_extra:")}
        if not (set(_ADDRESS_FIELDS) & semantic) or not (
            {"name", "description"} & semantic
        ):
            continue
        score = (len(semantic), end - start)
        if best is None or score > best[0]:
            best = (score, end, columns)
    return None if best is None else (best[1], best[2])


def _call(argv: list[str], *, timeout: int) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    assert process.stdout is not None and process.stderr is not None
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    selector = selectors.DefaultSelector()
    for stream in streams:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            for key, _events in selector.select(min(remaining, 0.25)):
                stream = key.fileobj
                target = streams[stream]
                chunk = os.read(stream.fileno(), min(65_536, _MAX_TOOL_OUTPUT_BYTES + 1 - len(target)))
                if not chunk:
                    selector.unregister(stream)
                    continue
                target.extend(chunk)
                if len(target) > _MAX_TOOL_OUTPUT_BYTES:
                    raise PdfExtractionError(f"pdftotext output exceeds {_MAX_TOOL_OUTPUT_BYTES} bytes")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(argv, timeout)
        returncode = process.wait(timeout=remaining)
    except (PdfExtractionError, subprocess.TimeoutExpired):
        process.kill()
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return subprocess.CompletedProcess(argv, returncode, bytes(streams[process.stdout]), bytes(streams[process.stderr]))


def _hold_result(
    path: Path,
    source: bytes,
    code: str,
    message: str,
    *,
    page_range: tuple[int, int] | None = None,
    capability: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    hold = {"code": code, "severity": "hold", "blocking": True, "message": message}
    return _envelope(path, source, [], [], [], [hold], page_range, capability=capability)


def _preflight(path: Path, source: bytes, page_range: tuple[int, int] | None) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
    executable = shutil.which("pdftotext")
    if executable is None:
        return None, None, _hold_result(path, source, "pdf-text-extractor-unavailable", "Provide a compatible pdftotext executable or bounded rights-safe OCR evidence; the runtime will not install OCR or PDF tools.", page_range=page_range)
    try:
        version_result = _call([executable, "-v"], timeout=5)
        help_result = _call([executable, "-h"], timeout=5)
    except (subprocess.TimeoutExpired, OSError, PdfExtractionError):
        return None, None, _hold_result(path, source, "pdf-text-extractor-incompatible", "pdftotext capability preflight did not complete within its bounded contract.", page_range=page_range)
    version_text = (version_result.stdout + version_result.stderr).decode("utf-8", errors="replace")
    help_text = (help_result.stdout + help_result.stderr).decode("utf-8", errors="replace")
    match = _VERSION.search(version_text)
    advertised_flags = set(re.findall(r"(?<!\S)-[A-Za-z][A-Za-z-]*(?=\s|$)", help_text))
    missing = [flag for flag in _REQUIRED_FLAGS if flag not in advertised_flags]
    if version_result.returncode != 0 or help_result.returncode != 0 or match is None or missing:
        detail = "missing " + ", ".join(missing) if missing else "version output was not recognized"
        return None, None, _hold_result(path, source, "pdf-text-extractor-incompatible", f"pdftotext does not meet the required capability contract: {detail}.", page_range=page_range)
    capability = {"name": "pdftotext", "version": match.group(1), "features": list(_REQUIRED_FLAGS)}
    return executable, capability, None


def _identity(record: Mapping[str, Any]) -> tuple[int, str, str]:
    source = record.get("_source", {})
    page = int(source.get("page", 0)) if isinstance(source, Mapping) else 0
    address = next(
        (record[field] for field in ("address", "protocol_offset", "display_address") if record.get(field) not in (None, "")),
        "",
    )
    if address in (None, ""):
        source_address = record.get("source_address")
        address = (
            source_address.get("raw")
            if isinstance(source_address, Mapping)
            else source_address
        )
    if address in (None, ""):
        address = record.get("source_register")
    if address is None:
        address = ""
    normalized_address = re.sub(r"\s+", "", str(address).casefold())
    name = re.sub(r"\W+", "", str(record.get("name", "")).casefold())
    return page, normalized_address, name


def _equivalent(field: str, left: Any, right: Any) -> bool:
    def normalized(value: Any) -> str:
        text = re.sub(r"\s+", " ", str(value).strip()).casefold()
        if field == "engineering_offset":
            try:
                return str(Fraction(text))
            except (ValueError, ZeroDivisionError):
                return text
        if field in {"address", "protocol_offset", "display_address", "word_count"}:
            try:
                return str(int(text, 10 if re.fullmatch(r"[+-]?\d+", text) else 0))
            except ValueError:
                return text
        return text.replace("_", "-")
    return normalized(left) == normalized(right)


def _name_identity(record: Mapping[str, Any]) -> tuple[int, str]:
    identity = _identity(record)
    return identity[0], identity[2]


def _address_identity(record: Mapping[str, Any]) -> tuple[int, str]:
    identity = _identity(record)
    return identity[0], identity[1]


def _physical_row_identity(record: Mapping[str, Any]) -> tuple[int, str] | None:
    source = record.get("_source", {})
    if not isinstance(source, Mapping):
        return None
    page, region = source.get("page"), source.get("region")
    if (
        isinstance(page, int)
        and page > 0
        and isinstance(region, str)
        and re.fullmatch(rf"p{page}:t\d+:r\d+", region)
    ):
        return page, region
    return None


def _merge_scope_compatible(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Do not associate claims across known area or physical table boundaries."""
    return _merge_scopes_compatible(_merge_scope(left), _merge_scope(right))


def _merge_scope(record: Mapping[str, Any]) -> tuple[Any, ...]:
    values = tuple(
        None if record.get(field) in (None, "") else
        re.sub(r"\s+", " ", str(record[field]).strip()).casefold().replace("_", "-")
        for field in ("area", "unit_id", "route_id")
    )
    source = record.get("_source", {})
    table = None
    if isinstance(source, Mapping):
        for field in ("table_index", "table", "table_id"):
            if source.get(field) not in (None, ""):
                table = str(source[field])
                break
        if table is None:
            match = re.search(r"(?:^|:)t(\d+)(?=:|$)", str(source.get("region", "")))
            table = match.group(1) if match else None
    return _physical_row_identity(record), values, table


def _merge_scopes_compatible(left: tuple[Any, ...], right: tuple[Any, ...]) -> bool:
    physical_row, values, table = left
    right_physical_row, right_values, right_table = right
    if physical_row is not None and right_physical_row is not None:
        # Two interpretations of the same located table row are not two
        # separate device points merely because their scope claims differ.
        # Associate them so the material conflicts below retain both claims.
        return physical_row == right_physical_row
    for a, b in zip(values, right_values, strict=True):
        if a is not None and b is not None and a != b:
            return False
    return table is None or right_table is None or table == right_table


def _source_row_locators(record: Mapping[str, Any]) -> set[tuple[int, str]]:
    locators = [record.get("_source")]
    locators.extend(claim.get("source_locator") for claim in record.get("_claims", ()) if isinstance(claim, Mapping))
    result = set()
    for source in locators:
        if not isinstance(source, Mapping):
            continue
        page, region = source.get("page"), source.get("region")
        if isinstance(page, int) and page > 0 and isinstance(region, str) and re.fullmatch(
            rf"p{page}:(?:t\d+:r\d+|l\d+|y\d+(?:\.\d+)?)", region
        ):
            result.add((page, region))
    return result


def _reconcile(
    strict: list[dict[str, Any]],
    coordinate: list[dict[str, Any]],
    *,
    quarantined_records: Sequence[dict[str, Any]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    # Earlier parser conflicts remain held even if a later parser agrees with
    # one interpretation. Include them in the same identity/scope uniqueness
    # checks rather than allowing their later claims to become new points.
    held_count = len(quarantined_records)
    strict = [*quarantined_records, *strict]
    accepted: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    unmatched_right = set(range(len(coordinate)))
    left_identities = [_identity(row) for row in strict]
    right_identities = [_identity(row) for row in coordinate]
    left_scopes = [_merge_scope(row) for row in strict]
    right_scopes = [_merge_scope(row) for row in coordinate]

    def index_keys(keys: Sequence[Any]) -> dict[Any, list[int]]:
        buckets: dict[Any, list[int]] = {}
        for index, key in enumerate(keys):
            buckets.setdefault(key, []).append(index)
        return buckets

    left_names = index_keys([(page, name) for page, _address, name in left_identities])
    left_addresses = index_keys([(page, address) for page, address, _name in left_identities])
    left_physical = index_keys([scope[0] for scope in left_scopes])
    right_exact = index_keys(right_identities)
    right_names = index_keys([(page, name) for page, _address, name in right_identities])
    right_addresses = index_keys([(page, address) for page, address, _name in right_identities])
    right_physical = index_keys([scope[0] for scope in right_scopes])
    for left_index, left in enumerate(strict):
        identity = left_identities[left_index]
        scope = left_scopes[left_index]

        def available(bucket: Sequence[int]) -> list[int]:
            return [index for index in bucket if index in unmatched_right
                    and _merge_scopes_compatible(scope, right_scopes[index])]

        physical_row = scope[0]
        match_index = None
        if (
            physical_row is not None
            and len(left_physical[physical_row]) == 1
            and len(right_physical.get(physical_row, ())) == 1
        ):
            same_physical_row = available(right_physical[physical_row])
            if same_physical_row:
                match_index = same_physical_row[0]
        if match_index is None:
            exact = available(right_exact.get(identity, ()))
            match_index = exact[0] if len(exact) == 1 else None
        address_key = identity[0], identity[1]
        if match_index is None and address_key[1]:
            same_address = available(right_addresses.get(address_key, ()))
            if len(same_address) == 1 and sum(
                _merge_scopes_compatible(scope, left_scopes[index])
                for index in left_addresses[address_key]
            ) == 1:
                match_index = same_address[0]
        name_key = identity[0], identity[2]
        if match_index is None and name_key[1]:
            same_name = available(right_names.get(name_key, ()))
            if len(same_name) == 1 and sum(
                _merge_scopes_compatible(scope, left_scopes[index])
                for index in left_names[name_key]
            ) == 1:
                match_index = same_name[0]
        if match_index is None:
            (quarantined if left_index < held_count else accepted).append(dict(left))
            continue
        unmatched_right.remove(match_index)
        right = coordinate[match_index]
        row_conflicts = []
        for field in sorted((_MATERIAL_FIELDS | {"unit_id", "route_id"}) & (set(left) & set(right))):
            if field in {"unit_id", "route_id"} and (
                left[field] in (None, "") or right[field] in (None, "")
            ):
                continue
            if not _equivalent(field, left[field], right[field]):
                row_conflicts.append({"field": field, "claims": [left[field], right[field]]})
        left_address, right_address = identity[1], right_identities[match_index][1]
        if (
            left_address and right_address
            and not _equivalent("address", left_address, right_address)
            and not any(item["field"] in _ADDRESS_FIELDS for item in row_conflicts)
        ):
            # Source-register/raw-address claims are material too, even when
            # neither parser has assigned a protocol/display address field.
            row_conflicts.append({"field": "address", "claims": [left_address, right_address]})
        merged = dict(left)
        for field, value in right.items():
            if not field.startswith("_") and field not in merged:
                merged[field] = value
        merged["_claims"] = [*left.get("_claims", []), *right.get("_claims", [])]
        if row_conflicts or left_index < held_count:
            quarantined.append(merged)
            if row_conflicts:
                conflicts.append({"identity": {"page": identity[0], "address": identity[1], "name": identity[2]}, "fields": row_conflicts, "source_regions": [left["_source"]["region"], right["_source"]["region"]]})
        else:
            accepted.append(merged)
    accepted.extend(dict(coordinate[index]) for index in sorted(unmatched_right))
    if held_count:
        held_locators: dict[tuple[int, str], list[tuple[Any, ...]]] = {}
        for row in quarantined_records:
            scope = _merge_scope(row)
            for locator in _source_row_locators(row):
                held_locators.setdefault(locator, []).append(scope)
        usable = []
        for row in accepted:
            scope = _merge_scope(row)
            shared = {
                locator for locator in _source_row_locators(row)
                if any(_merge_scopes_compatible(scope, held_scope) for held_scope in held_locators.get(locator, ()))
            }
            if shared:
                # Ambiguous semantic associations must not release a known
                # held physical/source row. Retain this claim separately;
                # do not pretend that ambiguous candidates were merged.
                quarantined.append({
                    **row,
                    "code": "pdf-prior-source-quarantine",
                    "_quarantine_source_locators": [
                        {"page": page, "region": region} for page, region in sorted(shared)
                    ],
                })
            else:
                usable.append(row)
        accepted = usable
    return accepted, quarantined, conflicts


def _ocr_rows(value: Mapping[str, Any], *, source_sha256: str, page_range: tuple[int, int] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    if page_range is None:
        raise PdfExtractionError("--ocr-evidence requires an explicit bounded --pages selection")
    try:
        assert_artifact_envelope(value)
    except ArtifactContractError as exc:
        raise PdfExtractionError(f"OCR evidence common envelope is invalid: {exc}") from exc
    if value.get("schema_version") != "modbus-ocr-evidence/v1" or value.get("artifact_type") != "modbus-ocr-evidence":
        raise PdfExtractionError("OCR evidence must use schema_version and artifact_type modbus-ocr-evidence/v1")
    if value["input_hashes"].get("source_pdf") != source_sha256 or str(value.get("source_sha256", "")).lower() != source_sha256:
        raise PdfExtractionError("OCR evidence source hash does not match the input PDF")
    tool = value.get("tool")
    if not isinstance(tool, Mapping) or not str(tool.get("name", "")).strip() or not str(tool.get("version", "")).strip():
        raise PdfExtractionError("OCR evidence tool must contain a non-empty name and version")
    if len(str(tool["name"])) > 100 or len(str(tool["version"])) > 100:
        raise PdfExtractionError("OCR evidence tool name and version must be at most 100 characters")
    raw_pages = value.get("pages")
    if not isinstance(raw_pages, Sequence) or isinstance(raw_pages, (str, bytes, bytearray)) or not raw_pages:
        raise PdfExtractionError("OCR evidence pages must be a non-empty array")
    expected = set(range(page_range[0], page_range[1] + 1))
    supplied: set[int] = set()
    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, raw_page in enumerate(raw_pages):
        if not isinstance(raw_page, Mapping) or isinstance(raw_page.get("page_index"), bool) or not isinstance(raw_page.get("page_index"), int):
            raise PdfExtractionError(f"OCR evidence pages[{index}].page_index must be an integer")
        page = int(raw_page["page_index"])
        if page not in expected or page in supplied:
            raise PdfExtractionError(f"OCR evidence page {page} is outside the selection or duplicated")
        supplied.add(page)
        text = raw_page.get("text")
        if not isinstance(text, str) or not text.strip() or "\f" in text:
            raise PdfExtractionError(f"OCR evidence page {page} text must be non-empty and contain no page separator")
        if len(text.encode()) > _MAX_OCR_PAGE_TEXT_BYTES:
            raise PdfExtractionError(f"OCR evidence page {page} text exceeds {_MAX_OCR_PAGE_TEXT_BYTES} bytes")
        printed_label = raw_page.get("printed_page_label")
        if printed_label is not None and len(str(printed_label)) > 100:
            raise PdfExtractionError(f"OCR evidence page {page} printed label must be at most 100 characters")
        page_records, page_rejected = parse_layout_rows(text, first_page=page, parser_id="external-ocr-layout/v1")
        for record in page_records:
            record["_source"]["printed_page_label"] = printed_label
            record["_source"]["ocr_tool"] = {"name": str(tool["name"]), "version": str(tool["version"])}
        records.extend(page_records)
        rejected.extend(page_rejected)
    if supplied != expected:
        raise PdfExtractionError("OCR evidence must contain each selected page exactly once; missing: " + ", ".join(map(str, sorted(expected - supplied))))
    return records, rejected, {"name": str(tool["name"]), "version": str(tool["version"])}


def _envelope(
    path: Path,
    source: bytes,
    records: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    holds: list[dict[str, Any]],
    page_range: tuple[int, int] | None,
    *,
    capability: Mapping[str, Any] | None = None,
    discovered_pages: Sequence[int] = (),
    quarantined: Sequence[Mapping[str, Any]] = (),
    ocr_tool: Mapping[str, str] | None = None,
    ocr_evidence: Mapping[str, Any] | None = None,
    discovery_complete: bool = True,
) -> dict[str, Any]:
    page_selection = {"first_page": page_range[0], "last_page": page_range[1]} if page_range else None
    width_conflicts = [row for row in records if row.get("code") == "pdf-address-width-conflict"]
    if width_conflicts:
        records = [row for row in records if row.get("code") != "pdf-address-width-conflict"]
        quarantined = [*quarantined, *width_conflicts]
    coverage = _source_coverage(
        records, rejected, quarantined, discovered_pages, discovery_complete
    )
    effective_holds = list(holds)
    quarantine_counts: dict[str, int] = {}
    for row in quarantined:
        code = str(row.get("code", "")) if isinstance(row, Mapping) else ""
        if code in _QUARANTINE_HOLD_MESSAGES:
            quarantine_counts[code] = quarantine_counts.get(code, 0) + 1
    existing_codes = {str(hold.get("code", "")) for hold in effective_holds}
    for code in sorted(quarantine_counts):
        if code in existing_codes:
            continue
        effective_holds.append(
            {
                "code": code,
                "severity": "hold",
                "blocking": True,
                "message": _QUARANTINE_HOLD_MESSAGES[code],
                "affected_count": quarantine_counts[code],
            }
        )
    if records and coverage["status"] != "complete" and not any(
        str(hold.get("code", "")) == "pdf-source-coverage-unproven"
        for hold in effective_holds
    ):
        effective_holds.append(
            {
                "code": "pdf-source-coverage-unproven",
                "severity": "hold",
                "blocking": True,
                "message": "The extracted rows are useful, but independent parsers did not establish complete source coverage. Review the grouped source exception or supply a bounded page range.",
            }
        )
    return artifact_envelope(
        {
            "status": "held" if effective_holds else "candidate",
            "source": {"filename": path.name, "sha256": stable_input_hash(source)},
            "page_selection": page_selection,
            "discovered_register_pages": list(discovered_pages),
            "source_coverage": coverage,
            "extractor": dict(capability) if capability else None,
            "ocr_tool": dict(ocr_tool) if ocr_tool else None,
            "review_strategy": {"mode": "batch-exceptions", "record_count": coverage["accepted_row_count"], "rejected_row_count": coverage["rejected_row_count"], "quarantined_record_count": coverage["quarantined_row_count"], "page_selection": page_selection},
            "records": records,
            "quarantined_records": list(quarantined),
            "rejected_rows": rejected,
            "warnings": [],
        },
        schema_version="modbus-pdf-extraction/v1",
        inputs={"source": source, "ocr_evidence": ocr_evidence, "page_selection": page_selection},
        findings=findings,
        holds=effective_holds,
    )


def _source_coverage(
    records: Sequence[Mapping[str, Any]],
    rejected: Sequence[Mapping[str, Any]],
    quarantined: Sequence[Mapping[str, Any]],
    discovered_pages: Sequence[int],
    discovery_complete: bool,
) -> dict[str, Any]:
    parser_sets: list[set[str]] = []
    regions: set[str] = set()
    covered_pages: set[int] = set()
    for record in records:
        source = record.get("_source", {})
        parsers = {
            str(claim.get("parser_id"))
            for claim in record.get("_claims", ())
            if isinstance(claim, Mapping) and claim.get("parser_id")
        }
        if isinstance(source, Mapping):
            if source.get("parser_id"):
                parsers.add(str(source["parser_id"]))
            if source.get("region"):
                regions.add(str(source["region"]))
            if isinstance(source.get("page"), int):
                covered_pages.add(int(source["page"]))
        parser_sets.append(parsers)
    for row in chain(rejected, quarantined):
        source = row.get("_source", {}) if isinstance(row, Mapping) else {}
        page = row.get("page") if isinstance(row, Mapping) else None
        if isinstance(source, Mapping) and isinstance(source.get("page"), int):
            covered_pages.add(int(source["page"]))
        elif isinstance(page, int):
            covered_pages.add(page)
    detected = sorted(set(discovered_pages))
    complete = (
        discovery_complete
        and bool(records)
        and not rejected
        and not quarantined
        and set(detected).issubset(covered_pages)
    )
    independently_supported = sum(len(parsers) >= 2 for parsers in parser_sets)
    return {
        "status": "complete" if complete else "unknown",
        "scope": "detected-pages-and-recognized-row-candidates",
        "full_source_fidelity": "not-asserted",
        "accepted_row_count": len(records),
        "rejected_row_count": len(rejected),
        "quarantined_row_count": len(quarantined),
        "detected_pages": detected,
        "covered_pages": sorted(covered_pages),
        "detected_regions": sorted(regions),
        "basis": "bounded-discovery" if complete else "incomplete-or-conflicting-discovery",
        "discovery_complete": discovery_complete,
        "independent_parser_row_count": independently_supported,
        "single_parser_row_count": len(parser_sets) - independently_supported,
    }


def extract_pdf(
    path: Path,
    source: bytes,
    *,
    page_range: tuple[int, int] | None = None,
    ocr_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract a bounded map while retaining independently sourced claims."""

    source_sha256 = stable_input_hash(source)
    if ocr_evidence is not None:
        records, rejected, tool = _ocr_rows(ocr_evidence, source_sha256=source_sha256, page_range=page_range)
        code = "pdf-ocr-human-review-required" if records else "pdf-ocr-structured-rows-unavailable"
        message = "Confirm or correct this bounded OCR extraction as one grouped decision; page-by-page approval is not required." if records else "OCR evidence contained no strict structured register rows."
        hold = {"code": code, "severity": "hold", "blocking": True, "message": message}
        return _envelope(path, source, records, rejected, [], [hold], page_range, discovered_pages=range(page_range[0], page_range[1] + 1), ocr_tool=tool, ocr_evidence=ocr_evidence)

    executable, capability, failure = _preflight(path, source, page_range)
    if failure is not None:
        return _recover_grid_or(
            path,
            source,
            page_range=page_range,
            fallback=failure,
        )
    assert executable is not None and capability is not None
    base = [executable, "-enc", "UTF-8"]
    if page_range:
        base.extend(["-f", str(page_range[0]), "-l", str(page_range[1])])
    try:
        layout = _call([*base, "-layout", str(path), "-"], timeout=60)
    except subprocess.TimeoutExpired:
        fallback = _hold_result(path, source, "pdf-text-extraction-timeout", "PDF text extraction exceeded the 60 second limit.", page_range=page_range, capability=capability)
        return _recover_grid_or(path, source, page_range=page_range, fallback=fallback, capability=capability)
    except PdfExtractionError as exc:
        if page_range is None:
            return _extract_large_pdf_in_chunks(
                path, source, executable=executable, capability=capability
            )
        fallback = _hold_result(path, source, "pdf-text-extraction-resource-limit", str(exc), page_range=page_range, capability=capability)
        return _recover_grid_or(path, source, page_range=page_range, fallback=fallback, capability=capability)
    except OSError as exc:
        fallback = _hold_result(path, source, "pdf-text-extraction-resource-limit", str(exc), page_range=page_range, capability=capability)
        return _recover_grid_or(path, source, page_range=page_range, fallback=fallback, capability=capability)
    if layout.returncode != 0:
        fallback = _hold_result(path, source, "pdf-text-extraction-failed", "pdftotext could not extract this document.", page_range=page_range, capability=capability)
        return _recover_grid_or(path, source, page_range=page_range, fallback=fallback, capability=capability)
    text = layout.stdout.decode("utf-8", errors="replace")
    if not text.strip():
        fallback = _hold_result(path, source, "pdf-ocr-required", "The selected pages contain no extractable text. Supply one bounded rights-safe modbus-ocr-evidence/v1 artifact.", page_range=page_range, capability=capability)
        return _recover_grid_or(path, source, page_range=page_range, fallback=fallback, capability=capability)
    first_page = page_range[0] if page_range else 1
    discovered = list(range(page_range[0], page_range[1] + 1)) if page_range else discover_register_pages(text, first_page=first_page)
    if not discovered:
        try:
            grid, grid_quarantined, discovered = _recover_grid_rows(path)
        except PdfTableExtractionError as exc:
            return _hold_result(path, source, "pdf-register-pages-unavailable", f"No likely register pages were discovered and grid recovery failed: {exc}.", page_range=page_range, capability=capability)
        return _envelope(
            path,
            source,
            grid,
            [],
            [_GRID_RECOVERY_FINDING],
            [],
            page_range,
            capability=capability,
            discovered_pages=discovered,
            quarantined=grid_quarantined,
        )
    page_filter = set(discovered)
    strict, rejected = parse_layout_rows(text, first_page=first_page, pages=page_filter)
    findings: list[dict[str, Any]] = []
    if not strict:
        findings.append({"code": "pdf-strict-parser-no-rows", "severity": "info", "blocking": False, "message": "Strict layout parsing found no rows; coordinate parsing was attempted automatically."})

    bbox_base = [executable, "-enc", "UTF-8", "-f", str(min(discovered)), "-l", str(max(discovered))]
    try:
        bbox_result = _call([*bbox_base, "-bbox-layout", str(path), "-"], timeout=60)
    except subprocess.TimeoutExpired:
        fallback = _hold_result(path, source, "pdf-coordinate-extraction-timeout", "Coordinate extraction exceeded the 60 second limit.", page_range=page_range, capability=capability)
        return _recover_grid_or(
            path,
            source,
            page_range=page_range,
            fallback=fallback,
            capability=capability,
            keep_rows=strict,
            keep_rejected=rejected,
        )
    except (OSError, PdfExtractionError) as exc:
        fallback = _hold_result(path, source, "pdf-coordinate-extraction-resource-limit", str(exc), page_range=page_range, capability=capability)
        return _recover_grid_or(
            path,
            source,
            page_range=page_range,
            fallback=fallback,
            capability=capability,
            keep_rows=strict,
            keep_rejected=rejected,
        )
    if bbox_result.returncode != 0:
        fallback = _hold_result(path, source, "pdf-coordinate-extraction-failed", "pdftotext coordinate extraction failed.", page_range=page_range, capability=capability)
        return _recover_grid_or(
            path,
            source,
            page_range=page_range,
            fallback=fallback,
            capability=capability,
            keep_rows=strict,
            keep_rejected=rejected,
        )
    try:
        coordinate = parse_bbox_rows(bbox_result.stdout.decode("utf-8", errors="replace"), first_page=min(discovered))
    except PdfExtractionError:
        fallback = _hold_result(path, source, "pdf-coordinate-output-malformed", "pdftotext coordinate output was malformed and could not be reconciled safely.", page_range=page_range, capability=capability)
        return _recover_grid_or(
            path,
            source,
            page_range=page_range,
            fallback=fallback,
            capability=capability,
            keep_rows=strict,
            keep_rejected=rejected,
        )
    coordinate = [record for record in coordinate if record["_source"]["page"] in page_filter]
    if not coordinate and not strict:
        try:
            grid, grid_quarantined, _grid_pages = _recover_grid_rows(path, pages=discovered)
        except PdfTableExtractionError as exc:
            return _hold_result(path, source, "pdf-structured-rows-unavailable", f"Text and coordinate parsing produced no rows, and grid recovery failed: {exc}.", page_range=page_range, capability=capability)
        findings.append(_GRID_RECOVERY_FINDING)
        return _envelope(path, source, grid, rejected, findings, [], page_range, capability=capability, discovered_pages=discovered, quarantined=grid_quarantined)
    records, quarantined, conflicts = _reconcile(strict, coordinate)
    try:
        grid, grid_source_quarantined, _grid_pages = _recover_grid_rows(path, pages=discovered)
    except PdfTableExtractionError:
        grid = []
        grid_source_quarantined = []
    quarantined.extend(grid_source_quarantined)
    if grid:
        records, quarantined, grid_conflicts = _reconcile(records, grid, quarantined_records=quarantined)
        conflicts.extend(grid_conflicts)
        findings.append(_GRID_RECOVERY_FINDING)
    holds: list[dict[str, Any]] = []
    if conflicts:
        holds.append({
            "code": "pdf-material-claim-conflict",
            "severity": "hold",
            "blocking": True,
            "message": "Resolve the listed material field conflicts as one localized decision; unaffected rows remain available.",
            "conflicts": conflicts,
        })
    return _envelope(path, source, records, rejected, findings, holds, page_range, capability=capability, discovered_pages=discovered, quarantined=quarantined)


def _extract_large_pdf_in_chunks(
    path: Path,
    source: bytes,
    *,
    executable: str,
    capability: Mapping[str, Any],
) -> dict[str, Any]:
    """Recover an oversized manual without requiring manual page selection."""

    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    discovered_pages: list[int] = []
    deadline = time.monotonic() + 180
    scan_complete = False
    for first_page in range(1, _MAX_CHUNK_SCAN_PAGES + 1, _MAX_PAGE_SPAN):
        last_page = first_page + _MAX_PAGE_SPAN - 1
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            break
        base = [
            executable,
            "-enc",
            "UTF-8",
            "-f",
            str(first_page),
            "-l",
            str(last_page),
        ]
        try:
            layout = _call(
                [*base, "-layout", str(path), "-"],
                timeout=min(30, remaining),
            )
        except (OSError, PdfExtractionError, subprocess.TimeoutExpired):
            break
        if layout.returncode != 0 or not layout.stdout.strip():
            break
        text = layout.stdout.decode("utf-8", errors="replace")
        page_count = _extracted_page_count(text)
        chunk_pages = discover_register_pages(text, first_page=first_page)
        discovered_pages.extend(chunk_pages)
        if chunk_pages:
            page_filter = set(chunk_pages)
            strict, chunk_rejected = parse_layout_rows(
                text, first_page=first_page, pages=page_filter
            )
            rejected.extend(chunk_rejected)
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                break
            try:
                bbox_result = _call(
                    [
                        executable,
                        "-enc",
                        "UTF-8",
                        "-f",
                        str(min(chunk_pages)),
                        "-l",
                        str(max(chunk_pages)),
                        "-bbox-layout",
                        str(path),
                        "-",
                    ],
                    timeout=min(30, remaining),
                )
                coordinate = (
                    parse_bbox_rows(
                        bbox_result.stdout.decode("utf-8", errors="replace"),
                        first_page=min(chunk_pages),
                    )
                    if bbox_result.returncode == 0
                    else []
                )
            except (OSError, PdfExtractionError, subprocess.TimeoutExpired):
                coordinate = []
            coordinate = [
                record
                for record in coordinate
                if record.get("_source", {}).get("page") in page_filter
            ]
            chunk_records, chunk_quarantined, chunk_conflicts = _reconcile(
                strict, coordinate
            )
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                break
            try:
                grid, grid_source_quarantined, _ = _recover_grid_rows(
                    path,
                    pages=chunk_pages,
                    timeout_seconds=min(60, remaining),
                )
            except PdfTableExtractionError:
                grid = []
                grid_source_quarantined = []
            chunk_quarantined.extend(grid_source_quarantined)
            if grid:
                chunk_records, chunk_quarantined, grid_conflicts = _reconcile(
                    chunk_records, grid, quarantined_records=chunk_quarantined
                )
                chunk_conflicts.extend(grid_conflicts)
            records.extend(chunk_records)
            quarantined.extend(chunk_quarantined)
            conflicts.extend(chunk_conflicts)
            if (
                len(records) + len(rejected) + len(quarantined) + len(conflicts)
                > _MAX_CHUNK_RECORDS
            ):
                remaining = _MAX_CHUNK_RECORDS
                records = records[:remaining]
                remaining -= len(records)
                rejected = rejected[:remaining]
                remaining -= len(rejected)
                quarantined = quarantined[:remaining]
                remaining -= len(quarantined)
                conflicts = conflicts[:remaining]
                break
        if page_count < _MAX_PAGE_SPAN:
            scan_complete = True
            break
    holds: list[dict[str, Any]] = []
    if not scan_complete:
        holds.append(
            {
                "code": "pdf-chunk-scan-limit",
                "severity": "hold",
                "blocking": True,
                "message": f"Automatic discovery stopped after {_MAX_CHUNK_SCAN_PAGES} pages or 180 seconds; extracted rows remain available as one grouped exception.",
            }
        )
    if conflicts:
        holds.append(
            {
                "code": "pdf-material-claim-conflict",
                "severity": "hold",
                "blocking": True,
                "message": "Resolve the listed material field conflicts as one localized decision; unaffected rows remain available.",
                "conflicts": conflicts,
            }
        )
    if not records:
        holds.append(
            {
                "code": "pdf-register-pages-unavailable",
                "severity": "hold",
                "blocking": True,
                "message": "Bounded chunk discovery found no independently verified register rows.",
            }
        )
    return _envelope(
        path,
        source,
        records,
        rejected,
        [{
            "code": "pdf-bounded-chunk-discovery-used",
            "severity": "info",
            "blocking": False,
            "message": "The manual exceeded one-pass extraction limits, so register pages were discovered in bounded chunks.",
        }],
        holds,
        None,
        capability=capability,
        discovered_pages=sorted(set(discovered_pages)),
        quarantined=quarantined,
        discovery_complete=scan_complete,
    )


def _extracted_page_count(text: str) -> int:
    return max(1, text.count("\f") + (0 if text.endswith("\f") else 1))


def _recover_grid_rows(
    path: Path,
    *,
    pages: Sequence[int] | None = None,
    timeout_seconds: int = 60,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[int]]:
    evidence = extract_pdf_table_evidence(
        path, pages=pages, timeout_seconds=timeout_seconds
    )
    records = evidence["records"]
    quarantined = evidence["quarantined_records"]
    if not records and not quarantined:
        raise PdfTableExtractionError("table geometry contained no register rows")
    discovered = sorted(
        {
            int(record["_source"]["page"])
            for record in chain(records, quarantined)
        }
    )
    return records, quarantined, discovered


def _recover_grid_or(
    path: Path,
    source: bytes,
    *,
    page_range: tuple[int, int] | None,
    fallback: dict[str, Any],
    capability: Mapping[str, Any] | None = None,
    keep_rows: Sequence[Mapping[str, Any]] | None = None,
    keep_rejected: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Prefer grid recovery; if that fails, keep any already-parsed layout rows.

    Coordinate/bbox failures used to discard successful strict layout rows by
    falling through to an empty hold. Preserve those rows so manuals that parse
    under ``-layout`` still produce candidates when ``-bbox-layout`` blows up.
    """

    pages = (
        list(range(page_range[0], page_range[1] + 1))
        if page_range is not None
        else None
    )
    kept_pages = sorted(
        {
            int(record["_source"]["page"])
            for record in (keep_rows or ())
            if isinstance(record.get("_source"), Mapping)
            and isinstance(record["_source"].get("page"), int)
        }
    ) or None
    keep_finding = {
        "code": "pdf-coordinate-fallback-kept-layout",
        "severity": "info",
        "blocking": False,
        "message": (
            "Coordinate/grid recovery failed; kept strict layout "
            "rows that already parsed successfully."
        ),
    }
    try:
        grid, quarantined, discovered = _recover_grid_rows(path, pages=pages)
    except PdfTableExtractionError:
        if keep_rows:
            return _envelope(
                path,
                source,
                list(keep_rows),
                list(keep_rejected or []),
                [keep_finding],
                [],
                page_range,
                capability=capability,
                discovered_pages=kept_pages,
            )
        return fallback
    # Empty accepted grid with only quarantines must not discard layout rows.
    if not grid and keep_rows:
        return _envelope(
            path,
            source,
            list(keep_rows),
            list(keep_rejected or []),
            [keep_finding, _GRID_RECOVERY_FINDING],
            [],
            page_range,
            capability=capability,
            discovered_pages=kept_pages or discovered,
            quarantined=quarantined,
        )
    return _envelope(
        path,
        source,
        grid,
        [],
        [_GRID_RECOVERY_FINDING],
        [],
        page_range,
        capability=capability,
        discovered_pages=discovered,
        quarantined=quarantined,
    )


__all__ = ["PdfExtractionError", "discover_register_pages", "extract_pdf", "parse_bbox_rows", "parse_layout_rows", "parse_page_range"]
