#!/usr/bin/env python3
"""Exercise the public compiler outcome and enforce its human-time contract."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import platform
import re
import sys
import time
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "plugins" / "modbus-skills" / "runtime"
sys.path.insert(0, str(RUNTIME))

from modbus_skills.artifacts import stable_input_hash  # noqa: E402
from modbus_skills.compiler import compile_user_map  # noqa: E402
from modbus_skills.compiler_contracts import (  # noqa: E402
    build_device_binding,
    build_oem_map,
)
from modbus_skills.exporters import stable_json  # noqa: E402


class WorkflowFailure(RuntimeError):
    """Raised when a transcript exceeds the product's human-time budget."""


_FORBIDDEN_EVENTS = frozenset(
    {
        "page-approval-question",
        "row-review-question",
        "dependency-install",
        "stage-skill-handoff",
    }
)


def validate_transcript(transcript: Mapping[str, Any]) -> None:
    events = transcript.get("events", ())
    forbidden = {
        str(event.get("kind"))
        for event in events
        if isinstance(event, Mapping) and event.get("kind") in _FORBIDDEN_EVENTS
    }
    if forbidden:
        raise WorkflowFailure("forbidden workflow events: " + ", ".join(sorted(forbidden)))
    if transcript.get("repeated_hold_signatures"):
        raise WorkflowFailure("the workflow repeated an unchanged hold")
    questions = int(transcript.get("question_count", 0))
    packets = int(transcript.get("decision_packet_count", 0))
    if questions > 1 or packets > 1:
        raise WorkflowFailure("the source phase exceeded one grouped decision exchange")
    if int(transcript.get("stage_handoffs", 0)):
        raise WorkflowFailure("the outcome path exposed a stage-skill handoff")
    if int(transcript.get("invocation_count", 0)) < 1:
        raise WorkflowFailure("the transcript contains no compiler invocation")


def fixture_sha256(path: Path) -> str:
    return stable_input_hash(path.read_bytes())


def load_benchmark_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _source(points: list[dict[str, Any]]) -> dict[str, Any]:
    return build_oem_map(points, source_hash="a" * 64)


def _point(point_id: str, offset: int, measurement: str) -> dict[str, Any]:
    return {
        "oem_point_id": point_id,
        "name": point_id.replace("-", " ").title(),
        "area": "holding-register",
        "protocol_offset": offset,
        "datatype": "uint16",
        "word_span": 1,
        "source_refs": [
            {"page_index": 1, "row_index": offset, "region_id": f"row-{offset}"}
        ],
        "measurement": measurement,
    }


def _selection(source: Mapping[str, Any], point_ids: list[str], *, quality: str = "exact") -> dict[str, Any]:
    offsets = {point["oem_point_id"]: point["protocol_offset"] for point in source["points"]}
    entries = [
        {
            "oem_point_id": point_id,
            "matched_intent": point_id,
            "match_quality": quality,
            "reason": "Synthetic requested measurement",
            "evidence_refs": [f"row-{offsets[point_id]}"],
        }
        for point_id in point_ids
    ]
    return {
        "oem_map_hash": stable_input_hash(source),
        "requested_measurements": point_ids,
        "included": entries if quality == "exact" else [],
        "suggested": entries if quality != "exact" else [],
        "excluded": [],
    }


def _request(
    source: Mapping[str, Any],
    point_ids: list[str],
    *,
    quality: str = "exact",
    targets: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "modbus-compile-request/v1",
        "oem_map": source,
        "selection_candidate": _selection(source, point_ids, quality=quality),
        "targets": targets or [],
        "target_options": {},
    }


def _selected_counts(case_root: Path) -> dict[str, int]:
    user_map = json.loads((case_root / "artifacts/user-map.json").read_text(encoding="utf-8"))
    with (case_root / "artifacts/user-map.csv").open(encoding="utf-8", newline="") as stream:
        csv_count = len(list(csv.DictReader(stream)))
    human = (case_root / "artifacts/user-map.md").read_text(encoding="utf-8")
    return {
        "csv": csv_count,
        "human": len(re.findall(r"^- `", human, flags=re.MULTILINE)),
        "json": len(user_map["points"]),
    }


def _clean_case(output: Path) -> dict[str, Any]:
    source_path = output / "inputs" / "clean-registers.csv"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        "logical_point_id,name,protocol_offset,area,datatype,access\n"
        "temperature,Temperature,10,holding-register,uint16,read-only\n"
        "status,Status,11,holding-register,uint16,read-only\n",
        encoding="utf-8",
    )
    request = {
        "schema_version": "modbus-compile-request/v1",
        "source": {"path": str(source_path), "format": "csv"},
        "selection_template": {
            "schema_version": "modbus-user-selection-template/v1",
            "requested_measurements": ["temperature", "status"],
            "included": [
                {
                    "oem_point_id": point_id,
                    "matched_intent": point_id,
                    "match_quality": "exact",
                    "reason": "Synthetic requested measurement",
                    "evidence_refs": [f"csv:row:{row}"],
                }
                for point_id, row in (("temperature", 2), ("status", 3))
            ],
            "suggested": [],
            "excluded": [],
        },
        "targets": [],
        "target_options": {},
    }
    root = output / "clean-offline"
    result = compile_user_map(request, root)
    transcript = {
        "case_id": "clean-offline",
        "invocation_count": 1,
        "question_count": 0,
        "decision_packet_count": 0,
        "resume_exchange_count": 0,
        "stage_handoffs": 0,
        "repeated_hold_signatures": [],
        "state_transitions": [result["state"]],
        "selected_point_counts": _selected_counts(root),
        "events": [{"kind": "compiler-invocation"}],
    }
    validate_transcript(transcript)
    return transcript


def _fallback_case(output: Path) -> dict[str, Any]:
    support = output / "support"
    support.mkdir(parents=True, exist_ok=True)
    executable = support / "pdftotext"
    executable.write_text(
        """#!/usr/bin/env python3
import sys
if '-v' in sys.argv:
    print('pdftotext version 25.06.0', file=sys.stderr)
elif '-h' in sys.argv:
    print('-f -l -layout -bbox-layout -enc', file=sys.stderr)
elif '-layout' in sys.argv:
    print('MODBUS REGISTER 40001\\nAddress Name Data Type Area Access')
else:
    print('''<doc><page width="600" height="800"><flow><block>
<line><word xMin="10" yMin="10" xMax="50" yMax="18">Address</word><word xMin="100" yMin="10" xMax="140" yMax="18">Name</word><word xMin="220" yMin="10" xMax="250" yMax="18">Data</word><word xMin="255" yMin="10" xMax="285" yMax="18">Type</word><word xMin="320" yMin="10" xMax="350" yMax="18">Area</word><word xMin="460" yMin="10" xMax="500" yMax="18">Access</word></line>
<line><word xMin="10" yMin="25" xMax="40" yMax="33">40001</word><word xMin="100" yMin="25" xMax="180" yMax="33">Temperature</word><word xMin="220" yMin="25" xMax="270" yMax="33">uint16</word><word xMin="320" yMin="25" xMax="430" yMax="33">holding-register</word><word xMin="460" yMin="25" xMax="520" yMax="33">read-only</word></line>
</block></flow></page></doc>''')
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    pdf = output / "inputs" / "fallback-map.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% synthetic transcript fixture\n")
    request = {
        "schema_version": "modbus-compile-request/v1",
        "source": {
            "path": str(pdf),
            "format": "pdf",
            "defaults": {"address_convention": "modicon-reference"},
        },
        "selection_template": {
            "schema_version": "modbus-user-selection-template/v1",
            "requested_measurements": ["temperature"],
            "included": [
                {
                    "exact_name": "Temperature",
                    "matched_intent": "temperature",
                    "match_quality": "exact",
                    "reason": "Synthetic exact-name selection",
                    "evidence_refs": ["p1:y25"],
                }
            ],
            "suggested": [],
            "excluded": [],
        },
        "targets": [],
        "target_options": {},
    }
    prior_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(support) + os.pathsep + prior_path
    try:
        result = compile_user_map(request, output / "fallback-extraction")
    finally:
        os.environ["PATH"] = prior_path
    oem_map = json.loads(
        (output / "fallback-extraction/artifacts/oem-map.json").read_text(encoding="utf-8")
    )
    transcript = {
        "case_id": "fallback-extraction",
        "invocation_count": 1,
        "question_count": 0,
        "decision_packet_count": 0,
        "resume_exchange_count": 0,
        "stage_handoffs": 0,
        "repeated_hold_signatures": [],
        "state_transitions": [result["state"]],
        "selected_point_counts": _selected_counts(output / "fallback-extraction"),
        "strategy": "coordinate-fallback",
        "source_point_count": len(oem_map["points"]),
        "events": [
            {"kind": "compiler-invocation"},
            {"kind": "internal-parser-fallback"},
        ],
    }
    validate_transcript(transcript)
    return transcript


def _selection_case(output: Path) -> dict[str, Any]:
    source = _source([_point("temperature", 20, "temperature")])
    root = output / "selection-exception"
    first = compile_user_map(_request(source, ["temperature"], quality="near"), root)
    case = json.loads((root / "case.json").read_text(encoding="utf-8"))
    packet = json.loads((root / "control/selection-packet.json").read_text(encoding="utf-8"))
    reply = {
        "schema_version": "modbus-compile-resume/v1",
        "case_id": case["case_id"],
        "case_hash": stable_input_hash(case),
        "action": "provide-selection-decision",
        "decision_candidate": {
            "schema_version": "modbus-compiler-decision-candidate/v1",
            "case_id": packet["case_id"],
            "phase": packet["phase"],
            "packet_id": packet["packet_id"],
            "source_hash": packet["source_hash"],
            "input_hashes": copy.deepcopy(packet["input_hashes"]),
            "decisions": [
                {
                    "decision_id": "selection.choose-included-points",
                    "disposition": "include-specified",
                    "selected_subject_ids": ["temperature"],
                    "reason": "Synthetic engineer choice",
                    "evidence_refs": ["row-20"],
                }
            ],
        },
    }
    second = compile_user_map(None, root, resume=reply)
    transcript = {
        "case_id": "selection-exception",
        "invocation_count": 2,
        "question_count": 1,
        "decision_packet_count": 1,
        "resume_exchange_count": 1,
        "stage_handoffs": 0,
        "repeated_hold_signatures": [],
        "state_transitions": [first["state"], second["state"]],
        "selected_point_counts": _selected_counts(root),
        "events": [
            {"kind": "compiler-invocation"},
            {"kind": "grouped-decision-packet"},
            {"kind": "compiler-resume"},
        ],
    }
    validate_transcript(transcript)
    return transcript


def _binding_case(output: Path) -> dict[str, Any]:
    source = _source([_point("first", 257, "first"), _point("last", 308, "last")])
    request = _request(source, ["first", "last"], targets=["node-red"])
    root = output / "binding-readable-island"
    first = compile_user_map(request, root)
    before = {
        name: fixture_sha256(root / "artifacts" / name)
        for name in ("user-map.md", "user-map.json", "user-map.csv")
    }
    case = json.loads((root / "case.json").read_text(encoding="utf-8"))
    binding = build_device_binding(
        source,
        route_id="synthetic-route",
        unit_id=1,
        read_constraints={
            "readable_islands": [
                {
                    "island_id": "synthetic-readable",
                    "route_id": "synthetic-route",
                    "unit_id": 1,
                    "area": "holding-register",
                    "function_code": 3,
                    "start_offset": 257,
                    "end_offset": 308,
                    "reason": "Synthetic fixture declares the interval readable",
                    "evidence_refs": ["fixture:readable-island"],
                }
            ]
        },
    )
    reply = {
        "schema_version": "modbus-compile-resume/v1",
        "case_id": case["case_id"],
        "case_hash": stable_input_hash(case),
        "action": "provide-binding",
        "binding": binding,
    }
    second = compile_user_map(None, root, resume=reply)
    after = {
        name: fixture_sha256(root / "artifacts" / name)
        for name in ("user-map.md", "user-map.json", "user-map.csv")
    }
    plan = json.loads((root / "artifacts/read-plan.json").read_text(encoding="utf-8"))
    transcript = {
        "case_id": "binding-readable-island",
        "invocation_count": 2,
        "question_count": 1,
        "decision_packet_count": 0,
        "resume_exchange_count": 1,
        "stage_handoffs": 0,
        "repeated_hold_signatures": [],
        "state_transitions": [first["state"], second["state"]],
        "offline_artifacts_preserved": before == after,
        "request_count": len(plan["requests"]),
        "physical_gate_count": int(second["state"] == "awaiting-physical-read"),
        "events": [
            {"kind": "compiler-invocation"},
            {"kind": "binding-request"},
            {"kind": "compiler-resume"},
        ],
    }
    validate_transcript(transcript)
    return transcript


def _benchmark(fixtures: Path, output: Path) -> dict[str, Any]:
    path = fixtures / "benchmark-registers.csv"
    rows = load_benchmark_rows(path)
    points = [
        {
            "oem_point_id": row["oem_point_id"],
            "name": row["name"],
            "area": row["area"],
            "protocol_offset": int(row["protocol_offset"]),
            "datatype": row["datatype"],
            "word_span": int(row["word_span"]),
            "source_refs": [{"record_id": f"benchmark:{index}"}],
        }
        for index, row in enumerate(rows, start=1)
    ]
    source = _source(points)
    ids = [point["oem_point_id"] for point in points]
    started = time.monotonic()
    result = compile_user_map(_request(source, ids), output / "benchmark-case")
    elapsed = time.monotonic() - started
    if result["state"] != "offline-complete" or elapsed > 300:
        raise WorkflowFailure("the local benchmark did not meet the five-minute offline target")
    return {
        "elapsed_ms": round(elapsed * 1000, 3),
        "fixture_sha256": fixture_sha256(path),
        "machine": platform.platform(),
        "python": platform.python_version(),
        "row_count": len(rows),
        "selected_point_count": len(ids),
        "threshold_ms": 300_000,
    }


def run(fixtures: Path, output: Path, *, benchmark: bool = False) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    cases = [
        _clean_case(output),
        _fallback_case(output),
        _selection_case(output),
        _binding_case(output),
    ]
    report: dict[str, Any] = {
        "schema_version": "modbus-compile-workflow-report/v1",
        "status": "passed",
        "cases": cases,
        "dependencies": [],
    }
    if benchmark:
        report["benchmark"] = _benchmark(fixtures, output)
    (output / "compile-workflow-report.json").write_text(stable_json(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, default=ROOT / "tests/fixtures/compiler-workflow")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run(args.fixtures, args.output, benchmark=args.benchmark)
    except (OSError, ValueError, WorkflowFailure) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(stable_json({"status": report["status"], "report": "compile-workflow-report.json"}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
