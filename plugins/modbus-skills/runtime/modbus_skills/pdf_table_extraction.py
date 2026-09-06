"""Grid-aware extraction for text PDFs with drawn or aligned register tables."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence
from fractions import Fraction
from hashlib import file_digest
import json
import math
import re
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

if __package__:
    from .pdf_protocol_context import EXCLUDED_PROTOCOL_CODE, protocol_contexts
else:  # The isolated grid worker executes this exact file, not python -m.
    from pdf_protocol_context import EXCLUDED_PROTOCOL_CODE, protocol_contexts


class PdfTableExtractionError(ValueError):
    """Raised when the optional grid extractor cannot run safely."""


class PdfTableEvidenceBudgetError(PdfTableExtractionError):
    """A localized proof/index budget exhausted before claim association."""

    def __init__(self, message: str, *, page: int, table_index: int, stage: str):
        super().__init__(message)
        self.page, self.table_index, self.stage = page, table_index, stage


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
    "addr": "address",
    "addr.": "address",
    "register": "address",
    "register address": "address",
    "register address (decimal)": "address",
    "register number": "address",
    "holding address": "address",
    "holding register address": "address",
    "reg no": "address",
    "reg no.": "address",
    "reg addr": "address",
    "reg addr.": "address",
    "start": "address",
    "start address": "address",
    "reg": "address",
    "reg.": "address",
    "e50xxa reg": "address",
    "e50xxa reg.": "address",
    "modbus address": "address",
    "modbus register": "address",
    "protocol offset": "protocol_offset",
    "offset": "source_offset",
    "display address": "display_address",
    "r/w": "access",
    "read/write": "access",
    "read/": "access",
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
    "engineering offset": "engineering_offset",
    "range": "range",
    "data range": "range",
    "description": "description",
    "meaning": "description",
    "semantics": "description",
    "parameter details": "description",
    "parameter description": "description",
    "contents": "name",
    "name": "name",
    "system name": "name",
    "tag": "name",
    "item": "name",
    "symbolic register name": "name",
    "parameter": "name",
    "parameters": "name",
    "parameter name": "name",
    "variable": "name",
    "modbus register type": "area",
    "area": "area",
    "unit id": "unit_id",
    "word count": "word_count",
    "words": "word_count",
    "byte order": "byte_order",
    "bit order": "bit_order",
}
_HEADER_NAMES = PDF_HEADER_ALIASES
_INHERITED_FIELDS = frozenset(
    {"access", "nonvolatile", "format", "units", "scale", "range"}
)
_AREA_BY_PREFIX = {
    "0": "coil",
    "1": "discrete-input",
    "3": "input-register",
    "4": "holding-register",
}
_PREFIX_BY_AREA = {value: key for key, value in _AREA_BY_PREFIX.items()}
_MAX_GRID_PAGES = 256
_MAX_GRID_RECORDS = 50_000
_MAX_GRID_OUTPUT_BYTES = 32_000_000
# Bound duplicated source proof before associating any common-cell claims.
_MAX_MERGED_PROOF_BYTES = 4_000_000
_MAX_MERGED_INDEX_INCIDENCES = 500_000
_GRID_TIMEOUT_SECONDS = 60


def extract_pdf_table_evidence(
    path: Path, *, pages: Sequence[int] | None = None, timeout_seconds: int = _GRID_TIMEOUT_SECONDS,
    cell_partition_pages: Sequence[int] | None = None,
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
    partition_pages = sorted(set(cell_partition_pages or ()))
    if (len(partition_pages) > _MAX_GRID_PAGES or any(type(page) is not int or page < 1
            or (selected is not None and page not in selected) for page in partition_pages)):
        raise PdfTableExtractionError("cell partition pages must belong to the bounded grid scope")
    return (_run_grid_worker(path, selected, timeout_seconds, partition_pages)
            if partition_pages else _run_grid_worker(path, selected, timeout_seconds))


def extract_pdf_table_rows(
    path: Path, *, pages: Sequence[int] | None = None, timeout_seconds: int = _GRID_TIMEOUT_SECONDS
) -> list[dict[str, Any]]:
    """Compatibility projection containing only accepted grid rows."""

    return extract_pdf_table_evidence(
        path, pages=pages, timeout_seconds=timeout_seconds
    )["records"]


def _run_grid_worker(
    path: Path, selected: Sequence[int] | None, timeout_seconds: int,
    cell_partition_pages: Sequence[int] = (),
) -> dict[str, list[dict[str, Any]]]:
    argv = [sys.executable, str(Path(__file__).resolve()), "--worker", str(path)]
    if selected is not None:
        argv.append(",".join(map(str, selected)))
    if cell_partition_pages:
        if selected is None:
            argv.append("")
        argv.append(",".join(map(str, cell_partition_pages)))
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
    if returncode == 3:
        try:
            error = json.loads(error_text)
            if (not isinstance(error, dict) or error.get("error_type") != "pdf-grid-evidence-budget/v1"
                    or type(error.get("page")) is not int or error["page"] < 1
                    or type(error.get("table_index")) is not int or error["table_index"] < 0
                    or error.get("stage") not in {"indexing", "claim-association"}
                    or not isinstance(error.get("message"), str) or not error["message"]):
                raise ValueError("invalid budget error")
        except (ValueError, TypeError) as exc:
            raise PdfTableExtractionError("grid worker returned invalid budget-error evidence") from exc
        raise PdfTableEvidenceBudgetError(
            error["message"], page=error["page"], table_index=error["table_index"], stage=error["stage"]
        )
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
    path: Path, *, pages: Sequence[int] | None = None,
    cell_partition_pages: Sequence[int] = (),
) -> dict[str, list[dict[str, Any]]]:
    """Run pdfplumber inside the bounded worker process."""

    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - exercised in dependency preflight
        raise PdfTableExtractionError(
            "pdfplumber is required for grid-aware PDF table recovery"
        ) from exc

    selected = set(pages) if pages is not None else None
    partition_pages = set(cell_partition_pages)
    evidence = {"records": [], "quarantined_records": []}
    merged_budget = [0]
    try:
        with pdfplumber.open(path) as document:
            source_sha256 = None
            if selected is None and len(document.pages) > _MAX_GRID_PAGES:
                raise PdfTableExtractionError(
                    f"automatic grid extraction is limited to {_MAX_GRID_PAGES} PDF pages"
                )
            for page_number, page in enumerate(document.pages, start=1):
                if selected is not None and page_number not in selected:
                    continue
                # Table cells and header recovery need this reader's glyphs.
                # Image/vector geometry alone cannot supply register text;
                # do not use another reader's empty output as this test.
                if not page.chars:
                    continue
                protocol_lines, protocol_y, protocol_scope = _page_protocol_scope(page)
                for table_index, table in enumerate(page.find_tables()):
                    cells, header_recovery = _recover_offset_header(page, table)
                    merged = _proved_common_cells(
                        page, table, cells, page_number, table_index,
                        source_sha256 or "0" * 64, merged_budget,
                    )
                    if merged.claims:
                        if source_sha256 is None:
                            position = document.stream.tell()
                            document.stream.seek(0)
                            source_sha256 = file_digest(document.stream, "sha256").hexdigest()
                            document.stream.seek(position)
                        for claim in merged.claims.values():
                            claim["merged_cell_evidence"]["source_sha256"] = source_sha256
                    parsed = parse_pdf_table_evidence(
                        cells,
                        page_number=page_number,
                        table_index=table_index,
                        _common_cells=merged,
                    )
                    if protocol_scope:
                        kept = {"records": [], "quarantined_records": []}
                        for channel, row in (
                            (channel, row) for channel in kept for row in parsed[channel]
                        ):
                            row_index = row.get("_source", {}).get("row")
                            context = None
                            if type(row_index) is int and 0 <= row_index < len(table.rows):
                                position = bisect_right(protocol_y, table.rows[row_index].bbox[1] + 2.5) - 1
                                if position >= 0:
                                    context = protocol_scope[position]
                            if context is None:
                                kept[channel].append(row)
                            else:
                                row["code"] = EXCLUDED_PROTOCOL_CODE
                                row["protocol"] = "DNP3"
                                row["_source"]["context_refs"] = [{
                                    "page": page_number, "line": context + 1,
                                    "region": f"p{page_number}:y{protocol_y[context]:g}",
                                    "excerpt": protocol_lines[context][:300],
                                }]
                                evidence["quarantined_records"].append(row)
                        parsed = kept
                    if header_recovery is not None:
                        header_evidence = {
                            **header_recovery,
                            "source_locator": {
                                "page": page_number,
                                "row": 0,
                                "region": f"p{page_number}:t{table_index}:r0",
                            },
                        }
                        for row in (*parsed["records"], *parsed["quarantined_records"]):
                            row["_source"]["header_recovery"] = header_evidence
                            for claim in row.get("_claims", []):
                                if claim.get("column_index") == header_recovery["column_index"]:
                                    claim["header_evidence"] = header_evidence
                    geometry = _prepare_description_cell_geometry(page, table, cells,
                        include_name=page_number in partition_pages)
                    if page_number in partition_pages:
                        if source_sha256 is None:
                            position = document.stream.tell()
                            document.stream.seek(0)
                            source_sha256 = file_digest(document.stream, "sha256").hexdigest()
                            document.stream.seek(position)
                        for row in (*parsed["records"], *parsed["quarantined_records"]):
                            if row.get("code") not in (None, "pdf-grid-type-unresolved"):
                                continue
                            partition = _drawn_name_partition(page, table, cells, row, source_sha256,
                                                             geometry=geometry)
                            if partition is not None:
                                merged_budget[0] += len(json.dumps(partition, ensure_ascii=True).encode()) * 2
                                if merged_budget[0] > _MAX_MERGED_PROOF_BYTES:
                                    raise PdfTableEvidenceBudgetError("drawn cell evidence exceeds the shared proof budget",
                                        page=page_number, table_index=table_index, stage="claim-association")
                                row["_drawn_name_partition"] = partition
                    for row in parsed["records"]:
                        proof = _description_access_cell_evidence(
                            page, table, cells, row, "", geometry=geometry
                        )
                        if proof is not None:
                            if source_sha256 is None:
                                position = document.stream.tell()
                                document.stream.seek(0)
                                source_sha256 = file_digest(document.stream, "sha256").hexdigest()
                                document.stream.seek(position)
                            proof["source_sha256"] = source_sha256
                            for claim in row.get("_claims", []):
                                if claim.get("field") == "description":
                                    claim["body_cell_evidence"] = proof
                    evidence["records"].extend(parsed["records"])
                    evidence["quarantined_records"].extend(
                        parsed["quarantined_records"]
                    )
                    if sum(map(len, evidence.values())) > _MAX_GRID_RECORDS:
                        raise PdfTableExtractionError(
                            f"grid extraction exceeds {_MAX_GRID_RECORDS} records"
                        )
            if source_sha256 is not None:
                document.stream.seek(0)
                if file_digest(document.stream, "sha256").hexdigest() != source_sha256:
                    raise PdfTableExtractionError("PDF changed during grid extraction")
    except PdfTableExtractionError:
        raise
    except Exception as exc:
        raise PdfTableExtractionError(
            "pdfplumber could not extract bounded table geometry"
        ) from exc
    return evidence


def _page_protocol_scope(page: Any) -> tuple[list[str], list[float], list[int | None]]:
    """Decode heading coordinates once, only on pages containing DNP3 glyphs."""
    if "dnp3" not in "".join(str(c.get("text", "")) for c in page.chars).casefold():
        return [], [], []
    lines: list[list[Any]] = []
    for word in sorted(page.extract_words(), key=lambda w: (w["top"], w["x0"])):
        if not lines or abs(lines[-1][0] - word["top"]) > 2.5:
            lines.append([float(word["top"]), []])
        lines[-1][1].append(word)
    labels = [" ".join(w["text"] for w in sorted(words, key=lambda w: w["x0"]))
              for _top, words in lines]
    contexts = protocol_contexts(labels)
    return labels, [top for top, _words in lines], contexts if any(c is not None for c in contexts) else []


class _CommonCells:
    """Invocation-owned geometry registry, never decoded from row evidence."""

    def __init__(self, table: Any, page: int, index: int):
        self.table, self.page, self.index = table, page, index
        self.claims: dict[tuple[int, str], dict[str, Any]] = {}


def _proved_common_cells(
    page: Any, table: Any, cells: Sequence[Sequence[Any]],
    page_number: int, table_index: int, source_sha256: str, budget: list[int],
) -> _CommonCells:
    """Associate only uniquely owned, drawn same-table spanning body cells.

    Row/column indexes retain every intersecting glyph/edge, including boundary
    crossings. No preceding-value state or None sentinel is merge authority.
    """
    result = _CommonCells(cells, page_number, table_index)
    header, columns, _extras, _confident = _find_header(cells)
    if columns is None:
        return result
    rows = list(getattr(table, "rows", ()))
    if len(rows) != len(cells) or header >= len(rows):
        return result

    def valid(box: Any) -> bool:
        return (box is not None and len(box) == 4
                and all(isinstance(v, (int, float)) and math.isfinite(v) for v in box)
                and box[0] < box[2] and box[1] < box[3])

    heads = rows[header].cells
    if not heads or any(not valid(box) for box in heads):
        return result
    if any(left[2] != right[0] for left, right in zip(heads, heads[1:])):
        return result
    boundaries = []
    for row in rows:
        boxes = [box for box in row.cells if box is not None]
        if not boxes or any(not valid(box) for box in boxes):
            return result
        top = min(box[1] for box in boxes)
        if boundaries and top <= boundaries[-1]:
            return result
        boundaries.append(top)
    if not valid(table.bbox) or table.bbox[3] <= boundaries[-1]:
        return result
    boundaries.append(table.bbox[3])
    boundary_index = {value: index for index, value in enumerate(boundaries)}
    boxes_by_column: list[list[tuple[int, int, Any]]] = [[] for _ in heads]
    original_boxes = [tuple(box) for box in table.cells]
    if len(set(original_boxes)) != len(original_boxes):
        return result
    remaining = set(original_boxes)
    for row_index, row in enumerate(rows):
        if len(row.cells) != len(heads) or len(cells[row_index]) != len(heads):
            return result
        for column, box in enumerate(row.cells):
            if box is None:
                continue
            if (tuple(box) not in remaining or box[1] != boundaries[row_index]
                    or box[3] not in boundary_index
                    or (box[0], box[2]) != (heads[column][0], heads[column][2])):
                return result
            remaining.remove(tuple(box))
            end = boundary_index[box[3]]
            previous = boxes_by_column[column]
            if end <= row_index or (previous and previous[-1][1] > row_index):
                return result
            previous.append((row_index, end, box))
    if remaining or any(head[1] != boundaries[header] or head[3] != boundaries[header+1] for head in heads):
        return result
    eligible = [(field, items[0][0], items[0][1]) for field, items in columns.items()
                if field in _INHERITED_FIELDS and len(items) == 1]
    if not any(end > start + 1 and start > header
               for _field, column, _label in eligible
               for start, end, _box in boxes_by_column[column]):
        return result
    # Sparse two-dimensional indexes, not all rows times all page glyphs.
    glyph_index: dict[tuple[int, int], list[int]] = {}
    edge_index: dict[tuple[int, int], list[int]] = {}
    chars, edges = list(page.chars), list(getattr(page, "edges", ()))
    if not edges:
        return result
    xstarts, xends = [b[0] for b in heads], [b[2] for b in heads]
    row_tops, row_bottoms = boundaries[:-1], boundaries[1:]
    incidences = 0
    for values, index in ((chars, glyph_index), (edges, edge_index)):
        for number, item in enumerate(values):
            box = (item.get("x0"), item.get("top"), item.get("x1"), item.get("bottom"))
            if (any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in box)
                    or box[0] > box[2] or box[1] > box[3]):
                return result
            for row in range(bisect_left(row_bottoms, box[1]), bisect_right(row_tops, box[3])):
                for column in range(bisect_left(xends, box[0]), bisect_right(xstarts, box[2])):
                    incidences += 1
                    if incidences > _MAX_MERGED_INDEX_INCIDENCES:
                        raise PdfTableEvidenceBudgetError(
                            "merged-cell evidence budget exceeded while indexing geometry",
                            page=page_number, table_index=table_index, stage="indexing",
                        )
                    index.setdefault((row, column), []).append(number)

    def glyphs_for(box: Any, column: int, start: int, end: int, literal: str):
        numbers = {n for row in range(start, end) for n in glyph_index.get((row, column), ())}
        glyphs = []
        for number in sorted(numbers):
            char = chars[number]
            if not char.get("text", "").strip():
                continue
            x0, top, x1, bottom = char["x0"], char["top"], char["x1"], char["bottom"]
            if x0 >= box[2] or x1 <= box[0] or top >= box[3] or bottom <= box[1]:
                continue
            if (not char.get("upright", True) or x0 < box[0] or x1 > box[2]
                    or top < box[1] or bottom > box[3]):
                return None
            glyphs.append({"text": char["text"], "bbox": [x0, top, x1, bottom]})
        glyphs.sort(key=lambda glyph: (glyph["bbox"][1], glyph["bbox"][0]))
        if not glyphs or "".join(g["text"] for g in glyphs) != re.sub(r"\s+", "", literal):
            return None
        return glyphs

    def locator(row: int) -> dict[str, Any]:
        return {"page": page_number, "row": row, "region": f"p{page_number}:t{table_index}:r{row}"}

    for field, column, label in eligible:
        header_glyphs = glyphs_for(heads[column], column, header, header+1, _clean(label))
        if header_glyphs is None:
            continue
        for start, end, box in boxes_by_column[column]:
            if start <= header or end <= start + 1:
                continue
            literal = _clean(cells[start][column])
            if not literal or any(cells[row][column] is not None or rows[row].cells[column] is not None
                                  for row in range(start+1, end)):
                continue
            numbers = {n for row in range(start, end) for n in edge_index.get((row, column), ())}
            if any((edge["orientation"] == "h" and box[1] < edge["top"] < box[3]
                    and edge["x0"] < box[2] and edge["x1"] > box[0])
                   or (edge["orientation"] == "v" and box[0] < edge["x0"] < box[2]
                       and edge["top"] < box[3] and edge["bottom"] > box[1])
                   for edge in (edges[n] for n in numbers)):
                continue
            glyphs = glyphs_for(box, column, start, end, literal)
            if glyphs is None:
                continue
            # Bound strings/glyph duplication before building or associating the
            # repeated proof. JSON escapes at most twelve bytes per code point;
            # the fixed allowance covers keys, locators and finite PDF numbers.
            size = (4096 + 36 * (len(literal) + len(str(label)))
                    + sum(256 + 12 * len(g["text"])
                          + sum(len(str(v)) for v in g["bbox"])
                          for g in (*glyphs, *header_glyphs)))
            required = size * (end-start-1)
            if budget[0] + required > _MAX_MERGED_PROOF_BYTES:
                raise PdfTableEvidenceBudgetError(
                    "merged-cell evidence budget exceeded before claim association",
                    page=page_number, table_index=table_index, stage="claim-association",
                )
            budget[0] += required
            base = {"method": "same-source-spanning-cell/v1", "source_sha256": source_sha256,
                    "source_locator": locator(start), "source_cell_bbox": list(box),
                    "raw_header": label, "raw_value": literal, "glyphs": glyphs,
                    "header_cell_bbox": list(heads[column]), "header_glyphs": header_glyphs}
            for target in range(start+1, end):
                proof = {**base, "target_locator": locator(target),
                         "target_row_band": boundaries[target:target+2]}
                result.claims[(target, field)] = {
                    "parser_id": "pdfplumber-table/v1", "field": field,
                    "value": literal, "raw_value": literal, "raw_header": label,
                    "column_index": column, "source_locator": locator(start),
                    "merged_cell_evidence": proof,
                }
    return result


def _prepare_description_cell_geometry(
    page: Any, table: Any, cells: Sequence[Sequence[Any]], *, include_name: bool = False,
) -> dict[str, Any]:
    """Index original glyphs by every intersected row, without clipping them."""
    header = _find_header(cells)
    prepared: dict[str, Any] = {"header": header, "header_glyphs": {}}
    if header[1] is None or (any(len(header[1].get(field, ())) != 1
                                for field in ("description", "access"))
                            and (not include_name or any(len(header[1].get(field, ())) != 1
                                for field in ("address", "name", "description")))):
        return prepared
    # pdfplumber's rows property sorts/regroups all cells on each access.
    # Retain this table-local result, including the original ambiguous cells.
    prepared["rows"] = table.rows
    bands = []
    for row in prepared["rows"]:
        boxes = [box for box in row.cells if box is not None]
        if not boxes:
            return prepared
        top, bottom = min(box[1] for box in boxes), max(box[3] for box in boxes)
        if (not math.isfinite(top) or not math.isfinite(bottom) or top >= bottom
                or (bands and bands[-1][1] > top)):
            # Merged/overlapping/unproved bands use the original full-page scan.
            return prepared
        bands.append((top, bottom))
    tops, bottoms = [b[0] for b in bands], [b[1] for b in bands]
    by_row: list[list[Any]] = [[] for _ in bands]
    for char in page.chars:
        top, bottom = char["top"], char["bottom"]
        if not math.isfinite(top) or not math.isfinite(bottom) or top > bottom:
            return prepared
        # Inclusive edges retain all ambiguous/touching glyphs for the unchanged
        # ownership checks. Original boxes and page order are retained verbatim.
        for index in range(bisect_left(bottoms, top), bisect_right(tops, bottom)):
            by_row[index].append(char)
    prepared["glyphs_by_row"] = by_row
    return prepared


def _description_access_cell_evidence(
    page: Any, table: Any, cells: Sequence[Sequence[Any]],
    record: Mapping[str, Any], source_sha256: str,
    *, geometry: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Prove two literal adjacent columns from their own drawn cells/glyphs.

    No inference from whitespace gaps, access-looking suffixes, inherited cells,
    or another reader's text. Multiline/crossing/ambiguous geometry stays unproved.
    """
    header_index, columns, _extras, _confident = (
        geometry["header"] if geometry is not None else _find_header(cells)
    )
    if columns is None or any(len(columns.get(field, ())) != 1
                              for field in ("description", "access")):
        return None
    description_index = columns["description"][0][0]
    access_index = columns["access"][0][0]
    row_index = record["_source"]["row"]
    rows = geometry["rows"] if geometry is not None else table.rows
    if access_index != description_index + 1 or row_index >= len(rows):
        return None
    body, header = rows[row_index].cells, rows[header_index].cells
    if access_index >= min(len(body), len(header)):
        return None
    boxes = [body[description_index], body[access_index]]
    header_boxes = [header[description_index], header[access_index]]
    if any(box is None for box in [*boxes, *header_boxes]):
        return None
    if (boxes[0][2] != boxes[1][0] or boxes[0][1::2] != boxes[1][1::2]
            or any((box[0], box[2]) != (head[0], head[2])
                   for box, head in zip(boxes, header_boxes))):
        return None

    def glyphs_for(box: Any, row_boxes: Sequence[Any], literal: str, row_number: int, *, is_header: bool = False):
        glyphs = []
        chars = (geometry["glyphs_by_row"][row_number]
                 if geometry is not None and "glyphs_by_row" in geometry else page.chars)
        for char in chars:
            if not char["text"].strip():
                continue
            x0, x1, top, bottom = char["x0"], char["x1"], char["top"], char["bottom"]
            cx, cy = (x0 + x1) / 2, (top + bottom) / 2
            if not (box[0] <= cx <= box[2] and box[1] <= cy <= box[3]):
                # A body glyph crossing into the cell is not uniquely owned.
                if not is_header and x0 < box[2] and x1 > box[0] and top < box[3] and bottom > box[1]:
                    return None
                continue
            owners = sum(b is not None and b[0] <= cx <= b[2] and b[1] <= cy <= b[3]
                         for b in row_boxes)
            if (owners != 1 or not char.get("upright", True)
                    or x0 < box[0] or x1 > box[2]
                    or (not is_header and (top < box[1] or bottom > box[3]))):
                return None
            glyphs.append({"text": char["text"], "bbox": [x0, top, x1, bottom]})
        if not glyphs or max(g["bbox"][1] for g in glyphs) >= min(g["bbox"][3] for g in glyphs):
            return None
        glyphs.sort(key=lambda g: g["bbox"][0])
        if "".join(g["text"] for g in glyphs) != re.sub(r"\s+", "", literal):
            return None
        return glyphs

    proof_cells = []
    for field, index, box, head in zip(("description", "access"),
                                     (description_index, access_index), boxes, header_boxes):
        literal, label = _clean(_cell(cells[row_index], index)), _clean(_cell(cells[header_index], index))
        if not literal or literal != record.get(field):
            return None
        glyphs = glyphs_for(box, body, literal, row_index)
        if geometry is not None and index in geometry["header_glyphs"]:
            header_glyphs = geometry["header_glyphs"][index]
        else:
            header_glyphs = glyphs_for(head, header, label, header_index, is_header=True)
            if geometry is not None:
                geometry["header_glyphs"][index] = header_glyphs
        if glyphs is None or header_glyphs is None:
            return None
        proof_cells.append({"field": field, "column_index": index, "raw_value": literal,
                            "raw_header": label, "bbox": list(box), "glyphs": glyphs,
                            "header_bbox": list(head), "header_glyphs": header_glyphs})
    return {"source_sha256": source_sha256, "source_locator": {
                key: record["_source"][key] for key in ("page", "row", "region")},
            "method": "same-source-drawn-cells-and-glyphs/v1", "cells": proof_cells}


def _drawn_name_partition(
    page: Any, table: Any, cells: Sequence[Sequence[Any]],
    record: Mapping[str, Any], source_sha256: str, *, geometry: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Literal address/name/description cells, never inferred column edges."""
    header_index, columns, _extras, _confident = geometry["header"] if geometry is not None else _find_header(cells)
    fields = ("address", "name", "description")
    if columns is None or any(len(columns.get(field, ())) != 1 for field in fields):
        return None
    row_index = record.get("_source", {}).get("row")
    rows = geometry["rows"] if geometry is not None else table.rows
    if type(row_index) is not int or not header_index < row_index < len(rows):
        return None
    body = rows[row_index].cells
    def prove_cell(field: str) -> dict[str, Any] | None:
        index, raw_header = columns[field][0]
        if index >= len(body):
            return None
        # The joined header's final row may contain only an unrelated wrapped
        # label. Bind this exact label to its own unique earlier drawn cell.
        header_matches = [h for h in range(header_index+1)
            if index < len(rows[h].cells) and rows[h].cells[index] is not None
            and _clean(_cell(cells[h],index)) == raw_header]
        if len(header_matches) != 1:
            return None
        actual_header = header_matches[0]
        header = rows[actual_header].cells
        box, head = body[index], header[index]
        if (box is None or head is None or any(len(b) != 4 or not all(math.isfinite(v) for v in b)
                or b[0] >= b[2] or b[1] >= b[3] for b in (box, head))
                or box[0::2] != head[0::2]):
            return None
        literal = _clean(_cell(cells[row_index], index))
        if not literal or (field == "address" and literal != record.get("source_register")):
            return None
        def owned_glyphs(bounds: Any, row_cells: Any, expected: str, *, body_cell: bool):
            glyphs = []
            source_row = row_index if body_cell else actual_header
            if geometry is not None and "glyphs_by_row" in geometry:
                chars = geometry["glyphs_by_row"][source_row]
            elif geometry is not None:
                # Header subrows can overlap a spanning header band. Cache
                # each original row's intersecting glyphs once, not per field.
                cache = geometry.setdefault("partition_row_glyphs", {})
                if source_row not in cache:
                    boxes = [b for b in rows[source_row].cells if b is not None]
                    top,bottom = min(b[1] for b in boxes),max(b[3] for b in boxes)
                    cache[source_row] = [c for c in page.chars if c["top"] < bottom and c["bottom"] > top]
                chars = cache[source_row]
            else:
                chars = page.chars
            for char in chars:
                if not char["text"].strip():
                    continue
                b = [char["x0"], char["top"], char["x1"], char["bottom"]]
                if not all(math.isfinite(v) for v in b):
                    return None
                if not (b[0] < bounds[2] and b[2] > bounds[0] and b[1] < bounds[3] and b[3] > bounds[1]):
                    continue
                if (not all(math.isfinite(v) for v in b) or not char.get("upright", True)
                        or b[0] < bounds[0] or b[2] > bounds[2] or b[1] < bounds[1] or b[3] > bounds[3]
                        or sum(c is not None and c[0] <= b[0] and b[2] <= c[2]
                               and c[1] <= b[1] and b[3] <= c[3] for c in row_cells) != 1):
                    return None
                glyphs.append({"text": char["text"], "bbox": b})
            if (not glyphs or "".join(g["text"] for g in glyphs) != re.sub(r"\s+", "", expected)
                    or (body_cell and max(g["bbox"][1] for g in glyphs) >= min(g["bbox"][3] for g in glyphs))):
                return None
            return glyphs
        glyphs = owned_glyphs(box, body, literal, body_cell=True)
        header_glyphs = owned_glyphs(head, header, raw_header, body_cell=False)
        if glyphs is None or header_glyphs is None:
            return None
        return {"field":"engineering_unit" if field == "units" else field, "raw_value":literal, "raw_header":raw_header,
                            "bbox":list(box), "header_bbox":list(head), "glyphs":glyphs,
                            "header_glyphs":header_glyphs, "column_index":index,
                            "header_source_locator":{"page":record["_source"]["page"],"row":actual_header,
                                "region":re.sub(r":r\d+$",f":r{actual_header}",record["_source"]["region"])}}
    proof_cells = []
    for field in fields:
        cell = prove_cell(field)
        if cell is None:
            return None
        proof_cells.append(cell)
    for field in ("units","range"):
        if len(columns.get(field, ())) == 1:
            cell = prove_cell(field)
            if cell is not None:
                proof_cells.append(cell)
    boxes = [cell["bbox"] for cell in proof_cells]
    if any(a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]
           for i, a in enumerate(boxes) for b in boxes[i+1:]):
        return None
    return {"method":"same-source-drawn-name-cells/v1", "source_sha256":source_sha256,
            "source_locator":{key:record["_source"][key] for key in ("page","row","region")},
            "cells":proof_cells}


def _recover_offset_header(page: Any, table: Any) -> tuple[list[list[Any]], dict[str, Any] | None]:
    """Recover one open outer header cell from its own aligned literal glyphs."""
    cells = table.extract()
    if not cells or len(table.rows) < 3 or _find_header(cells)[1] is not None:
        return cells, None
    header_cells = table.rows[0].cells
    missing = [index for index, box in enumerate(header_cells) if box is None]
    # Only the proved missing outer-right header-border case is supported.
    if missing != [len(header_cells) - 1] or len(header_cells) < 3:
        return cells, None
    column = missing[0]
    if cells[0][column] not in (None, ""):
        return cells, None
    labels = {_HEADER_NAMES.get(_header_text(value)) for value in cells[0] if value}
    if not {"name", "description"} & labels or not {"access", "format", "area"} & labels:
        return cells, None
    boxes = [row.cells[column] if column < len(row.cells) else None for row in table.rows[1:]]
    if any(box is None for box in boxes):
        return cells, None
    x0, x1 = boxes[0][0], boxes[0][2]
    if any(abs(box[0] - x0) > 0.5 or abs(box[2] - x1) > 0.5 for box in boxes):
        return cells, None
    header = table.rows[0].bbox
    glyphs = [char for char in page.chars
              if x0 <= (char["x0"] + char["x1"]) / 2 <= x1
              and header[1] <= (char["top"] + char["bottom"]) / 2 <= header[3]]
    if not glyphs or max(char["top"] for char in glyphs) - min(char["top"] for char in glyphs) > 1:
        return cells, None
    glyphs.sort(key=lambda char: char["x0"])
    label = "".join(char["text"] for char in glyphs).strip()
    if label.casefold() != "offset":
        return cells, None
    recovered = [list(row) for row in cells]
    recovered[0][column] = label
    header_index, columns, _extras, _confident = _find_header(recovered)
    if header_index != 0 or columns is None or columns.get("address") != [(column, label)]:
        return cells, None
    return recovered, {
        "column_index": column,
        "original_cell": cells[0][column],
        "original_cell_bbox": None,
        "recovered_text": label,
        "method": "coordinate-derived",
        "derived_bbox": [x0, header[1], x1, header[3]],
        "glyphs": [{"text": char["text"], "bbox": [char["x0"], char["top"], char["x1"], char["bottom"]]} for char in glyphs],
    }


def parse_pdf_table(
    table: Sequence[Sequence[Any]], *, page_number: int, table_index: int
) -> list[dict[str, Any]]:
    """Compatibility projection containing accepted rows from one grid table."""

    return parse_pdf_table_evidence(
        table, page_number=page_number, table_index=table_index
    )["records"]


def parse_pdf_table_evidence(
    table: Sequence[Sequence[Any]], *, page_number: int, table_index: int,
    _common_cells: _CommonCells | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Parse one grid table and retain ambiguous rows separately."""

    if not table:
        return {"records": [], "quarantined_records": []}
    header_index, columns, extra_columns, confident = _find_header(table)
    if columns is None:
        return {"records": [], "quarantined_records": []}
    records: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    common = (_common_cells.claims if type(_common_cells) is _CommonCells
              and _common_cells.table is table and _common_cells.page == page_number
              and _common_cells.index == table_index else {})
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
        parsed_address = (
            _parse_source_offset(address_text)
            if any(_header_text(raw) == "offset" for _column, raw in columns["address"])
            else _parse_pdf_address(
                address_text,
                protocol_offset=any(_header_text(raw) == "protocol offset"
                                    for _column, raw in columns["address"]),
            )
        )
        if parsed_address is None:
            continue
        values: dict[str, str] = {}
        claims: list[dict[str, Any]] = []
        for field, candidates in columns.items():
            if field == "address":
                value = address_text
            else:
                value = resolved.get(field, "")
            claim = common.get((row_index, field)) if not value else None
            if claim is not None and _cell(row, claim["column_index"]) is None:
                value = claim["value"]
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
        extra_fields: dict[str, str] = {}
        extra_conflicts: list[str] = []
        extra_values: dict[str, str] = {}
        for column, header in extra_columns:
            value = _clean(_cell(row, column))
            if not value:
                continue
            field = "_extra:" + re.sub(r"[^a-z0-9]+", "_", header.casefold()).strip("_")
            if field in extra_values and extra_values[field] != value:
                extra_conflicts.append(field)
            extra_values.setdefault(field, value)
            extra_fields.setdefault(header, value)
            claims.append({
                "parser_id": "pdfplumber-table/v1", "field": field, "value": value,
                "raw_header": header, "raw_value": value, "column_index": column,
                "source_locator": {"page": page_number, "row": row_index, "region": region},
            })
        for column, header in columns.get("units", []):
            raw_unit = _clean(_cell(row, column))
            if raw_unit:
                extra_fields.setdefault(header, raw_unit)
        record: dict[str, Any] = {
            **{field: value for field, value in values.items() if value},
            **_address_record_fields(parsed_address, explicit_word_count=values.get("word_count")),
            "name": name,
            "description": description,
            "_claims": claims,
            "_source": source,
        }
        if extra_fields:
            record["_extra"] = extra_fields
        if trailing:
            record["notes"] = " | ".join(trailing)
        if extra_conflicts:
            record["code"] = "pdf-grid-column-ambiguous"
            record["fields"] = sorted(set(extra_conflicts))
            quarantined.append(record)
        elif record.get("code") == "pdf-address-width-conflict":
            quarantined.append(record)
        else:
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
    _lift_pdf_source_address(record)
    return record


def _lift_pdf_source_address(record: dict[str, Any]) -> None:
    """Expose PDF address evidence on the fields normalize already understands.

    Prefer leaving structured ``source_address`` alone — normalize already reads
    it. Only lift a bare ``address_number`` / ``source_register`` when those
    nested fields are missing so the row is not dropped as address-less.
    """

    if any(
        record.get(key) not in (None, "")
        for key in ("address", "protocol_offset", "display_address")
    ):
        return
    if isinstance(record.get("source_address"), Mapping) and record["source_address"].get(
        "raw"
    ) not in (None, ""):
        return
    number = record.get("address_number")
    if not isinstance(number, int):
        number = _source_register_number(record.get("source_register"))
    if not isinstance(number, int):
        return
    record["address"] = number


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
            if "address" not in columns and "source_offset" in columns:
                columns["address"] = columns.pop("source_offset")
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


def _parse_pdf_address(value: Any, *, protocol_offset: bool = False) -> dict[str, Any] | None:
    """Parse one source address without creating a protocol offset."""

    raw = _clean(value)
    match = _ADDRESS.fullmatch(raw)
    if match is None:
        return None
    parse_component = _parse_protocol_component if protocol_offset else _parse_address_component
    first = parse_component(match.group("first"))
    second_text = match.group("second")
    separator = match.group("separator")
    parsed: dict[str, Any] = {
        "raw": raw,
        "first": first,
        "second": parse_component(second_text) if second_text else None,
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


def _parse_protocol_component(value: str) -> dict[str, Any]:
    """Use an explicit protocol header without display-prefix heuristics."""
    numeric = re.fullmatch(r"(?:0[xX][0-9A-Fa-f]+|\d+)", value) is not None
    return {
        "raw": value,
        "status": "single" if numeric else "ambiguous",
        "convention": "protocol-offset",
        "area": None,
        "number": int(value, 16 if value.lower().startswith("0x") else 10) if numeric else None,
    }


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
    if parsed.get("source_offset") or parsed.get("convention") == "protocol-offset":
        # An area does not establish whether an unqualified Offset is zero-based,
        # one-based, a reference number, or an engineering bias; it also cannot
        # change an already explicit protocol basis into a display reference.
        result["area"] = area
        result["first"] = {**parsed["first"], "area": area}
        return result
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
        and (field != "word_count" or parsed.get("second") is not None)
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


def _address_record_fields(
    parsed: Mapping[str, Any], *, explicit_word_count: Any = None
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "source_register": parsed["raw"],
        "address_convention": parsed["convention"],
        "address_number": parsed["number"],
        "footnote_marker": parsed["footnote_marker"],
        "address_parse": _address_parse_evidence(parsed),
    }
    if parsed.get("second") is not None:
        fields["word_count"] = parsed["word_count"]
    if explicit_word_count not in (None, ""):
        # Only a printed width or actual consecutive pair supplies source width.
        # A single start address's internal default is not a physical width claim.
        fields["word_count"] = explicit_word_count
        try:
            pair_conflict = (parsed.get("second") is not None
                             and Fraction(str(explicit_word_count)) != parsed["word_count"])
        except (ValueError, ZeroDivisionError):
            pair_conflict = False  # Keep invalid raw counts for normalization holds.
        if pair_conflict:
            # A true consecutive address pair is independent span evidence.
            # Preserve both claims; the envelope must not accept this row.
            fields["code"] = "pdf-address-width-conflict"
    if parsed.get("area") is not None:
        fields["area"] = parsed["area"]
    if parsed.get("source_offset"):
        fields["source_offset"] = parsed["raw"]
    if parsed.get("display_address") is not None:
        fields["display_address"] = parsed["display_address"]
    else:
        fields["source_address"] = {
            "raw": parsed["first"]["raw"],
            "convention": parsed["convention"],
        }
    return fields


def _parse_source_offset(value: Any) -> dict[str, Any] | None:
    """Preserve a bare Offset column without assigning an address convention."""
    parsed = _parse_pdf_address(value)
    if parsed is None or parsed.get("status") != "single":
        return parsed
    raw = str(parsed["raw"])
    number = int(raw) if raw.isdigit() else parsed["number"]
    first = {
        **parsed["first"],
        "raw": raw,
        "number": number,
        "convention": "unknown",
        "area": None,
        "display_address": None,
    }
    return {
        **parsed,
        "first": first,
        "number": number,
        "convention": "unknown",
        "area": None,
        "display_address": None,
        "source_offset": True,
    }


def _clean_values(values: Iterable[Any]) -> list[str]:
    cleaned = (_clean(value) for value in values)
    return [value for value in cleaned if value]


def _header_text(value: Any) -> str:
    normalized = re.sub(r"\s+", " ", _clean(value).casefold()).strip().rstrip(":")
    # A wrapped slash changes spacing, not the role of this exact header.
    # Keep longer prose headers and original raw claims untouched.
    if re.fullmatch(r"read\s*/\s*write", normalized):
        return "read/write"
    return normalized


def _cell(row: Sequence[Any], index: int) -> Any:
    return row[index] if index < len(row) else None


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


__all__ = [
    "PdfTableExtractionError",
    "PdfTableEvidenceBudgetError",
    "extract_pdf_table_evidence",
    "extract_pdf_table_rows",
    "parse_pdf_table",
    "parse_pdf_table_evidence",
    "prepare_pdf_records",
]


def _worker_main(argv: Sequence[str]) -> int:
    if len(argv) not in {3, 4, 5} or argv[1] != "--worker":
        return 2
    pages = [int(value) for value in argv[3].split(",")] if len(argv) >= 4 and argv[3] else None
    partition_pages = [int(value) for value in argv[4].split(",")] if len(argv) == 5 and argv[4] else ()
    try:
        evidence = _extract_pdf_table_rows_in_process(Path(argv[2]), pages=pages,
                                                     cell_partition_pages=partition_pages)
        payload = json.dumps(evidence, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if len(payload) > _MAX_GRID_OUTPUT_BYTES:
            raise PdfTableExtractionError(
                f"grid extraction output exceeds {_MAX_GRID_OUTPUT_BYTES} bytes"
            )
    except PdfTableEvidenceBudgetError as exc:
        print(json.dumps({"error_type": "pdf-grid-evidence-budget/v1", "message": str(exc),
                          "page": exc.page, "table_index": exc.table_index,
                          "stage": exc.stage}), file=sys.stderr)
        return 3
    except (PdfTableExtractionError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the parent boundary
    raise SystemExit(_worker_main(sys.argv))
