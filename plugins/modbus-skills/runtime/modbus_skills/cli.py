"""Dependency-free command line interface for the Modbus skills runtime."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .address import format_modicon_reference, resolve_address
from .analysis import analyze_capture
from .artifacts import (
    ArtifactContractError,
    artifact_envelope,
    assert_artifact_envelope,
    stable_input_hash,
)
from .byte_order import RawSample, evaluate_byte_orders
from .comparison import compare_maps
from .custom_format import render_custom_format, validate_custom_format
from .decisions import apply_review_decisions
from .exporters import Artifact, ExportResult, stable_json
from .map_workflows import (
    diagnose_map,
    lint_map,
    normalize_map,
    review_parse_evidence,
)
from .modpoll import export_modpoll
from .modscan import export_modscan
from .node_red import export_node_red
from .parsers import parse_source
from .read_plan import compile_read_plan
from .tool_pack import ToolPack, build_tool_pack, group_blocking_findings


COMMANDS = (
    "parse-map",
    "extract-pdf",
    "normalize-map",
    "lint-map",
    "diagnose-map",
    "review-evidence",
    "apply-review-decisions",
    "remap-addresses",
    "compare-maps",
    "capture-sample",
    "evaluate-byte-order",
    "compile-read-plan",
    "generate-node-red",
    "generate-modpoll",
    "generate-modscan",
    "build-tool-pack",
    "analyze-capture",
    "infer-custom-format",
)


class CliError(ValueError):
    """A concise user-facing command error."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliError(message)


def _parser(command: str) -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog=f"modbus-skills {command}")
    parser.add_argument("--overwrite", action="store_true", help="replace output files")

    if command in {"parse-map", "normalize-map", "lint-map", "review-evidence", "compile-read-plan", "analyze-capture", "evaluate-byte-order"}:
        parser.add_argument("--input", required=True)
        parser.add_argument("--output", required=True)
    if command == "parse-map":
        parser.add_argument("--format", choices=("csv", "tsv", "psv", "json", "xml", "xlsx"))
        parser.add_argument("--delimiter")
    elif command == "extract-pdf":
        parser.add_argument("--input", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument(
            "--pages",
            help="one contiguous selection using page numbers, commas, and ranges",
        )
        parser.add_argument(
            "--ocr-evidence",
            help="local modbus-ocr-evidence/v1 JSON for selected scanned pages",
        )
    elif command == "normalize-map":
        parser.add_argument("--defaults")
    elif command == "review-evidence":
        parser.add_argument("--lint")
    elif command == "diagnose-map":
        parser.add_argument("--input", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--format", choices=("csv", "tsv", "psv", "json", "xml", "xlsx"))
        parser.add_argument("--delimiter")
        parser.add_argument("--defaults")
    elif command == "apply-review-decisions":
        parser.add_argument("--map", required=True)
        parser.add_argument("--decisions", required=True)
        parser.add_argument("--evidence", action="append", default=[])
        parser.add_argument("--output", required=True)
    elif command == "remap-addresses":
        parser.add_argument("--input", required=True)
        parser.add_argument("--from", dest="source_convention", required=True, choices=("protocol-offset", "one-based-offset", "modicon-reference"))
        parser.add_argument("--to", dest="target_convention", required=True, choices=("protocol-offset", "one-based-offset", "modicon-reference"))
        parser.add_argument("--output", required=True)
    elif command == "compare-maps":
        parser.add_argument("--before", required=True)
        parser.add_argument("--after", required=True)
        parser.add_argument("--fields", help="comma-separated fields")
        parser.add_argument("--output", required=True)
    elif command == "capture-sample":
        parser.add_argument("--request", required=True)
        parser.add_argument("--output", required=True)
    elif command == "evaluate-byte-order":
        parser.add_argument("--types", help="comma-separated data types")
        parser.add_argument("--layouts", help="comma-separated explicit layouts")
        parser.add_argument("--sample-id")
        parser.add_argument("--scale", type=float, default=1.0)
        parser.add_argument("--engineering-offset", type=float, default=0.0)
    elif command == "compile-read-plan":
        parser.add_argument("--max-gap", type=int, default=0)
        parser.add_argument("--max-quantity", action="append", default=[], metavar="AREA=COUNT")
    elif command in {"generate-node-red", "generate-modpoll", "generate-modscan"}:
        parser.add_argument("--map", required=True)
        parser.add_argument("--plan", required=True)
        parser.add_argument("--mode", choices=("probe", "final"), default="final")
        parser.add_argument("--options")
        parser.add_argument("--output", required=True)
        if command == "generate-modpoll":
            parser.add_argument(
                "--profile",
                choices=("gavinying-cli", "witte-desktop", "witte-v12-xml"),
                default="gavinying-cli",
            )
    elif command == "build-tool-pack":
        parser.add_argument("--request", required=True)
        parser.add_argument("--output", required=True)
    elif command == "analyze-capture":
        parser.add_argument("--format", choices=("json", "csv"))
        parser.add_argument("--options")
        parser.add_argument("--now")
    elif command == "infer-custom-format":
        parser.add_argument("--example", required=True)
        parser.add_argument("--map", required=True)
        parser.add_argument("--config")
        parser.add_argument("--output", required=True)
    return parser


def run_cli(command: str, argv: Sequence[str] | None = None) -> int:
    """Run one fixed command. Skill wrappers call this function directly."""

    try:
        if command not in COMMANDS:
            raise CliError(f"unknown command: {command}")
        args = _parser(command).parse_args(list(argv or ()))
        receipt = _HANDLERS[command](args)
        print(stable_json({"command": command, **receipt}), end="")
        return 0
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    values = list(argv if argv is not None else sys.argv[1:])
    if not values or values[0] in {"-h", "--help"}:
        print("usage: modbus-skills COMMAND [OPTIONS]")
        print("commands: " + ", ".join(COMMANDS))
        return 0 if values else 2
    return run_cli(values[0], values[1:])


def _read_bytes(path_value: str) -> tuple[Path, bytes]:
    path = Path(path_value)
    if not path.is_file():
        raise CliError(f"input file does not exist: {path.name}")
    return path, path.read_bytes()


def _read_json(path_value: str, *, label: str = "JSON input") -> Any:
    path, data = _read_bytes(path_value)
    try:
        return json.loads(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise CliError(f"{label} must use UTF-8: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise CliError(f"{label} is invalid at line {exc.lineno}, column {exc.colno}") from exc


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CliError(f"{label} must be a JSON object")
    return value


def _write_file(path: Path, content: bytes, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise CliError(f"output already exists: {path.name}; use --overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _write_json(path_value: str | Path, value: Any, *, overwrite: bool) -> Path:
    path = Path(path_value)
    _write_file(path, stable_json(value).encode("utf-8"), overwrite=overwrite)
    return path


def _safe_targets(root_value: str | Path, artifacts: Sequence[Artifact]) -> list[tuple[Path, Artifact]]:
    root = Path(root_value).resolve()
    targets: list[tuple[Path, Artifact]] = []
    for artifact in sorted(artifacts, key=lambda item: item.path):
        relative = PurePosixPath(artifact.path)
        target = (root / Path(*relative.parts)).resolve()
        if target != root and root not in target.parents:
            raise CliError(f"artifact escapes output directory: {artifact.path}")
        targets.append((target, artifact))
    return targets


def _write_artifacts(root_value: str | Path, artifacts: Sequence[Artifact], *, overwrite: bool) -> None:
    targets = _safe_targets(root_value, artifacts)
    for target, _ in targets:
        if target.exists() and not overwrite:
            raise CliError(f"output already exists: {target.name}; use --overwrite")
    for target, artifact in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(artifact.content)


def _blocking_findings(findings: Sequence[Any]) -> list[Any]:
    return [
        finding
        for finding in findings
        if isinstance(finding, Mapping)
        and str(finding.get("severity", "")).lower() in {"error", "hold"}
    ]


def _write_result(result: ExportResult, root_value: str, *, overwrite: bool) -> None:
    manifest = result.to_manifest()
    artifacts = list(result.artifacts)
    artifacts.append(
        Artifact.text(
            f"{result.target}-result.json",
            "application/json",
            stable_json(manifest),
            "cli-result",
        )
    )
    _write_artifacts(root_value, artifacts, overwrite=overwrite)


def _write_pack(pack: ToolPack, root_value: str, *, overwrite: bool) -> None:
    findings = [
        {"target": result.target, **finding.to_dict()}
        for result in pack.target_results
        for finding in result.findings
    ]
    envelope = artifact_envelope(
        {
            "status": pack.status,
            "mode": pack.mode,
            "targets": [result.to_manifest() for result in pack.target_results],
            "artifacts": [
                artifact.to_manifest()
                for artifact in sorted(pack.artifacts, key=lambda item: item.path)
            ],
            "container": {
                "format": "zip",
                "includes_result_envelope": True,
                "hash_claimed": False,
            },
        },
        schema_version="modbus-tool-pack/v1",
        artifact_type="modbus-tool-pack",
        input_hashes={
            "canonical_map": pack.map_hash,
            "read_plan": pack.read_plan_hash,
        },
        assumptions=[],
        findings=findings,
        holds=group_blocking_findings(findings),
    )
    result_artifact = Artifact.text(
        "tool-pack-result.json",
        "application/json",
        stable_json(envelope),
        "cli-result",
    )
    zip_artifact = Artifact(
        "tool-pack.zip",
        "application/zip",
        pack.to_zip_bytes((result_artifact,)),
        "tool-pack-archive",
    )
    _write_artifacts(
        root_value,
        [*pack.artifacts, zip_artifact, result_artifact],
        overwrite=overwrite,
    )


def _json_options(path_value: str | None, label: str) -> Mapping[str, Any]:
    if not path_value:
        return {}
    return _mapping(_read_json(path_value, label=label), label)


def _handle_parse(args: argparse.Namespace) -> dict[str, Any]:
    path, data = _read_bytes(args.input)
    result = parse_source(data, source_format=args.format, filename=path.name, delimiter=args.delimiter)
    output = artifact_envelope(
        result,
        schema_version="candidate-map/v1",
        inputs={
            "source": data,
            "parse_options": {
                "format": args.format,
                "delimiter": args.delimiter,
            },
        },
        findings=list(result.get("warnings", ())),
        holds=[],
    )
    _write_json(args.output, output, overwrite=args.overwrite)
    return {"status": "parsed", "records": len(output["records"]), "output": Path(args.output).name}


_PDF_HEADER_ALIASES = {
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
    "unit id": "unit_id",
    "word count": "word_count",
    "byte order": "byte_order",
}


def _pdf_header(line: str) -> list[str] | None:
    raw = re.split(r"\s{2,}", line.strip())
    if len(raw) < 2:
        return None
    names = [_PDF_HEADER_ALIASES.get(re.sub(r"\s+", " ", item.strip().lower())) for item in raw]
    if None in names or not ({"address", "protocol_offset", "display_address"} & set(names)):
        return None
    return [str(name) for name in names]


def _structured_pdf_rows(
    text: str, *, first_page: int = 1
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for page_number, page in enumerate(text.split("\f"), start=first_page):
        header: list[str] | None = None
        for line_number, line in enumerate(page.splitlines(), start=1):
            candidate_header = _pdf_header(line)
            if candidate_header is not None:
                header = candidate_header
                continue
            if header is None or not line.strip():
                continue
            parts = re.split(r"\s{2,}", line.strip())
            if len(parts) != len(header):
                continue
            record = dict(zip(header, (value.strip() for value in parts), strict=True))
            address = record.get("address", record.get("protocol_offset", record.get("display_address")))
            if not re.fullmatch(r"(?:0[xX][0-9A-Fa-f]+|\d+)", str(address or "")):
                rejected.append({"code": "pdf-row-address-invalid", "page": page_number, "line": line_number})
                continue
            record["_source"] = {
                "format": "pdf",
                "page": page_number,
                "line": line_number,
                "excerpt": line.strip()[:300],
            }
            records.append(record)
    return records, rejected


_PDF_PAGE_TOKEN = re.compile(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?")
_MAX_PDF_PAGE = 100_000
_MAX_PDF_PAGE_SPAN = 256
_MAX_OCR_EVIDENCE_BYTES = 10_000_000
_MAX_OCR_PAGE_TEXT_BYTES = 1_000_000


def _pdf_page_range(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    if not value or any(character.isspace() for character in value):
        raise CliError("--pages must use comma-separated page numbers or ranges without spaces")
    selected: set[int] = set()
    selected_count = 0
    for token in value.split(","):
        match = _PDF_PAGE_TOKEN.fullmatch(token)
        if match is None:
            raise CliError("--pages must use syntax such as 42-48 or 42,43-48")
        first = int(match.group(1))
        last = int(match.group(2) or first)
        if last < first:
            raise CliError("--pages ranges must increase")
        if last > _MAX_PDF_PAGE:
            raise CliError(f"--pages must not exceed {_MAX_PDF_PAGE}")
        span = last - first + 1
        if span > _MAX_PDF_PAGE_SPAN:
            raise CliError(
                f"--pages can select at most {_MAX_PDF_PAGE_SPAN} contiguous pages"
            )
        selected_count += span
        selected.update(range(first, last + 1))
    if len(selected) != selected_count:
        raise CliError("--pages must not contain duplicate or overlapping pages")
    first = min(selected)
    last = max(selected)
    if len(selected) != last - first + 1:
        raise CliError("--pages must resolve to one contiguous range")
    if len(selected) > _MAX_PDF_PAGE_SPAN:
        raise CliError(
            f"--pages can select at most {_MAX_PDF_PAGE_SPAN} contiguous pages"
        )
    return first, last


def _ocr_text_rows(
    value: Mapping[str, Any],
    *,
    source_sha256: str,
    page_range: tuple[int, int] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    if page_range is None:
        raise CliError("--ocr-evidence requires an explicit bounded --pages selection")
    try:
        assert_artifact_envelope(value)
    except ArtifactContractError as exc:
        raise CliError(f"OCR evidence common envelope is invalid: {exc}") from exc
    if value.get("schema_version") != "modbus-ocr-evidence/v1":
        raise CliError("OCR evidence must use schema_version modbus-ocr-evidence/v1")
    if value.get("artifact_type") != "modbus-ocr-evidence":
        raise CliError("OCR evidence artifact_type must be modbus-ocr-evidence")
    input_hashes = value["input_hashes"]
    if input_hashes.get("source_pdf") != source_sha256:
        raise CliError("OCR evidence input_hashes.source_pdf does not match the input PDF")
    if str(value.get("source_sha256", "")).lower() != source_sha256:
        raise CliError("OCR evidence source_sha256 does not match the input PDF")

    raw_tool = value.get("tool")
    if not isinstance(raw_tool, Mapping):
        raise CliError("OCR evidence tool must contain name and version")
    tool_name = str(raw_tool.get("name", "")).strip()
    tool_version = str(raw_tool.get("version", "")).strip()
    if not tool_name or not tool_version:
        raise CliError("OCR evidence tool name and version must be non-empty")
    if len(tool_name) > 100 or len(tool_version) > 100:
        raise CliError("OCR evidence tool name and version must be at most 100 characters")

    raw_pages = value.get("pages")
    if not isinstance(raw_pages, Sequence) or isinstance(
        raw_pages, (str, bytes, bytearray)
    ) or not raw_pages:
        raise CliError("OCR evidence pages must be a non-empty array")

    expected_pages = set(range(page_range[0], page_range[1] + 1))
    supplied_pages: set[int] = set()
    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, raw_page in enumerate(raw_pages):
        if not isinstance(raw_page, Mapping):
            raise CliError(f"OCR evidence pages[{index}] must be an object")
        page_index = raw_page.get("page_index")
        if isinstance(page_index, bool) or not isinstance(page_index, int):
            raise CliError(f"OCR evidence pages[{index}].page_index must be an integer")
        if page_index not in expected_pages:
            raise CliError(
                f"OCR evidence page {page_index} is outside the selected PDF page range"
            )
        if page_index in supplied_pages:
            raise CliError(f"OCR evidence page {page_index} is duplicated")
        supplied_pages.add(page_index)

        text = raw_page.get("text")
        if not isinstance(text, str) or not text.strip():
            raise CliError(f"OCR evidence page {page_index} text must be non-empty")
        if "\f" in text:
            raise CliError(
                f"OCR evidence page {page_index} text must not contain page separators"
            )
        if len(text.encode("utf-8")) > _MAX_OCR_PAGE_TEXT_BYTES:
            raise CliError(
                f"OCR evidence page {page_index} text exceeds {_MAX_OCR_PAGE_TEXT_BYTES} bytes"
            )

        printed_label_value = raw_page.get("printed_page_label")
        printed_label = (
            None
            if printed_label_value is None
            else str(printed_label_value).strip()
        )
        if printed_label is not None and len(printed_label) > 100:
            raise CliError(
                f"OCR evidence page {page_index} printed label must be at most 100 characters"
            )
        page_records, page_rejected = _structured_pdf_rows(
            text, first_page=page_index
        )
        for record in page_records:
            source = dict(record.get("_source", {}))
            source.update(
                {
                    "method": "ocr-derived",
                    "printed_page_label": printed_label,
                    "ocr_tool": {
                        "name": tool_name,
                        "version": tool_version,
                    },
                }
            )
            record["_source"] = source
        for item in page_rejected:
            item.update(
                {
                    "method": "ocr-derived",
                    "printed_page_label": printed_label,
                }
            )
        records.extend(page_records)
        rejected.extend(page_rejected)

    if supplied_pages != expected_pages:
        missing = sorted(expected_pages - supplied_pages)
        raise CliError(
            "OCR evidence must contain each selected page exactly once; missing: "
            + ", ".join(str(page) for page in missing)
        )
    return records, rejected, {"name": tool_name, "version": tool_version}


def _handle_pdf(args: argparse.Namespace) -> dict[str, Any]:
    path, data = _read_bytes(args.input)
    page_range = _pdf_page_range(args.pages)
    source_sha256 = hashlib.sha256(data).hexdigest()
    holds: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    ocr_evidence: Mapping[str, Any] | None = None
    ocr_tool: dict[str, str] | None = None
    if args.ocr_evidence:
        _, ocr_bytes = _read_bytes(args.ocr_evidence)
        if len(ocr_bytes) > _MAX_OCR_EVIDENCE_BYTES:
            raise CliError(
                f"OCR evidence exceeds {_MAX_OCR_EVIDENCE_BYTES} bytes"
            )
        ocr_evidence = _mapping(
            _read_json(args.ocr_evidence, label="OCR evidence"), "OCR evidence"
        )
        records, rejected, ocr_tool = _ocr_text_rows(
            ocr_evidence,
            source_sha256=source_sha256,
            page_range=page_range,
        )
        if records:
            holds.append(
                {
                    "code": "pdf-ocr-human-review-required",
                    "severity": "hold",
                    "blocking": True,
                    "message": "Review every OCR-derived candidate against its source page before normalization.",
                }
            )
        else:
            holds.append(
                {
                    "code": "pdf-ocr-structured-rows-unavailable",
                    "severity": "hold",
                    "blocking": True,
                    "message": "OCR evidence was supplied, but no strict structured register rows were found.",
                }
            )
    else:
        executable = shutil.which("pdftotext")
        if executable is None:
            holds.append({"code": "pdf-text-extractor-unavailable", "severity": "hold", "blocking": True, "message": "Install pdftotext or provide a rights-safe OCR evidence file for deterministic extraction."})
        else:
            command = [executable, "-layout", "-enc", "UTF-8"]
            if page_range is not None:
                command.extend(["-f", str(page_range[0]), "-l", str(page_range[1])])
            command.extend([str(path), "-"])
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=60,
                )
            except subprocess.TimeoutExpired:
                completed = None
                holds.append({"code": "pdf-text-extraction-timeout", "severity": "hold", "blocking": True, "message": "PDF text extraction exceeded the 60 second limit."})
            if completed is not None:
                if completed.returncode != 0:
                    holds.append({"code": "pdf-text-extraction-failed", "severity": "hold", "blocking": True, "message": "pdftotext could not extract this document."})
                else:
                    text = completed.stdout.decode("utf-8", errors="replace")
                    records, rejected = _structured_pdf_rows(
                        text,
                        first_page=page_range[0] if page_range is not None else 1,
                    )
                    if not text.strip():
                        holds.append({"code": "pdf-ocr-required", "severity": "hold", "blocking": True, "message": "The selected PDF pages contain no extractable text. Supply a rights-safe local modbus-ocr-evidence/v1 file; this command will not invent register rows."})
                    elif not records:
                        holds.append({"code": "pdf-structured-rows-unavailable", "severity": "hold", "blocking": True, "message": "Text was extracted, but no strict structured register rows were found. Review the manual or provide a table export."})
                    else:
                        holds.append({"code": "pdf-human-review-required", "severity": "hold", "blocking": True, "message": "Review every PDF candidate against its source page before normalization."})
    result = artifact_envelope({
        "status": "held" if holds else "candidate",
        "source": {"filename": path.name, "sha256": source_sha256},
        "page_selection": (
            {"first_page": page_range[0], "last_page": page_range[1]}
            if page_range is not None
            else None
        ),
        "ocr_tool": ocr_tool,
        "records": records,
        "rejected_rows": rejected,
        "warnings": [],
    },
        schema_version="modbus-pdf-extraction/v1",
        inputs={
            "source": data,
            "ocr_evidence": ocr_evidence,
            "page_selection": (
                {"first_page": page_range[0], "last_page": page_range[1]}
                if page_range is not None
                else None
            ),
        },
        assumptions=[],
        findings=[],
        holds=holds,
    )
    artifact = Artifact.text("pdf-extraction.json", "application/json", stable_json(result), "pdf-extraction")
    _write_artifacts(args.output, [artifact], overwrite=args.overwrite)
    return {"status": result["status"], "records": len(records), "output": Path(args.output).name}


def _handle_normalize(args: argparse.Namespace) -> dict[str, Any]:
    source = _read_json(args.input)
    defaults = _json_options(args.defaults, "defaults")
    raw_result = normalize_map(source, defaults=defaults)
    result = artifact_envelope(
        raw_result,
        schema_version="modbus-map/v1",
        inputs={"candidate_map": source, "defaults": defaults},
        findings=list(raw_result.get("warnings", ())),
        holds=list(raw_result.get("holds", ())),
    )
    _write_json(args.output, result, overwrite=args.overwrite)
    return {"status": "normalized", "points": len(result["points"]), "holds": len(result["holds"]), "output": Path(args.output).name}


def _handle_lint(args: argparse.Namespace) -> dict[str, Any]:
    canonical = _read_json(args.input)
    raw_result = lint_map(canonical)
    findings = list(raw_result.get("findings", ()))
    result = artifact_envelope(
        raw_result,
        schema_version="modbus-map-lint/v1",
        inputs={"canonical_map": canonical},
        assumptions=[],
        findings=findings,
        holds=_blocking_findings(findings),
    )
    _write_json(args.output, result, overwrite=args.overwrite)
    return {"status": "linted", "blocking": result["summary"]["blocking"], "output": Path(args.output).name}


def _handle_diagnose(args: argparse.Namespace) -> dict[str, Any]:
    path, data = _read_bytes(args.input)
    defaults = _json_options(args.defaults, "defaults")
    result = diagnose_map(data, source_format=args.format, filename=path.name, delimiter=args.delimiter, defaults=defaults)
    parsed_raw = result["parsed"]
    parsed = artifact_envelope(
        parsed_raw,
        schema_version="candidate-map/v1",
        inputs={
            "source": data,
            "parse_options": {"format": args.format, "delimiter": args.delimiter},
        },
        findings=list(parsed_raw.get("warnings", ())),
        holds=[],
    )
    canonical_raw = result["canonical_map"]
    canonical = artifact_envelope(
        canonical_raw,
        schema_version="modbus-map/v1",
        inputs={"candidate_map": parsed, "defaults": defaults},
        findings=list(canonical_raw.get("warnings", ())),
        holds=list(canonical_raw.get("holds", ())),
    )
    lint_raw = result["lint"]
    lint_findings = list(lint_raw.get("findings", ()))
    lint = artifact_envelope(
        lint_raw,
        schema_version="modbus-map-lint/v1",
        inputs={"canonical_map": canonical},
        assumptions=[],
        findings=lint_findings,
        holds=_blocking_findings(lint_findings),
    )
    review_raw = result["review"]
    review_findings = [
        *lint_findings,
        *list(review_raw.get("global_findings", ())),
    ]
    review = artifact_envelope(
        review_raw,
        schema_version="modbus-map-evidence-review/v1",
        inputs={"canonical_map": canonical, "lint": lint},
        findings=review_findings,
        holds=_blocking_findings(review_findings),
    )
    artifacts = [
        Artifact.text("parsed.json", "application/json", stable_json(parsed), "parsed-map"),
        Artifact.text("map-draft.json", "application/json", stable_json(canonical), "canonical-map-draft"),
        Artifact.text("lint.json", "application/json", stable_json(lint), "map-lint"),
        Artifact.text("review.json", "application/json", stable_json(review), "evidence-review"),
    ]
    _write_artifacts(args.output, artifacts, overwrite=args.overwrite)
    return {"status": review["review_status"], "points": len(canonical["points"]), "output": Path(args.output).name}


def _handle_review(args: argparse.Namespace) -> dict[str, Any]:
    canonical = _mapping(_read_json(args.input), "map")
    lint = (
        _mapping(_read_json(args.lint, label="lint result"), "lint result")
        if args.lint
        else lint_map(canonical)
    )
    raw_result = review_parse_evidence(canonical, lint_result=lint)
    findings = [
        *list(lint.get("findings", ())),
        *list(raw_result.get("global_findings", ())),
    ]
    result = artifact_envelope(
        raw_result,
        schema_version="modbus-map-evidence-review/v1",
        inputs={"canonical_map": canonical, "lint": lint},
        findings=findings,
        holds=_blocking_findings(findings),
    )
    _write_json(args.output, result, overwrite=args.overwrite)
    return {"status": result["review_status"], "items": len(result["items"]), "output": Path(args.output).name}


def _handle_decisions(args: argparse.Namespace) -> dict[str, Any]:
    canonical = _mapping(_read_json(args.map, label="map"), "map")
    decisions = _mapping(
        _read_json(args.decisions, label="review decisions"),
        "review decisions",
    )
    evidence_artifacts: dict[str, Mapping[str, Any]] = {}
    for path in args.evidence:
        evidence = _mapping(
            _read_json(path, label="review evidence"), "review evidence"
        )
        evidence_artifacts[stable_input_hash(evidence)] = evidence
    raw_result = apply_review_decisions(
        canonical,
        decisions,
        evidence_artifacts=evidence_artifacts,
    )
    result = artifact_envelope(
        raw_result,
        schema_version="modbus-map/v1",
        artifact_type="modbus-map",
        inputs={"canonical_map_draft": canonical, "review_decisions": decisions},
        assumptions=list(raw_result.get("assumptions", ())),
        findings=list(raw_result.get("findings", ())),
        holds=list(raw_result.get("holds", ())),
    )
    _write_json(args.output, result, overwrite=args.overwrite)
    return {
        "status": result["review_status"],
        "points": len(result["points"]),
        "excluded": len(result.get("excluded_points", ())),
        "holds": len(result["holds"]),
        "output": Path(args.output).name,
    }


def _map_points(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        raw = value.get("points", value.get("records", value.get("registers")))
    else:
        raw = value
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)) or any(not isinstance(item, Mapping) for item in raw):
        raise CliError("map must contain a points, records, or registers array")
    return list(raw)


def _source_raw(point: Mapping[str, Any], convention: str) -> Any:
    source = point.get("source_address")
    if isinstance(source, Mapping) and source.get("raw") not in (None, ""):
        return source.get("raw")
    if convention == "protocol-offset":
        return point.get("protocol_offset", point.get("address"))
    if convention == "modicon-reference":
        return point.get("display_address", point.get("address"))
    return point.get("address", point.get("protocol_offset"))


def _handle_remap(args: argparse.Namespace) -> dict[str, Any]:
    source_map = _read_json(args.input)
    points = _map_points(source_map)
    conversions: list[dict[str, Any]] = []
    collision_index: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    for index, point in enumerate(points):
        identifier = str(point.get("logical_point_id", point.get("point_id", f"point-{index + 1}")))
        area = point.get("area")
        raw = _source_raw(point, args.source_convention)
        resolution = resolve_address(raw, args.source_convention, area)
        target: int | str | None = None
        findings = [finding.to_dict() for finding in resolution.findings]
        if resolution.resolved:
            assert resolution.protocol_offset is not None
            if args.target_convention == "protocol-offset":
                target = resolution.protocol_offset
            elif args.target_convention == "one-based-offset":
                target = resolution.protocol_offset + 1
            else:
                target = format_modicon_reference(resolution.area, resolution.protocol_offset)
            key = (point.get("route_id"), point.get("unit_id"), resolution.area.value, str(target))
            collision_index[key].append(identifier)
        conversions.append({
            "logical_point_id": identifier,
            "area": area,
            "source": {"value": raw, "convention": args.source_convention},
            "protocol_offset": resolution.protocol_offset,
            "target": {"value": target, "convention": args.target_convention},
            "status": "converted" if resolution.resolved else "held",
            "findings": findings,
        })
    collisions = [
        {"route_id": key[0], "unit_id": key[1], "area": key[2], "target_value": key[3], "point_ids": sorted(ids)}
        for key, ids in sorted(collision_index.items(), key=lambda item: tuple(str(value) for value in item[0]))
        if len(ids) > 1
    ]
    findings = [
        {"logical_point_id": item["logical_point_id"], **finding}
        for item in conversions
        for finding in item["findings"]
    ]
    collision_holds = [
        {
            "code": "address-remap-collision",
            "severity": "hold",
            "blocking": True,
            "message": "Two or more points resolve to the same target identity.",
            "details": collision,
        }
        for collision in collisions
    ]
    raw_result = {
        "contract": "modbus-address-remap-preview/v1",
        "source_convention": args.source_convention,
        "target_convention": args.target_convention,
        "status": "held" if any(item["status"] == "held" for item in conversions) or collisions else "ready-for-review",
        "conversions": conversions,
        "collisions": collisions,
    }
    result = artifact_envelope(
        raw_result,
        schema_version="modbus-address-remap-preview/v1",
        inputs={
            "canonical_map": source_map,
            "remap_options": {
                "source_convention": args.source_convention,
                "target_convention": args.target_convention,
            },
        },
        assumptions=[],
        findings=findings,
        holds=[*_blocking_findings(findings), *collision_holds],
    )
    _write_json(args.output, result, overwrite=args.overwrite)
    return {"status": result["status"], "points": len(conversions), "output": Path(args.output).name}


def _handle_compare(args: argparse.Namespace) -> dict[str, Any]:
    fields = _split_values(args.fields) if args.fields else None
    before = _read_json(args.before)
    after = _read_json(args.after)
    raw_result = compare_maps(before, after, compare_fields=fields)
    holds = [
        {
            "code": "map-comparison-identity-ambiguous",
            "severity": "hold",
            "blocking": True,
            "message": "A composite point identity is duplicated in one or both maps.",
            "details": duplicate,
        }
        for duplicate in raw_result.get("duplicates", ())
    ]
    result = artifact_envelope(
        raw_result,
        schema_version="modbus-map-diff/v1",
        inputs={
            "before_map": before,
            "after_map": after,
            "comparison_options": {"fields": list(fields) if fields else None},
        },
        assumptions=[],
        findings=[],
        holds=holds,
    )
    _write_json(args.output, result, overwrite=args.overwrite)
    return {"status": "compared", **result["summary"], "output": Path(args.output).name}


def _request_value(value: Any, base: Path, label: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        path = Path(value)
        if not path.is_absolute():
            path = base / path
        return _mapping(_read_json(str(path), label=label), label)
    raise CliError(f"{label} must be an object or JSON file path")


def _probe_map(request: Mapping[str, Any], base: Path) -> Mapping[str, Any]:
    supplied = request.get("canonical_map", request.get("map"))
    if isinstance(supplied, Mapping):
        return supplied
    if isinstance(supplied, str):
        return _request_value(supplied, base, "canonical map")
    points = request.get("points")
    if points is None and any(key in request for key in ("protocol_offset", "address", "display_address")):
        points = [{key: value for key, value in request.items() if key not in {"target", "targets", "target_options", "mode", "max_gap"}}]
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes, bytearray)):
        raise CliError("probe request must contain points or canonical_map")
    return normalize_map({"records": points}, defaults=_mapping(request.get("defaults", {}), "defaults"))


def _capture_plannable_points(
    canonical_map: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    """Exclude points whose source evidence does not permit a read probe."""

    points = _map_points(canonical_map)
    blocked_ids: set[str] = set()
    global_access_hold = False
    raw_holds = canonical_map.get("holds", ())
    if isinstance(raw_holds, Sequence) and not isinstance(
        raw_holds, (str, bytes, bytearray)
    ):
        for hold in raw_holds:
            if not isinstance(hold, Mapping) or hold.get("blocking", True) is False:
                continue
            code = str(hold.get("code", "")).lower()
            field = str(hold.get("field", "")).lower()
            access_hold = (
                field in {"access", "access_readable", "access_writable"}
                or "access" in code
                or "not-readable" in code
                or "write-only" in code
            )
            if not access_hold:
                continue
            point_ids = hold.get("point_ids", ())
            if isinstance(point_ids, Sequence) and not isinstance(
                point_ids, (str, bytes, bytearray)
            ) and point_ids:
                blocked_ids.update(str(value) for value in point_ids)
            else:
                global_access_hold = True

    if global_access_hold:
        return []

    plannable: list[Mapping[str, Any]] = []
    for index, point in enumerate(points):
        identifier = str(
            point.get(
                "logical_point_id",
                point.get("point_id", point.get("id", f"point-{index + 1}")),
            )
        )
        access_value = point.get("access")
        access = (
            str(access_value)
            .strip()
            .lower()
            .replace("_", "-")
            .replace(" ", "-")
            if access_value not in (None, "")
            else None
        )
        if (
            identifier in blocked_ids
            or point.get("source_include") is False
            or access == "write-only"
            or (
                access_value not in (None, "")
                and access not in {"read-only", "read-write"}
            )
        ):
            continue
        plannable.append(point)
    return plannable


def _ensure_workflow_envelope(
    value: Mapping[str, Any], schema_version: str
) -> Mapping[str, Any]:
    findings = list(value.get("findings", ()))
    holds = list(value.get("holds", ())) or _blocking_findings(findings)
    existing_hashes = value.get("input_hashes")
    hash_arguments: dict[str, Any]
    if isinstance(existing_hashes, Mapping):
        hash_arguments = {"input_hashes": existing_hashes}
    else:
        hash_arguments = {"inputs": {"provided_artifact": value}}
    return artifact_envelope(
        value,
        schema_version=schema_version,
        assumptions=list(value.get("assumptions", ())),
        findings=findings,
        holds=holds,
        **hash_arguments,
    )


def _handle_capture(args: argparse.Namespace) -> dict[str, Any]:
    request_path = Path(args.request)
    request = _mapping(_read_json(args.request, label="probe request"), "probe request")
    canonical = _ensure_workflow_envelope(
        _probe_map(request, request_path.parent), "modbus-map/v1"
    )
    plan_value = request.get("read_plan", request.get("plan"))
    if plan_value is not None:
        plan = _request_value(plan_value, request_path.parent, "read plan")
        plan = _ensure_workflow_envelope(plan, "modbus-read-plan/v1")
    else:
        max_gap = int(request.get("max_gap", 0))
        raw_plan = compile_read_plan(
            _capture_plannable_points(canonical),
            max_gap=max_gap,
        ).to_dict()
        planning_options = {"max_gap": max_gap, "max_quantities": {}}
        raw_plan["planning_options"] = planning_options
        plan_findings = list(raw_plan.get("findings", ()))
        plan = artifact_envelope(
            raw_plan,
            schema_version="modbus-read-plan/v1",
            inputs={
                "canonical_map": canonical,
                "planning_options": planning_options,
            },
            assumptions=[],
            findings=plan_findings,
            holds=_blocking_findings(plan_findings),
        )
    raw_targets = request.get("targets", [request.get("target")])
    if isinstance(raw_targets, str):
        raw_targets = [raw_targets]
    targets, target_options = _target_request(
        raw_targets,
        request.get("target_options", {}),
    )
    if request.get("profile") and "modpoll" in targets:
        current_profile = target_options.get("modpoll", {}).get("profile")
        if current_profile is not None and current_profile != request["profile"]:
            raise CliError("probe request contains conflicting modpoll profiles")
        target_options["modpoll"] = {
            **dict(target_options.get("modpoll", {})),
            "profile": request["profile"],
        }
    pack = build_tool_pack(canonical, plan, targets=targets, mode="probe", target_options=target_options)
    _write_pack(pack, args.output, overwrite=args.overwrite)
    return {"status": pack.status, "targets": targets, "output": Path(args.output).name}


def _parse_word(value: Any) -> int:
    if isinstance(value, bool):
        raise CliError("sample words must be integers")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    try:
        return int(text, 0) if text.lower().startswith(("0x", "+0x", "-0x")) else int(text, 10)
    except ValueError as exc:
        raise CliError(f"invalid sample word: {value!r}") from exc


def _raw_sample(
    value: Any, requested_id: str | None
) -> tuple[RawSample, Mapping[str, Any]]:
    candidate: Any = value
    if isinstance(value, Mapping) and isinstance(value.get("sample"), Mapping):
        candidate = value["sample"]
    elif isinstance(value, Mapping) and isinstance(value.get("samples"), Sequence):
        candidates = [item for item in value["samples"] if isinstance(item, Mapping) and any(key in item for key in ("words", "raw_words", "registers"))]
        if requested_id:
            candidates = [item for item in candidates if str(item.get("sample_id", item.get("id", ""))) == requested_id]
        if len(candidates) != 1:
            raise CliError("capture must contain exactly one matching raw-word sample; use --sample-id")
        candidate = candidates[0]
    if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes, bytearray)):
        words = candidate
        sample_id = requested_id or "sample-001"
        candidate_mapping: Mapping[str, Any] = {}
    elif isinstance(candidate, Mapping):
        words = candidate.get("words", candidate.get("raw_words", candidate.get("registers")))
        declared_id_value = candidate.get("sample_id", candidate.get("id"))
        declared_id = (
            str(declared_id_value).strip()
            if declared_id_value not in (None, "")
            else None
        )
        if requested_id and declared_id and requested_id != declared_id:
            raise CliError(
                "--sample-id does not match the sample_id in the capture"
            )
        sample_id = requested_id or declared_id or "sample-001"
        candidate_mapping = candidate
    else:
        raise CliError("input must contain raw words")
    if not isinstance(words, Sequence) or isinstance(words, (str, bytes, bytearray)):
        raise CliError("sample words must be an array")
    sample = RawSample(sample_id, tuple(_parse_word(word) for word in words))
    identity = _raw_sample_identity(value, candidate_mapping, sample.sample_id)
    return sample, identity


def _raw_sample_identity(
    capture: Any,
    candidate: Mapping[str, Any],
    sample_id: str,
) -> Mapping[str, Any]:
    point_id_value = candidate.get(
        "point_id", candidate.get("logical_point_id", candidate.get("tag_id"))
    )
    point_id = str(point_id_value).strip() if point_id_value not in (None, "") else None
    point_config: Mapping[str, Any] = {}
    if point_id and isinstance(capture, Mapping):
        raw_points = capture.get("points", ())
        if isinstance(raw_points, Sequence) and not isinstance(
            raw_points, (str, bytes, bytearray)
        ):
            matches = [
                point
                for point in raw_points
                if isinstance(point, Mapping)
                and str(
                    point.get(
                        "point_id",
                        point.get("logical_point_id", point.get("tag_id", "")),
                    )
                )
                == point_id
            ]
            if len(matches) == 1:
                point_config = matches[0]

    def value(*keys: str) -> Any:
        for source in (candidate, point_config, capture if isinstance(capture, Mapping) else {}):
            for key in keys:
                if source.get(key) not in (None, ""):
                    return source.get(key)
        return None

    protocol_offset = value("protocol_offset", "pdu_offset")
    if protocol_offset is None:
        for source in (candidate, point_config):
            address = source.get("address")
            if isinstance(address, Mapping) and address.get("protocol_offset") not in (None, ""):
                protocol_offset = address.get("protocol_offset")
                break
    return {
        "sample_id": sample_id,
        "point_id": point_id,
        "route_id": value("route_id", "route"),
        "unit_id": value("unit_id", "slave_id"),
        "area": value("area", "register_area"),
        "protocol_offset": protocol_offset,
        "timestamp": value("timestamp", "captured_at"),
    }


def _split_values(value: str | None) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def _handle_byte_order(args: argparse.Namespace) -> dict[str, Any]:
    capture = _read_json(args.input)
    sample, sample_identity = _raw_sample(capture, args.sample_id)
    raw_result = evaluate_byte_orders(
        sample,
        datatypes=_split_values(args.types) or None,
        layouts=_split_values(args.layouts) or None,
        scale=args.scale,
        engineering_offset=args.engineering_offset,
    ).to_dict()
    raw_result["sample_identity"] = dict(sample_identity)
    missing_identity = [
        field
        for field in (
            "point_id",
            "route_id",
            "unit_id",
            "area",
            "protocol_offset",
            "timestamp",
        )
        if sample_identity.get(field) in (None, "")
    ]
    holds = []
    if missing_identity:
        holds.append(
            {
                "code": "byte-order-sample-identity-incomplete",
                "severity": "hold",
                "blocking": True,
                "message": "Identify the sampled point before applying a byte-order decision.",
                "details": {"missing_fields": missing_identity},
            }
        )
    holds.append(
        {
            "code": "byte-order-human-confirmation-required",
            "severity": "hold",
            "blocking": True,
            "message": "The candidates are evidence. A human must confirm one layout before final generation.",
        }
    )
    result = artifact_envelope(
        raw_result,
        schema_version="modbus-byte-order-evidence/v1",
        inputs={
            "capture": capture,
            "evaluation_options": {
                "types": list(_split_values(args.types)),
                "layouts": list(_split_values(args.layouts)),
                "sample_id": args.sample_id,
                "scale": args.scale,
                "engineering_offset": args.engineering_offset,
            },
        },
        assumptions=[],
        findings=[],
        holds=holds,
    )
    _write_json(args.output, result, overwrite=args.overwrite)
    return {"status": "evaluated", "candidates": len(result["candidates"]), "output": Path(args.output).name}


def _max_quantities(values: Sequence[str]) -> Mapping[str, int] | None:
    if not values:
        return None
    result: dict[str, int] = {}
    for value in values:
        if "=" not in value:
            raise CliError("--max-quantity must use AREA=COUNT")
        area, count = value.split("=", 1)
        try:
            result[area.strip()] = int(count)
        except ValueError as exc:
            raise CliError("--max-quantity count must be an integer") from exc
    return result


def _handle_plan(args: argparse.Namespace) -> dict[str, Any]:
    canonical = _read_json(args.input)
    max_quantities = _max_quantities(args.max_quantity)
    points = _map_points(canonical)
    source_holds = canonical.get("holds", ()) if isinstance(canonical, Mapping) else ()
    if not isinstance(source_holds, Sequence) or isinstance(
        source_holds, (str, bytes, bytearray)
    ):
        source_holds = ()
    probe_safe_fields = {"datatype", "byte_order", "byte_order_confirmed"}
    blocked_point_ids: set[str] = set()
    global_blocking = False
    for hold in source_holds:
        if not isinstance(hold, Mapping) or hold.get("blocking", True) is False:
            continue
        if hold.get("field") in probe_safe_fields:
            continue
        point_ids = hold.get("point_ids", ())
        if isinstance(point_ids, Sequence) and not isinstance(
            point_ids, (str, bytes, bytearray)
        ) and point_ids:
            blocked_point_ids.update(str(value) for value in point_ids)
        else:
            global_blocking = True
    if global_blocking:
        plannable_points: list[Mapping[str, Any]] = []
    else:
        plannable_points = [
            point
            for index, point in enumerate(points)
            if str(
                point.get(
                    "logical_point_id",
                    point.get("point_id", point.get("id", f"point-{index + 1}")),
                )
            )
            not in blocked_point_ids
        ]
    raw_result = compile_read_plan(
        plannable_points,
        max_gap=args.max_gap,
        max_quantities=max_quantities,
    ).to_dict()
    planning_options = {
        "max_gap": args.max_gap,
        "max_quantities": dict(max_quantities or {}),
    }
    raw_result["planning_options"] = planning_options
    findings = list(raw_result.get("findings", ()))
    for hold in source_holds:
        if isinstance(hold, Mapping) and hold.get("blocking", True) is not False:
            candidate = dict(hold)
            key = (
                candidate.get("code"),
                candidate.get("field"),
                tuple(str(value) for value in candidate.get("point_ids", ()))
                if isinstance(candidate.get("point_ids", ()), Sequence)
                and not isinstance(candidate.get("point_ids", ()), (str, bytes, bytearray))
                else (),
            )
            existing_keys = {
                (
                    item.get("code"),
                    item.get("field"),
                    tuple(str(value) for value in item.get("point_ids", ()))
                    if isinstance(item.get("point_ids", ()), Sequence)
                    and not isinstance(item.get("point_ids", ()), (str, bytes, bytearray))
                    else (),
                )
                for item in findings
                if isinstance(item, Mapping)
            }
            if key not in existing_keys:
                findings.append(candidate)
    raw_result["findings"] = findings
    raw_result["has_holds"] = bool(_blocking_findings(findings))
    raw_result["status"] = "held" if raw_result["has_holds"] else "planned"
    result = artifact_envelope(
        raw_result,
        schema_version="modbus-read-plan/v1",
        inputs={
            "canonical_map": canonical,
            "planning_options": planning_options,
        },
        assumptions=[],
        findings=findings,
        holds=_blocking_findings(findings),
    )
    _write_json(args.output, result, overwrite=args.overwrite)
    return {"status": result["status"], "requests": len(result["requests"]), "output": Path(args.output).name}


def _export_inputs(args: argparse.Namespace) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    return (
        _mapping(_read_json(args.map), "canonical map"),
        _mapping(_read_json(args.plan), "read plan"),
        _json_options(args.options, "export options"),
    )


def _handle_node_red(args: argparse.Namespace) -> dict[str, Any]:
    canonical, plan, options = _export_inputs(args)
    result = export_node_red(canonical, plan, mode=args.mode, options=options)
    _write_result(result, args.output, overwrite=args.overwrite)
    return {"status": result.status, "target": result.target, "output": Path(args.output).name}


def _handle_modpoll(args: argparse.Namespace) -> dict[str, Any]:
    canonical, plan, options = _export_inputs(args)
    result = export_modpoll(canonical, plan, mode=args.mode, profile=args.profile, options=options)
    _write_result(result, args.output, overwrite=args.overwrite)
    return {"status": result.status, "target": result.target, "profile": result.profile, "output": Path(args.output).name}


def _handle_modscan(args: argparse.Namespace) -> dict[str, Any]:
    canonical, plan, options = _export_inputs(args)
    result = export_modscan(canonical, plan, mode=args.mode, options=options)
    _write_result(result, args.output, overwrite=args.overwrite)
    return {"status": result.status, "target": result.target, "output": Path(args.output).name}


def _handle_tool_pack(args: argparse.Namespace) -> dict[str, Any]:
    request_path = Path(args.request)
    request = _mapping(_read_json(args.request, label="tool-pack request"), "tool-pack request")
    base = request_path.parent
    canonical = _ensure_workflow_envelope(
        _request_value(
            request.get("canonical_map", request.get("map")),
            base,
            "canonical map",
        ),
        "modbus-map/v1",
    )
    plan = _ensure_workflow_envelope(
        _request_value(
            request.get("read_plan", request.get("plan")), base, "read plan"
        ),
        "modbus-read-plan/v1",
    )
    targets = request.get("targets")
    if not isinstance(targets, Sequence) or isinstance(targets, (str, bytes, bytearray)):
        raise CliError("tool-pack request targets must be an array")
    target_ids, options = _target_request(targets, request.get("target_options", {}))
    pack = build_tool_pack(canonical, plan, targets=target_ids, mode=str(request.get("mode", "final")), target_options=options)
    _write_pack(pack, args.output, overwrite=args.overwrite)
    return {"status": pack.status, "targets": [result.target for result in pack.target_results], "output": Path(args.output).name}


def _target_request(
    raw_targets: Any,
    raw_options: Any,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if not isinstance(raw_targets, Sequence) or isinstance(raw_targets, (str, bytes, bytearray)):
        raise CliError("targets must be an array")
    option_mapping = _mapping(raw_options, "target_options")
    merged: dict[str, dict[str, Any]] = {}
    for target, options in option_mapping.items():
        if not isinstance(options, Mapping):
            raise CliError(f"target_options.{target} must be an object")
        normalized_target = str(target).strip().lower()
        if not normalized_target:
            raise CliError("target_options keys must not be empty")
        if normalized_target in merged:
            raise CliError(f"target_options contains duplicate target {normalized_target!r}")
        merged[normalized_target] = dict(options)

    identifiers: list[str] = []
    for index, entry in enumerate(raw_targets):
        entry_options: Mapping[str, Any] = {}
        profile: Any = None
        if isinstance(entry, str):
            identifier = entry.strip().lower()
        elif isinstance(entry, Mapping):
            unknown = set(entry) - {"id", "profile", "options"}
            if unknown:
                raise CliError(
                    f"targets[{index}] has unknown fields: " + ", ".join(sorted(str(value) for value in unknown))
                )
            identifier = str(entry.get("id", "")).strip().lower()
            profile = entry.get("profile")
            entry_options = _mapping(entry.get("options", {}), f"targets[{index}].options")
        else:
            raise CliError(f"targets[{index}] must be a target ID or object")
        if not identifier:
            raise CliError(f"targets[{index}].id must not be empty")
        if identifier in identifiers:
            raise CliError(f"target {identifier!r} is selected more than once")
        if profile is not None and identifier != "modpoll":
            raise CliError("only the modpoll target accepts a profile")
        identifiers.append(identifier)

        selected = merged.setdefault(identifier, {})
        additions = dict(entry_options)
        if profile is not None:
            if "profile" in additions and additions["profile"] != str(profile):
                raise CliError(f"targets[{index}] contains conflicting modpoll profiles")
            additions["profile"] = str(profile)
        for key, value in additions.items():
            if key in selected and selected[key] != value:
                raise CliError(
                    f"conflicting {identifier!r} option {key!r} appears in targets and target_options"
                )
            selected[key] = value
    unused = set(merged) - set(identifiers)
    if unused:
        raise CliError(
            "target_options contains unselected targets: " + ", ".join(sorted(unused))
        )
    return identifiers, merged


def _capture_csv(data: bytes) -> Mapping[str, Any]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CliError("capture CSV must use UTF-8") from exc
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text, newline=""), dialect=dialect)
    if not reader.fieldnames:
        raise CliError("capture CSV must contain a header row")
    raw_headers = list(reader.fieldnames)
    headers = [_normalize_header(value or "") for value in raw_headers]
    if any(not header for header in headers):
        raise CliError("capture CSV headers must be non-empty")
    if len(headers) != len(set(headers)):
        raise CliError("capture CSV headers must be unique after normalization")
    samples: list[dict[str, Any]] = []
    for row in reader:
        if not row or all(value in (None, "") for value in row.values()):
            continue
        sample = {
            header: (
                row.get(raw_header).strip()
                if isinstance(row.get(raw_header), str)
                else row.get(raw_header)
            )
            for raw_header, header in zip(raw_headers, headers, strict=True)
        }
        if sample.get("raw_words") not in (None, ""):
            raw_words = str(sample["raw_words"]).strip()
            if raw_words.startswith("["):
                try:
                    parsed_words = json.loads(raw_words)
                except json.JSONDecodeError as exc:
                    raise CliError(
                        f"capture CSV raw_words is invalid on row {reader.line_num}"
                    ) from exc
                if not isinstance(parsed_words, list):
                    raise CliError(
                        f"capture CSV raw_words must be an array on row {reader.line_num}"
                    )
                sample["raw_words"] = [_parse_word(value) for value in parsed_words]
            else:
                sample["raw_words"] = [
                    _parse_word(value)
                    for value in re.split(r"[\s;|]+", raw_words)
                    if value
                ]
        else:
            sample.pop("raw_words", None)
        if sample.get("success") not in (None, ""):
            normalized = str(sample["success"]).strip().lower()
            if normalized not in {"true", "false"}:
                raise CliError(
                    f"capture CSV success must be true or false on row {reader.line_num}"
                )
            sample["success"] = normalized == "true"
        sample["_source"] = {"format": "csv", "row": reader.line_num}
        samples.append(sample)
    return {"schema_version": "capture/v1", "samples": samples}


def _handle_analysis(args: argparse.Namespace) -> dict[str, Any]:
    path, data = _read_bytes(args.input)
    source_format = args.format or ("csv" if path.suffix.lower() == ".csv" else "json")
    if source_format == "csv":
        capture = _capture_csv(data)
    else:
        capture = _mapping(_read_json(args.input, label="capture"), "capture")
    options = dict(_json_options(args.options, "analysis options"))
    if args.now is not None:
        options["now"] = args.now
    allowed = {"now", "max_samples", "expected_interval_seconds", "stale_after_seconds", "flatline_min_samples", "ranges", "rate_limits", "counter_specs"}
    unknown = set(options) - allowed
    if unknown:
        raise CliError("unknown analysis options: " + ", ".join(sorted(unknown)))
    raw_result = analyze_capture(capture, **options)
    findings = list(raw_result.get("findings", ()))
    result = artifact_envelope(
        raw_result,
        schema_version="modbus-capture-analysis/v1",
        inputs={"capture": capture, "analysis_options": options},
        findings=findings,
        holds=_blocking_findings(findings),
    )
    _write_json(args.output, result, overwrite=args.overwrite)
    return {"status": "analyzed", "findings": len(result.get("findings", ())), "output": Path(args.output).name}


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _infer_delimited_config(example: bytes, records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    try:
        text = example.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CliError("custom format example must use UTF-8") from exc
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error as exc:
        raise CliError("could not infer a safe delimited format; provide --config") from exc
    rows = list(csv.reader(io.StringIO(text, newline=""), dialect))
    if len(rows) < 2 or not rows[0] or any(len(row) != len(rows[0]) for row in rows[1:]):
        raise CliError("example needs a header and consistent delimited rows; provide --config")
    headers = rows[0]
    fields = [_normalize_header(header) for header in headers]
    available = set().union(*(record.keys() for record in records)) if records else set()
    if len(fields) != len(set(fields)) or any(field not in available for field in fields):
        raise CliError("example headers must match canonical map fields; provide a reviewed --config")
    return {
        "name": "inferred-delimited-format",
        "header_template": dialect.delimiter.join(headers),
        "record_template": dialect.delimiter.join("{" + field + "}" for field in fields),
        "line_ending": "\r\n" if "\r\n" in text else "\n",
        "escape_mode": "csv",
        "delimiter": dialect.delimiter,
        "spreadsheet_safe": True,
        "missing": "error",
    }


def _handle_custom(args: argparse.Namespace) -> dict[str, Any]:
    example_path, example = _read_bytes(args.example)
    canonical = _read_json(args.map)
    records = _map_points(canonical)
    if args.config:
        config = _mapping(_read_json(args.config, label="custom format config"), "custom format config")
        inference = "reviewed-config"
    else:
        config = _infer_delimited_config(example, records)
        inference = "strict-delimited-example"
    available = sorted(set().union(*(record.keys() for record in records)) if records else set())
    findings = validate_custom_format(config, available_fields=available)
    errors = [finding for finding in findings if finding.get("severity") == "error"]
    if errors:
        raise CliError("custom format is invalid: " + "; ".join(str(item["message"]) for item in errors))
    rendered = render_custom_format(records, config, metadata={"source_map_schema": canonical.get("schema_version") if isinstance(canonical, Mapping) else None})
    config_artifact = artifact_envelope(
        config,
        schema_version="modbus-custom-format-config/v1",
        inputs={
            "example": example,
            "canonical_map": canonical,
            "provided_config": config if args.config else None,
        },
        assumptions=[],
        findings=findings,
        holds=_blocking_findings(findings),
    )
    evidence = artifact_envelope({
        "contract": "modbus-custom-format-evidence/v1",
        "status": "ready-for-human-review",
        "inference": inference,
        "example": {"filename": example_path.name, "sha256": hashlib.sha256(example).hexdigest()},
        "record_count": len(records),
    },
        schema_version="modbus-custom-format-evidence/v1",
        inputs={
            "example": example,
            "canonical_map": canonical,
            "format_config": config,
        },
        assumptions=[],
        findings=findings,
        holds=_blocking_findings(findings),
    )
    artifacts = [
        Artifact.text("format-config.json", "application/json", stable_json(config_artifact), "custom-format-config"),
        Artifact.text("rendered-output.txt", "text/plain", rendered, "custom-format-output"),
        Artifact.text("evidence.json", "application/json", stable_json(evidence), "custom-format-evidence"),
    ]
    _write_artifacts(args.output, artifacts, overwrite=args.overwrite)
    return {"status": evidence["status"], "records": len(records), "output": Path(args.output).name}


_HANDLERS: dict[str, Callable[[argparse.Namespace], dict[str, Any]]] = {
    "parse-map": _handle_parse,
    "extract-pdf": _handle_pdf,
    "normalize-map": _handle_normalize,
    "lint-map": _handle_lint,
    "diagnose-map": _handle_diagnose,
    "review-evidence": _handle_review,
    "apply-review-decisions": _handle_decisions,
    "remap-addresses": _handle_remap,
    "compare-maps": _handle_compare,
    "capture-sample": _handle_capture,
    "evaluate-byte-order": _handle_byte_order,
    "compile-read-plan": _handle_plan,
    "generate-node-red": _handle_node_red,
    "generate-modpoll": _handle_modpoll,
    "generate-modscan": _handle_modscan,
    "build-tool-pack": _handle_tool_pack,
    "analyze-capture": _handle_analysis,
    "infer-custom-format": _handle_custom,
}


__all__ = ["COMMANDS", "main", "run_cli"]
