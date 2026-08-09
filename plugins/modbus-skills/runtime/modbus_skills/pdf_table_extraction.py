"""Grid-aware extraction for text PDFs with drawn or aligned register tables."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


class PdfTableExtractionError(ValueError):
    """Raised when the optional grid extractor cannot run safely."""


_ADDRESS = re.compile(r"^(\d+)(?:/(\d+))?(\*)?$")
_PAIR_SUFFIX = re.compile(r"\s*\((MSR|LSR)\)$", re.IGNORECASE)
_HEADER_NAMES = {
    "address": "address",
    "register": "address",
    "register address": "address",
    "reg": "address",
    "reg.": "address",
    "r/w": "access",
    "access": "access",
    "nv": "nonvolatile",
    "format": "format",
    "data type": "format",
    "datatype": "format",
    "units": "units",
    "unit": "units",
    "scale": "scale",
    "range": "range",
    "description": "description",
    "name": "description",
}
_INHERITED_FIELDS = frozenset(
    {"access", "nonvolatile", "format", "units", "scale", "range"}
)
_MAX_GRID_PAGES = 256
_MAX_GRID_RECORDS = 50_000
_MAX_GRID_OUTPUT_BYTES = 32_000_000
_GRID_TIMEOUT_SECONDS = 60


def extract_pdf_table_rows(
    path: Path, *, pages: Sequence[int] | None = None
) -> list[dict[str, Any]]:
    """Extract register rows in a killable, output-bounded worker process."""

    selected = sorted(set(pages)) if pages is not None else None
    if selected is not None:
        if any(isinstance(page, bool) or not isinstance(page, int) or page < 1 for page in selected):
            raise PdfTableExtractionError("grid extraction pages must be positive integers")
        if len(selected) > _MAX_GRID_PAGES:
            raise PdfTableExtractionError(
                f"grid extraction is limited to {_MAX_GRID_PAGES} selected pages"
            )
    argv = [sys.executable, str(Path(__file__).resolve()), "--worker", str(path)]
    if selected is not None:
        argv.append(",".join(map(str, selected)))
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(argv, stdout=stdout, stderr=stderr, shell=False)
            try:
                returncode = process.wait(timeout=_GRID_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise PdfTableExtractionError(
                    f"grid extraction exceeded the {_GRID_TIMEOUT_SECONDS} second limit"
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
    if not isinstance(decoded, list) or any(not isinstance(row, dict) for row in decoded):
        raise PdfTableExtractionError("grid extraction worker returned an invalid record list")
    return decoded


def _extract_pdf_table_rows_in_process(
    path: Path, *, pages: Sequence[int] | None = None
) -> list[dict[str, Any]]:
    """Run pdfplumber inside the bounded worker process."""

    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - exercised in dependency preflight
        raise PdfTableExtractionError(
            "pdfplumber is required for grid-aware PDF table recovery"
        ) from exc

    selected = set(pages) if pages is not None else None
    records: list[dict[str, Any]] = []
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
                    records.extend(
                        parse_pdf_table(
                            table,
                            page_number=page_number,
                            table_index=table_index,
                        )
                    )
                    if len(records) > _MAX_GRID_RECORDS:
                        raise PdfTableExtractionError(
                            f"grid extraction exceeds {_MAX_GRID_RECORDS} records"
                        )
    except PdfTableExtractionError:
        raise
    except Exception as exc:
        raise PdfTableExtractionError(
            "pdfplumber could not extract bounded table geometry"
        ) from exc
    return records


def parse_pdf_table(
    table: Sequence[Sequence[Any]], *, page_number: int, table_index: int
) -> list[dict[str, Any]]:
    """Parse one grid table when its columns establish register semantics."""

    if not table:
        return []
    header_index, columns = _find_header(table)
    if columns is None:
        return []
    records: list[dict[str, Any]] = []
    inherited: dict[str, str] = {}
    for row_index, raw_row in enumerate(table[header_index + 1 :], start=header_index + 1):
        row = list(raw_row)
        address_text = _clean(_cell(row, columns["address"]))
        address_match = _ADDRESS.fullmatch(address_text)
        if address_match is None:
            continue
        first_address = int(address_match.group(1))
        second_address = (
            int(address_match.group(2)) if address_match.group(2) else None
        )
        if second_address is not None and second_address != first_address + 1:
            continue
        values: dict[str, str] = {}
        for field, column in columns.items():
            if field == "address":
                continue
            raw_value = _cell(row, column)
            value = _clean(raw_value)
            if field in _INHERITED_FIELDS and raw_value is None:
                value = inherited.get(field, "")
            elif field in _INHERITED_FIELDS:
                inherited[field] = value
            values[field] = value
        description_column = columns.get("description")
        trailing = []
        if description_column is not None:
            trailing = [
                _clean(value)
                for value in row[description_column + 1 :]
                if _clean(value)
            ]
        description = values.get("description", "")
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
        region = f"p{page_number}:t{table_index}:r{row_index}"
        record: dict[str, Any] = {
            "source_register": address_text,
            "address": first_address,
            "word_count": 2 if second_address is not None else 1,
            "logical_point_id": f"source-{first_address}" + (
                f"-{second_address}" if second_address is not None else ""
            ),
            "footnote_marker": bool(address_match.group(3)),
            **{field: value for field, value in values.items() if value},
            "name": description,
            "description": description,
            "_source": {
                "format": "pdf",
                "page": page_number,
                "row": row_index,
                "region": region,
                "parser_id": "pdfplumber-table/v1",
                "method": "coordinate-derived",
                "excerpt": " | ".join(
                    _clean(value) for value in row if _clean(value)
                )[:300],
            },
        }
        if trailing:
            record["notes"] = " | ".join(trailing)
        records.append(record)
    return records


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
                    "name": _PAIR_SUFFIX.sub(
                        "", str(current.get("name", ""))
                    ).strip(),
                    "description": _PAIR_SUFFIX.sub(
                        "", str(current.get("description", ""))
                    ).strip(),
                    "logical_point_id": f"source-{first}-{first + 1}",
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
            logical.append(merged)
            index += 2
            continue
        logical.append(current)
        index += 1
    result["records"] = logical
    return result


def _prepare_pdf_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    record = dict(raw)
    description = str(record.get("description") or "").strip()
    record.setdefault("name", description or None)
    source_format = str(record.get("format") or "").strip()
    if source_format:
        record["datatype"] = source_format
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
) -> tuple[int, dict[str, int] | None]:
    for row_index, row in enumerate(table[:5]):
        columns: dict[str, int] = {}
        for column, value in enumerate(row):
            name = _HEADER_NAMES.get(_header_text(value))
            if name is not None and name not in columns:
                columns[name] = column
        if "address" not in columns and "access" in columns and columns["access"] > 0:
            columns["address"] = columns["access"] - 1
        if "address" in columns and "description" in columns and (
            "access" in columns or "format" in columns
        ):
            return row_index, columns
    return 0, None


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
    "extract_pdf_table_rows",
    "parse_pdf_table",
    "prepare_pdf_records",
]


def _worker_main(argv: Sequence[str]) -> int:
    if len(argv) not in {3, 4} or argv[1] != "--worker":
        return 2
    pages = [int(value) for value in argv[3].split(",")] if len(argv) == 4 and argv[3] else None
    try:
        records = _extract_pdf_table_rows_in_process(Path(argv[2]), pages=pages)
        payload = json.dumps(records, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
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
