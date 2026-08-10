"""Bounded, evidence-preserving PDF register-map extraction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import chain
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
from .pdf_table_extraction import PdfTableExtractionError, extract_pdf_table_evidence


class PdfExtractionError(ValueError):
    """Raised when supplied PDF evidence violates the public contract."""


_CONTINUATION_ROW = re.compile(
    r"(?im)^\s*(?:\d+|0x[0-9a-f]+)\b.*\b(?:bool(?:ean)?|bit|u?int(?:16|32|64)?|sint|word|dword|ulong|float(?:32|64)?|real|double|string|ascii|r/w|ro|rw|r)\b"
)


_HEADER_ALIASES = {
    "address": "address",
    "register": "address",
    "register address": "address",
    "protocol offset": "protocol_offset",
    "display address": "display_address",
    "name": "name",
    "tag": "name",
    "description": "description",
    "data type": "datatype",
    "datatype": "datatype",
    "area": "area",
    "access": "access",
    "unit id": "unit_id",
    "word count": "word_count",
    "width": "word_count",
    "byte order": "byte_order",
}
_MATERIAL_FIELDS = frozenset(
    {"address", "protocol_offset", "display_address", "name", "area", "word_count", "datatype", "access"}
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
    names = [_HEADER_ALIASES.get(re.sub(r"\s+", " ", item.strip().lower())) for item in raw]
    if None in names or not ({"address", "protocol_offset", "display_address"} & set(names)):
        return None
    return [str(name) for name in names]


def _claim(parser_id: str, field: str, value: str, locator: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "parser_id": parser_id,
        "field": field,
        "value": value,
        "source_locator": dict(locator),
    }


def parse_layout_rows(
    text: str, *, first_page: int = 1, pages: set[int] | None = None, parser_id: str = "pdftotext-layout/v1"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for page_number, page in enumerate(text.split("\f"), start=first_page):
        if pages is not None and page_number not in pages:
            continue
        header: list[str] | None = None
        for line_number, line in enumerate(page.splitlines(), start=1):
            candidate = _header(line)
            if candidate is not None:
                header = candidate
                continue
            if header is None or not line.strip():
                continue
            parts = re.split(r"\s{2,}", line.strip())
            if len(parts) != len(header):
                continue
            record = dict(zip(header, (value.strip() for value in parts), strict=True))
            address = record.get("address", record.get("protocol_offset", record.get("display_address")))
            if not re.fullmatch(r"(?:0[xX][0-9A-Fa-f]+|\d+)", str(address or "")):
                rejected.append({"code": "pdf-row-address-invalid", "page": page_number, "line": line_number, "parser_id": parser_id})
                continue
            locator = {"page": page_number, "line": line_number, "region": f"p{page_number}:l{line_number}"}
            record["_claims"] = [_claim(parser_id, field, str(value), locator) for field, value in record.items() if not field.startswith("_")]
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


def discover_register_pages(text: str, *, first_page: int = 1) -> list[int]:
    pages: list[int] = []
    previous_was_register = False
    for page_number, page in enumerate(text.split("\f"), start=first_page):
        has_header = any(_header(line) is not None for line in page.splitlines())
        has_register_signal = bool(re.search(r"\b(?:modbus|register|address)\b", page, re.IGNORECASE)) and bool(
            re.search(r"\b(?:[1-4][0-9]{4,5}|0[xX][0-9A-Fa-f]+)\b", page)
        )
        is_continuation = previous_was_register and not (
            has_header or has_register_signal
        ) and _CONTINUATION_ROW.search(page) is not None
        is_register = has_header or has_register_signal or is_continuation
        if is_register:
            pages.append(page_number)
        previous_was_register = is_register
    return pages


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

        columns: list[tuple[float, str]] | None = None
        for y_min in sorted(lines):
            words = sorted(lines[y_min])
            header_columns: list[tuple[float, str | None]] = []
            index = 0
            while index < len(words):
                matched: tuple[float, str] | None = None
                consumed = 1
                for width in range(min(3, len(words) - index), 0, -1):
                    phrase = " ".join(item[3] for item in words[index : index + width]).casefold()
                    alias = _HEADER_ALIASES.get(re.sub(r"\s+", " ", phrase))
                    if alias is not None:
                        matched = (words[index][0], alias)
                        consumed = width
                        break
                header_columns.append(matched or (words[index][0], None))
                index += consumed
            if any(name in {"address", "protocol_offset", "display_address"} for _, name in header_columns) and sum(name is not None for _, name in header_columns) >= 2:
                columns = [(x, str(name)) for x, name in header_columns if name is not None]
                continue
            if columns is None:
                continue
            cells: dict[str, list[str]] = {name: [] for _, name in columns}
            cell_regions: dict[str, list[float]] = {}
            for x_min, x_max, y_max, text in words:
                candidates = [(x, name) for x, name in columns if x <= x_min + 3]
                _anchor, name = max(candidates or [columns[0]], key=lambda item: item[0])
                cells[name].append(text)
                bounds = cell_regions.setdefault(name, [x_min, y_min, x_max, y_max])
                bounds[0] = min(bounds[0], x_min)
                bounds[2] = max(bounds[2], x_max)
                bounds[3] = max(bounds[3], y_max)
            record = {name: " ".join(values).strip() for name, values in cells.items() if values}
            address = record.get("address", record.get("protocol_offset", record.get("display_address")))
            if not re.fullmatch(r"(?:0[xX][0-9A-Fa-f]+|\d+)", str(address or "")):
                continue
            region = f"p{page_number}:y{y_min:g}"
            record["_claims"] = [
                _claim(
                    "pdftotext-bbox-layout/v1",
                    field,
                    str(value),
                    {"page": page_number, "region": region, "bbox": cell_regions[field]},
                )
                for field, value in record.items()
                if not field.startswith("_")
            ]
            record["_source"] = {
                "format": "pdf",
                "page": page_number,
                "region": region,
                "parser_id": "pdftotext-bbox-layout/v1",
                "method": "coordinate-derived",
                "excerpt": " | ".join(str(record[name]) for _, name in columns if name in record)[:300],
            }
            records.append(record)
    return records


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
    address = record.get(
        "address",
        record.get("protocol_offset", record.get("display_address", "")),
    )
    normalized_address = re.sub(r"\s+", "", str(address).casefold())
    name = re.sub(r"\W+", "", str(record.get("name", "")).casefold())
    return page, normalized_address, name


def _equivalent(field: str, left: Any, right: Any) -> bool:
    def normalized(value: Any) -> str:
        text = re.sub(r"\s+", " ", str(value).strip()).casefold()
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


def _reconcile(strict: list[dict[str, Any]], coordinate: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    unmatched_right = set(range(len(coordinate)))
    left_name_counts: dict[tuple[int, str], int] = {}
    right_name_counts: dict[tuple[int, str], int] = {}
    left_address_counts: dict[tuple[int, str], int] = {}
    right_address_counts: dict[tuple[int, str], int] = {}
    for record in strict:
        key = _name_identity(record)
        left_name_counts[key] = left_name_counts.get(key, 0) + 1
        address_key = _address_identity(record)
        left_address_counts[address_key] = left_address_counts.get(address_key, 0) + 1
    for record in coordinate:
        key = _name_identity(record)
        right_name_counts[key] = right_name_counts.get(key, 0) + 1
        address_key = _address_identity(record)
        right_address_counts[address_key] = right_address_counts.get(address_key, 0) + 1
    for left in strict:
        exact = [
            index
            for index in unmatched_right
            if _identity(coordinate[index]) == _identity(left)
        ]
        match_index = exact[0] if len(exact) == 1 else None
        address_key = _address_identity(left)
        if (
            match_index is None
            and address_key[1]
            and left_address_counts[address_key] == 1
            and right_address_counts.get(address_key) == 1
        ):
            match_index = next(
                index
                for index in unmatched_right
                if _address_identity(coordinate[index]) == address_key
            )
        name_key = _name_identity(left)
        if (
            match_index is None
            and name_key[1]
            and left_name_counts[name_key] == 1
            and right_name_counts.get(name_key) == 1
        ):
            match_index = next(
                index
                for index in unmatched_right
                if _name_identity(coordinate[index]) == name_key
            )
        if match_index is None:
            accepted.append(dict(left))
            continue
        unmatched_right.remove(match_index)
        right = coordinate[match_index]
        row_conflicts = []
        for field in sorted(_MATERIAL_FIELDS & (set(left) & set(right))):
            if not _equivalent(field, left[field], right[field]):
                row_conflicts.append({"field": field, "claims": [left[field], right[field]]})
        merged = dict(left)
        for field, value in right.items():
            if not field.startswith("_") and field not in merged:
                merged[field] = value
        merged["_claims"] = [*left.get("_claims", []), *right.get("_claims", [])]
        if row_conflicts:
            quarantined.append(merged)
            identity = _identity(left)
            conflicts.append({"identity": {"page": identity[0], "address": identity[1], "name": identity[2]}, "fields": row_conflicts, "source_regions": [left["_source"]["region"], right["_source"]["region"]]})
        else:
            accepted.append(merged)
    accepted.extend(dict(coordinate[index]) for index in sorted(unmatched_right))
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
    coverage = _source_coverage(
        records, rejected, quarantined, discovered_pages, discovery_complete
    )
    effective_holds = list(holds)
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
        return _recover_grid_or(path, source, page_range=page_range, fallback=fallback, capability=capability)
    except (OSError, PdfExtractionError) as exc:
        fallback = _hold_result(path, source, "pdf-coordinate-extraction-resource-limit", str(exc), page_range=page_range, capability=capability)
        return _recover_grid_or(path, source, page_range=page_range, fallback=fallback, capability=capability)
    if bbox_result.returncode != 0:
        fallback = _hold_result(path, source, "pdf-coordinate-extraction-failed", "pdftotext coordinate extraction failed.", page_range=page_range, capability=capability)
        return _recover_grid_or(path, source, page_range=page_range, fallback=fallback, capability=capability)
    try:
        coordinate = parse_bbox_rows(bbox_result.stdout.decode("utf-8", errors="replace"), first_page=min(discovered))
    except PdfExtractionError:
        fallback = _hold_result(path, source, "pdf-coordinate-output-malformed", "pdftotext coordinate output was malformed and could not be reconciled safely.", page_range=page_range, capability=capability)
        return _recover_grid_or(path, source, page_range=page_range, fallback=fallback, capability=capability)
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
        records, grid_quarantined, grid_conflicts = _reconcile(records, grid)
        quarantined.extend(grid_quarantined)
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
                chunk_records, grid_quarantined, grid_conflicts = _reconcile(
                    chunk_records, grid
                )
                chunk_quarantined.extend(grid_quarantined)
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
) -> dict[str, Any]:
    pages = (
        list(range(page_range[0], page_range[1] + 1))
        if page_range is not None
        else None
    )
    try:
        grid, quarantined, discovered = _recover_grid_rows(path, pages=pages)
    except PdfTableExtractionError:
        return fallback
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
