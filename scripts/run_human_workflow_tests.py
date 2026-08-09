#!/usr/bin/env python3
"""Run the public skill wrappers through a human commissioning workflow.

The runner intentionally does not ship register maps.  Give it a local corpus
directory that contains a small manifest and maps for which the operator has
redistribution rights.  Its reports contain only case IDs, counts, statuses,
and finding codes.  They never copy map rows, source text, or local paths.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "plugins" / "modbus-skills" / "skills"
REPORT_SCHEMA = "modbus-human-workflow-report/v1"
_CORPUS_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_WORKFLOW_ROLES = frozenset(
    {"clean", "byte_order", "safety", "compare_before", "compare_after"}
)
_LOCAL_ONLY_OUTPUT_ROOTS = (ROOT / "artifacts", ROOT / "private")
_BYTE_ORDER_TYPES = {
    2: ("uint32", "int32", "float32"),
    4: ("uint64", "int64", "float64"),
}


class WorkflowFailure(RuntimeError):
    """An expected human-visible workflow condition was not met."""


@dataclass
class CaseResult:
    case_id: str
    status: str = "passed"
    checks: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None

    def check(self, name: str, condition: bool, *, details: Mapping[str, Any] | None = None) -> None:
        value = {"name": name, "passed": bool(condition)}
        if details:
            value["details"] = dict(details)
        self.checks.append(value)
        if not condition:
            self.status = "failed"


@dataclass(frozen=True, slots=True)
class ByteOrderProfile:
    word_count: int
    datatypes: tuple[str, ...]
    point_datatype: str
    expected_candidate_count: int
    identity_layout: str
    raw_words: tuple[str, ...]


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_"} else "-" for character in value)


def _is_ignored_repo_path(path: Path) -> bool:
    try:
        relative = path.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return False
    result = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "check-ignore",
            "--quiet",
            "--no-index",
            "--",
            relative,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _validate_output_path(output: Path) -> Path:
    """Allow external output or an ignored, explicit local-only repo root."""

    resolved = output.resolve()
    repository = ROOT.resolve()
    if resolved != repository and repository not in resolved.parents:
        return resolved
    for local_root in _LOCAL_ONLY_OUTPUT_ROOTS:
        local_root = local_root.resolve()
        if (
            resolved == local_root or local_root in resolved.parents
        ) and _is_ignored_repo_path(resolved):
            return resolved
    raise WorkflowFailure(
        "--output inside the repository must be below an ignored artifacts/ or private/ directory"
    )


def _codes(value: Mapping[str, Any]) -> set[str]:
    return {
        str(item.get("code"))
        for group in ("holds", "findings")
        for item in value.get(group, ())
        if isinstance(item, Mapping) and item.get("code")
    }


def _point_id(point: Mapping[str, Any]) -> str:
    return str(point.get("logical_point_id", point.get("point_id", point.get("id", ""))))


def _planned_point_ids(plan: Mapping[str, Any]) -> set[str]:
    identifiers: set[str] = set()
    requests = plan.get("requests", ())
    if not isinstance(requests, Sequence) or isinstance(
        requests, (str, bytes, bytearray)
    ):
        return identifiers
    for request in requests:
        if not isinstance(request, Mapping):
            continue
        traces = request.get("points", ())
        if isinstance(traces, Sequence) and not isinstance(
            traces, (str, bytes, bytearray)
        ):
            for trace in traces:
                if isinstance(trace, Mapping):
                    identifier = _point_id(trace)
                    if identifier:
                        identifiers.add(identifier)
        point_ids = request.get("point_ids", ())
        if isinstance(point_ids, Sequence) and not isinstance(
            point_ids, (str, bytes, bytearray)
        ):
            identifiers.update(
                str(identifier) for identifier in point_ids if str(identifier)
            )
    return identifiers


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _semantic_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class WrapperRunner:
    """Run the same wrapper files that an installed skill directs a user to run."""

    def __init__(self, work: Path) -> None:
        self.work = work
        self.receipts: list[dict[str, Any]] = []

    def run(self, skill: str, args: Sequence[object], *, expected_code: int = 0) -> dict[str, Any]:
        wrapper = SKILLS / skill / "scripts" / "run.py"
        if not wrapper.is_file():
            raise WorkflowFailure(f"skill wrapper is unavailable: {skill}")
        command = [sys.executable, str(wrapper), *(str(value) for value in args)]
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        record = {
            "skill": skill,
            "return_code": result.returncode,
            "stdout_sha256": hashlib.sha256(result.stdout.encode("utf-8")).hexdigest(),
            "stderr_sha256": hashlib.sha256(result.stderr.encode("utf-8")).hexdigest(),
        }
        self.receipts.append(record)
        if result.returncode != expected_code:
            raise WorkflowFailure(f"{skill} returned {result.returncode}; expected {expected_code}")
        try:
            receipt = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise WorkflowFailure(f"{skill} did not return a JSON receipt") from exc
        if not isinstance(receipt, Mapping):
            raise WorkflowFailure(f"{skill} returned a non-object receipt")
        return dict(receipt)


def _manifest_cases(manifest: Mapping[str, Any], maps: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    explicit = manifest.get("workflow_cases", manifest.get("cases", {}))
    if isinstance(explicit, Mapping):
        values = {
            str(key): str(value)
            for key, value in explicit.items()
            if str(key) in _WORKFLOW_ROLES and str(value) in maps
        }
        if values:
            return values
    # A local corpus can use the small generic manifest that the project uses
    # for provenance.  These keywords avoid putting vendor names in this file.
    def matching(*terms: str) -> str | None:
        for identifier, entry in maps.items():
            text = " ".join(str(entry.get(key, "")) for key in ("id", "scenario", "role")).lower()
            if all(term in text for term in terms):
                return identifier
        return None
    result = {
        "clean": matching("mixed", "input"),
        "byte_order": matching("unresolved", "byte"),
        "safety": matching("write-only"),
        "compare_before": matching("pre-review"),
        "compare_after": matching("post-review"),
    }
    return {key: value for key, value in result.items() if value}


def _resolve_corpus_file(corpus_dir: Path, filename: str, *, label: str) -> Path:
    root = corpus_dir.resolve()
    relative = Path(filename)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or filename.startswith("\\\\")
        or _WINDOWS_ABSOLUTE_PATH.match(filename) is not None
    ):
        raise WorkflowFailure(f"{label} must use a relative path inside the corpus")
    path = (root / relative).resolve()
    if path == root or root not in path.parents:
        raise WorkflowFailure(f"{label} escapes the corpus directory")
    return path


def _load_corpus(corpus_dir: Path) -> tuple[Mapping[str, Any], dict[str, Mapping[str, Any]], dict[str, str]]:
    manifest_path = _resolve_corpus_file(
        corpus_dir, "corpus.json", label="corpus manifest"
    )
    if not manifest_path.is_file():
        raise WorkflowFailure("corpus.json is required in --corpus-dir")
    manifest = _json(manifest_path)
    if not isinstance(manifest, Mapping) or not isinstance(manifest.get("maps"), Sequence):
        raise WorkflowFailure("corpus.json must contain a maps array")
    maps: dict[str, Mapping[str, Any]] = {}
    for value in manifest["maps"]:
        if not isinstance(value, Mapping):
            raise WorkflowFailure("corpus maps must be objects")
        identifier_value = value.get("id")
        filename_value = value.get("file")
        identifier = identifier_value.strip() if isinstance(identifier_value, str) else ""
        filename = filename_value.strip() if isinstance(filename_value, str) else ""
        if not identifier or not filename:
            raise WorkflowFailure("each corpus map needs id and file")
        if _CORPUS_ID.fullmatch(identifier) is None:
            raise WorkflowFailure(
                "corpus map IDs must use lowercase letters, digits, and hyphens"
            )
        if identifier in maps:
            raise WorkflowFailure("corpus map IDs must be unique")
        path = _resolve_corpus_file(corpus_dir, filename, label="corpus map file")
        if not path.is_file():
            raise WorkflowFailure(f"map file is missing for case {identifier}")
        expected_hash = value.get("sha256")
        if expected_hash and _sha256(path) != str(expected_hash):
            raise WorkflowFailure(f"map checksum mismatch for case {identifier}")
        maps[identifier] = dict(value)
    cases = _manifest_cases(manifest, maps)
    required = set(_WORKFLOW_ROLES)
    missing = sorted(required - set(cases))
    if missing:
        raise WorkflowFailure("corpus has no workflow roles: " + ", ".join(missing))
    return manifest, maps, cases


def _map_path(corpus: Path, entry: Mapping[str, Any]) -> Path:
    return _resolve_corpus_file(
        corpus, str(entry["file"]), label="corpus map file"
    )


def _defaults_path(corpus: Path, identifier: str) -> Path | None:
    stems = [identifier]
    for suffix in ("-before-review", "-after-review", "-reviewed", "-safety"):
        if identifier.endswith(suffix):
            stems.append(identifier[: -len(suffix)])
    for stem in stems:
        path = _resolve_corpus_file(
            corpus, f"defaults-{stem}.json", label="corpus defaults file"
        )
        if path.is_file():
            return path
    return None


def _diagnose(runner: WrapperRunner, source: Path, output: Path, defaults: Path | None) -> tuple[dict[str, Any], Mapping[str, Any]]:
    args: list[object] = ["--input", source, "--output", output]
    if defaults:
        args.extend(["--defaults", defaults])
    receipt = runner.run("review-map", args)
    canonical = _json(output / "map-draft.json")
    if not isinstance(canonical, Mapping):
        raise WorkflowFailure("diagnose did not create a canonical map object")
    return receipt, canonical


def _compile(runner: WrapperRunner, canonical: Path, output: Path) -> tuple[dict[str, Any], Mapping[str, Any]]:
    receipt = runner.run("plan-reads", ["--input", canonical, "--output", output])
    value = _json(output)
    if not isinstance(value, Mapping):
        raise WorkflowFailure("read plan is not an object")
    return receipt, value


def _pack_request(map_path: Path, plan_path: Path, *, mode: str) -> dict[str, Any]:
    return {
        "canonical_map": map_path.name,
        "read_plan": plan_path.name,
        "mode": mode,
        "targets": ["node-red", {"id": "modpoll", "profile": "gavinying-cli"}, "modscan"],
    }


def _copy_for_request(source: Path, directory: Path, name: str) -> Path:
    target = directory / name
    shutil.copyfile(source, target)
    return target


def _run_pack(runner: WrapperRunner, map_path: Path, plan_path: Path, output: Path, *, mode: str) -> tuple[dict[str, Any], Mapping[str, Any]]:
    request_dir = output.parent / f"{output.name}-request"
    request_dir.mkdir(parents=True, exist_ok=True)
    map_local = _copy_for_request(map_path, request_dir, "canonical-map.json")
    plan_local = _copy_for_request(plan_path, request_dir, "read-plan.json")
    request = request_dir / "request.json"
    _write_json(request, _pack_request(map_local, plan_local, mode=mode))
    receipt = runner.run("build-tool-pack", ["--request", request, "--output", output])
    manifest = _json(output / "manifest.json")
    if not isinstance(manifest, Mapping):
        raise WorkflowFailure("tool pack manifest is not an object")
    return receipt, manifest


def _find_byte_points(canonical: Mapping[str, Any]) -> list[str]:
    ids: set[str] = set()
    for hold in canonical.get("holds", ()):
        if isinstance(hold, Mapping) and str(hold.get("field", "")) in {"byte_order", "byte_order_confirmed"}:
            ids.update(str(value) for value in hold.get("point_ids", ()) if str(value))
    return sorted(ids)


def _byte_order_profile(point: Mapping[str, Any]) -> ByteOrderProfile:
    raw_word_count = point.get(
        "word_span", point.get("word_count", point.get("register_width"))
    )
    if isinstance(raw_word_count, bool):
        raise WorkflowFailure(
            "an unresolved byte-order point must have a 32- or 64-bit word width"
        )
    try:
        word_count = int(raw_word_count)
    except (TypeError, ValueError) as exc:
        raise WorkflowFailure(
            "an unresolved byte-order point must have a 32- or 64-bit word width"
        ) from exc
    datatypes = _BYTE_ORDER_TYPES.get(word_count)
    point_datatype = str(point.get("datatype", "")).strip().lower()
    if datatypes is None or point_datatype not in datatypes:
        raise WorkflowFailure(
            "an unresolved byte-order point must use a matching 32- or 64-bit datatype"
        )
    byte_labels = "ABCDEFGH"[: word_count * 2]
    sample_words = ("0x0123", "0x4567", "0x89AB", "0xCDEF")[:word_count]
    return ByteOrderProfile(
        word_count=word_count,
        datatypes=datatypes,
        point_datatype=point_datatype,
        expected_candidate_count=math.factorial(word_count) * 2 * len(datatypes),
        identity_layout=byte_labels,
        raw_words=sample_words,
    )


def _select_byte_order_point(
    identifiers: Sequence[str], points: Mapping[str, Mapping[str, Any]]
) -> tuple[str, Mapping[str, Any], ByteOrderProfile]:
    for identifier in identifiers:
        point = points.get(identifier)
        if point is None:
            continue
        if point.get("source_include") is False or point.get("include") is False:
            continue
        if str(point.get("access", "")).strip().lower() == "write-only":
            continue
        try:
            profile = _byte_order_profile(point)
        except WorkflowFailure:
            continue
        return identifier, point, profile
    raise WorkflowFailure(
        "byte-order role needs an active unresolved 32- or 64-bit point"
    )


def _sample_identity(point: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": "workflow-sample-001",
        "point_id": _point_id(point),
        "route_id": point.get("route_id"),
        "unit_id": point.get("unit_id"),
        "area": point.get("area"),
        "protocol_offset": point.get("protocol_offset"),
        "timestamp": "2026-08-07T12:00:00Z",
    }


def _capture_for_point(
    point: Mapping[str, Any], profile: ByteOrderProfile
) -> dict[str, Any]:
    identity = _sample_identity(point)
    return {
        "schema_version": "capture/v1",
        "points": [{
            "logical_point_id": identity["point_id"],
            "route_id": identity["route_id"],
            "unit_id": identity["unit_id"],
            "area": identity["area"],
            "protocol_offset": identity["protocol_offset"],
            "datatype": profile.point_datatype,
            "word_count": profile.word_count,
        }],
        "samples": [{
            **identity,
            "raw_words": list(profile.raw_words),
            "response_ms": 8,
        }],
    }


def _words_for_value(point: Mapping[str, Any], value: int) -> list[int]:
    raw_word_count = point.get(
        "word_span", point.get("word_count", point.get("register_width", 1))
    )
    try:
        word_count = int(raw_word_count)
    except (TypeError, ValueError):
        word_count = 1
    if word_count not in {1, 2, 4}:
        word_count = 1
    return [0] * (word_count - 1) + [value]


def _live_capture(
    primary: Mapping[str, Any],
    flat: Mapping[str, Any],
    missing: Mapping[str, Any],
) -> dict[str, Any]:
    def config(point: Mapping[str, Any], **values: Any) -> dict[str, Any]:
        return {
            "logical_point_id": _point_id(point),
            "route_id": point.get("route_id"),
            "unit_id": point.get("unit_id"),
            "area": point.get("area"),
            "protocol_offset": point.get("protocol_offset"),
            "datatype": point.get("datatype"),
            **values,
        }

    primary_id = _point_id(primary)
    flat_id = _point_id(flat)
    return {
        "schema_version": "capture/v1",
        "capture_id": "bounded-human-workflow",
        "points": [
            config(
                primary,
                expected_interval_seconds=10,
                stale_after_seconds=15,
                minimum=0,
                maximum=100,
                rate_of_change_limit=2,
            ),
            config(flat, expected_interval_seconds=10, stale_after_seconds=15),
            config(missing, expected_interval_seconds=10, stale_after_seconds=15),
        ],
        "samples": [
            {"sample_id": "primary-001", "point_id": primary_id, "timestamp": "2026-08-07T12:00:00Z", "value": 10, "response_ms": 8, "raw_words": _words_for_value(primary, 10)},
            {"sample_id": "primary-002", "point_id": primary_id, "timestamp": "2026-08-07T12:00:10Z", "value": 20, "response_ms": 9, "raw_words": _words_for_value(primary, 20)},
            {"sample_id": "primary-003", "point_id": primary_id, "timestamp": "2026-08-07T12:00:30Z", "value": 80, "response_ms": 10, "raw_words": _words_for_value(primary, 80)},
            {"sample_id": "primary-004", "point_id": primary_id, "timestamp": "2026-08-07T12:00:40Z", "value": 120, "response_ms": 11, "raw_words": _words_for_value(primary, 120)},
            {"sample_id": "primary-004", "point_id": primary_id, "timestamp": "2026-08-07T12:00:40Z", "value": 120, "response_ms": 11, "raw_words": _words_for_value(primary, 120)},
            {"sample_id": "primary-005", "point_id": primary_id, "timestamp": "2026-08-07T12:00:50Z", "error": "timeout", "success": False, "response_ms": 100},
            {"sample_id": "flat-001", "point_id": flat_id, "timestamp": "2026-08-07T12:00:00Z", "value": 5, "response_ms": 8},
            {"sample_id": "flat-002", "point_id": flat_id, "timestamp": "2026-08-07T12:00:10Z", "value": 5, "response_ms": 8},
            {"sample_id": "flat-003", "point_id": flat_id, "timestamp": "2026-08-07T12:00:20Z", "value": 5, "response_ms": 8},
        ],
    }


def _write_live_capture_csv(path: Path, capture: Mapping[str, Any]) -> None:
    fields = (
        "sample_id", "point_id", "timestamp", "value", "response_ms",
        "error", "success", "raw_words",
    )
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        for sample in capture.get("samples", ()):
            if not isinstance(sample, Mapping):
                continue
            row = {field: sample.get(field, "") for field in fields}
            words = row["raw_words"]
            if isinstance(words, Sequence) and not isinstance(
                words, (str, bytes, bytearray)
            ):
                row["raw_words"] = ";".join(str(value) for value in words)
            writer.writerow(row)


def _node_red_summary(path: Path) -> dict[str, Any]:
    flow = _json(path)
    if not isinstance(flow, Sequence) or isinstance(flow, (str, bytes, bytearray)):
        raise WorkflowFailure("Node-RED flow is not an array")
    counts: dict[str, int] = {}
    tabs_disabled = True
    manual_injects = True
    names: set[str] = set()
    for node in flow:
        if not isinstance(node, Mapping):
            continue
        node_type = str(node.get("type", ""))
        names.add(str(node.get("name", "")))
        counts[node_type] = counts.get(node_type, 0) + 1
        if node_type == "tab":
            tabs_disabled = tabs_disabled and node.get("disabled") is True
        if node_type == "inject":
            manual_injects = (
                manual_injects
                and node.get("once") is False
                and str(node.get("repeat", "")) == ""
            )
    return {
        "counts": counts,
        "tabs_disabled": tabs_disabled,
        "manual_injects": manual_injects,
        "names": names,
        "write_nodes": sum(
            count for node_type, count in counts.items() if "write" in node_type.lower()
        ),
    }


def _verify_checksum_file(pack: Path) -> tuple[bool, int]:
    checksum_path = pack / "checksums.sha256"
    if not checksum_path.is_file():
        return False, 0
    rows = [row for row in checksum_path.read_text(encoding="utf-8").splitlines() if row]
    for row in rows:
        if "  " not in row:
            return False, len(rows)
        expected, relative = row.split("  ", 1)
        target = pack / relative
        if not target.is_file() or _sha256(target) != expected:
            return False, len(rows)
    return True, len(rows)


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_forbidden(key) or _contains_forbidden(item) for key, item in value.items())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_forbidden(item) for item in value)
    text = str(value).lower()
    return any(marker in text for marker in ("password", "secret", "api_key", "token", "private key", "file://", "/users/", "c:\\"))


def _case_map(cases: list[CaseResult]) -> dict[str, CaseResult]:
    return {case.case_id: case for case in cases}


def run_workflow(corpus_dir: Path, output: Path) -> dict[str, Any]:
    output = _validate_output_path(output)
    _, map_entries, roles = _load_corpus(corpus_dir)
    if output.exists() and any(output.iterdir()):
        raise WorkflowFailure("--output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    work = output / "artifacts"
    work.mkdir()
    runner = WrapperRunner(work)
    cases = [CaseResult(name) for name in (
        "diagnose-without-defaults", "diagnose-with-defaults", "access-safety",
        "read-plan", "clean-final-pack", "byte-order-probe-and-final-hold",
        "byte-order-evidence", "review-decisions", "stale-plan-and-rebuild",
        "map-comparison", "live-capture-analysis", "artifact-safety-and-determinism",
    )]
    check = _case_map(cases)

    diagnosed: dict[str, tuple[Path, Mapping[str, Any]]] = {}
    for map_index, (identifier, entry) in enumerate(
        sorted(map_entries.items()), start=1
    ):
        report_id = f"map-{map_index:03d}"
        raw_output = work / "diagnose-without-defaults" / _safe_name(identifier)
        receipt, canonical = _diagnose(runner, _map_path(corpus_dir, entry), raw_output, None)
        check["diagnose-without-defaults"].check(
            f"{report_id}-holds-or-ready", receipt.get("status") in {"blocked", "held", "ready"},
            details={"status": receipt.get("status"), "hold_codes": sorted(_codes(canonical))},
        )
        full_output = work / "diagnose-with-defaults" / _safe_name(identifier)
        receipt, canonical = _diagnose(runner, _map_path(corpus_dir, entry), full_output, _defaults_path(corpus_dir, identifier))
        expected_count = entry.get("expected_point_count")
        point_count = len(canonical.get("points", ()))
        check["diagnose-with-defaults"].check(
            f"{report_id}-keeps-rows",
            bool(canonical.get("points"))
            and (expected_count is None or point_count == int(expected_count)),
            details={"status": receipt.get("status"), "point_count": point_count, "expected_point_count": expected_count, "finding_codes": sorted(_codes(canonical))},
        )
        diagnosed[identifier] = (full_output / "map-draft.json", canonical)

    safety_path, safety_map = diagnosed[roles["safety"]]
    safety_lint = _json(safety_path.parent / "lint.json")
    safety_plan_path = work / "plans" / "safety.json"
    _, safety_plan = _compile(runner, safety_path, safety_plan_path)
    safety_points = { _point_id(point): point for point in safety_map.get("points", ()) if isinstance(point, Mapping) }
    write_only = {identifier for identifier, point in safety_points.items() if point.get("access") == "write-only"}
    planned_ids = _planned_point_ids(safety_plan)
    check["access-safety"].check("write-only-point-present", bool(write_only), details={"write_only_count": len(write_only)})
    check["access-safety"].check("write-only-point-excluded", write_only.isdisjoint(planned_ids), details={"plan_status": safety_plan.get("status"), "hold_codes": sorted(_codes(safety_plan))})
    check["access-safety"].check("write-access-is-visible", "point.write-access-declared" in _codes(safety_lint), details={"finding_codes": sorted(_codes(safety_lint))})

    clean_path, clean_map = diagnosed[roles["clean"]]
    clean_plan_path = work / "plans" / "clean.json"
    clean_plan_receipt, clean_plan = _compile(runner, clean_path, clean_plan_path)
    check["read-plan"].check("clean-plan-has-requests", bool(clean_plan.get("requests")), details={"status": clean_plan_receipt.get("status"), "request_count": len(clean_plan.get("requests", ()) )})
    fcs = {int(request.get("function_code")) for request in clean_plan.get("requests", ()) if isinstance(request, Mapping) and request.get("function_code") is not None}
    check["read-plan"].check("read-plan-only-uses-read-functions", fcs <= {1, 2, 3, 4}, details={"function_codes": sorted(fcs)})

    clean_pack_one = work / "clean-pack-one"
    clean_receipt, clean_manifest = _run_pack(runner, clean_path, clean_plan_path, clean_pack_one, mode="final")
    check["clean-final-pack"].check("clean-final-pack-generated", clean_receipt.get("status") == "generated" and clean_manifest.get("status") == "generated", details={"status": clean_receipt.get("status"), "targets": [item.get("target") for item in clean_manifest.get("targets", ()) if isinstance(item, Mapping)]})

    byte_path, byte_map = diagnosed[roles["byte_order"]]
    byte_plan_path = work / "plans" / "byte-before.json"
    _, byte_plan = _compile(runner, byte_path, byte_plan_path)
    probe_receipt, probe_manifest = _run_pack(runner, byte_path, byte_plan_path, work / "byte-probe", mode="probe")
    final_receipt, final_manifest = _run_pack(runner, byte_path, byte_plan_path, work / "byte-final-held", mode="final")
    check["byte-order-probe-and-final-hold"].check("probe-generates-before-byte-order-confirmation", probe_receipt.get("status") == "generated", details={"status": probe_receipt.get("status")})
    check["byte-order-probe-and-final-hold"].check("final-is-held-before-byte-order-confirmation", final_receipt.get("status") == "held" and bool(final_manifest.get("holds")), details={"status": final_receipt.get("status"), "hold_codes": sorted(_codes(final_manifest))})
    probe_node_red = _node_red_summary(work / "byte-probe" / "node-red" / "flow.json")
    probe_requests = len(byte_plan.get("requests", ()))
    check["byte-order-probe-and-final-hold"].check(
        "node-red-probe-is-manual-one-shot",
        probe_node_red["counts"].get("inject", 0) == 1
        and 0 < probe_node_red["counts"].get("modbus-flex-getter", 0) <= probe_requests
        and probe_node_red["counts"].get("modbus-read", 0) == 0
        and probe_node_red["manual_injects"]
        and "Run bounded read plan" in probe_node_red["names"],
        details={"request_count": probe_requests, "inject_count": probe_node_red["counts"].get("inject", 0), "getter_count": probe_node_red["counts"].get("modbus-flex-getter", 0)},
    )
    check["byte-order-probe-and-final-hold"].check(
        "node-red-probe-imports-disabled-and-read-only",
        probe_node_red["tabs_disabled"] and probe_node_red["write_nodes"] == 0,
        details={"tabs_disabled": probe_node_red["tabs_disabled"], "write_node_count": probe_node_red["write_nodes"]},
    )

    byte_ids = _find_byte_points(byte_map)
    byte_points = {_point_id(point): point for point in byte_map.get("points", ()) if isinstance(point, Mapping)}
    if not byte_ids:
        raise WorkflowFailure("byte-order role must contain at least one unresolved byte-order point")
    selected_id, selected, byte_profile = _select_byte_order_point(
        byte_ids, byte_points
    )
    capture = work / "capture.json"
    _write_json(capture, _capture_for_point(selected, byte_profile))
    evidence_path = work / "byte-order-evidence.json"
    byte_receipt = runner.run(
        "check-byte-order",
        [
            "--input",
            capture,
            "--types",
            ",".join(byte_profile.datatypes),
            "--output",
            evidence_path,
        ],
    )
    evidence = _json(evidence_path)
    identity = evidence.get("sample_identity", {}) if isinstance(evidence, Mapping) else {}
    evidence_sample = evidence.get("sample", {}) if isinstance(evidence, Mapping) else {}
    candidate_count = len(evidence.get("candidates", ())) if isinstance(evidence, Mapping) else 0
    check["byte-order-evidence"].check(
        "all-width-specific-layout-and-type-candidates",
        byte_receipt.get("candidates") == byte_profile.expected_candidate_count
        and candidate_count == byte_profile.expected_candidate_count
        and evidence_sample.get("bit_width") == byte_profile.word_count * 16
        and len(evidence_sample.get("words", ())) == byte_profile.word_count,
        details={
            "candidate_count": byte_receipt.get("candidates"),
            "expected_candidate_count": byte_profile.expected_candidate_count,
            "word_count": byte_profile.word_count,
            "datatype_family": list(byte_profile.datatypes),
        },
    )
    expected_identity = _sample_identity(selected)
    check["byte-order-evidence"].check(
        "sample-identity-matches-selected-point",
        all(identity.get(field) == value for field, value in expected_identity.items()),
        details={"hold_codes": sorted(_codes(evidence))},
    )
    check["byte-order-evidence"].check("human-confirmation-remains-required", "byte-order-human-confirmation-required" in _codes(evidence), details={"hold_codes": sorted(_codes(evidence))})

    matching_candidate = next(
        (
            candidate
            for candidate in evidence.get("candidates", ())
            if isinstance(candidate, Mapping)
            and candidate.get("sample_id") == expected_identity["sample_id"]
            and candidate.get("datatype") == byte_profile.point_datatype
            and candidate.get("layout") == byte_profile.identity_layout
        ),
        None,
    )
    if matching_candidate is None:
        raise WorkflowFailure(
            "byte-order evidence does not contain the selected point datatype and identity layout"
        )

    evidence_hash = _semantic_hash(evidence)
    decisions = {
        "schema_version": "modbus-review-decisions/v1", "canonical_map_hash": _semantic_hash(byte_map), "review_id": "human-workflow-001", "reviewed_at": "2026-08-07T12:30:00Z", "reviewer": "workflow-test", "approve_map": True,
        "decisions": [{"point_id": selected_id, "action": "set", "field": "byte_order", "value": matching_candidate["layout"], "reason": "The human reviewed the complete candidate table for this exact raw sample and point datatype.", "evidence_refs": [f"sha256:{evidence_hash}"]}, *[
            {"point_id": identifier, "action": "exclude", "reason": "This bounded test does not approve this unrelated point.", "evidence_refs": ["human-workflow-scope"]}
            for identifier in byte_ids[1:]
        ]],
    }
    decision_path = work / "review-decisions.json"
    approved_path = work / "approved-map.json"
    _write_json(decision_path, decisions)
    decision_receipt = runner.run("apply-review", ["--map", byte_path, "--decisions", decision_path, "--evidence", evidence_path, "--output", approved_path])
    approved = _json(approved_path)
    check["review-decisions"].check("decision-applies-with-audit", decision_receipt.get("status") == "approved" and approved.get("approval"), details={"status": decision_receipt.get("status"), "excluded": decision_receipt.get("excluded"), "hold_codes": sorted(_codes(approved))})

    stale_receipt, stale_manifest = _run_pack(runner, approved_path, byte_plan_path, work / "stale-plan", mode="final")
    check["stale-plan-and-rebuild"].check("stale-plan-is-rejected", stale_receipt.get("status") == "held" and "PLAN_MAP_HASH_MISMATCH" in _codes(stale_manifest), details={"status": stale_receipt.get("status"), "hold_codes": sorted(_codes(stale_manifest))})
    rebuilt_plan_path = work / "plans" / "byte-approved.json"
    rebuilt_receipt, _ = _compile(runner, approved_path, rebuilt_plan_path)
    rebuilt_pack_receipt, rebuilt_manifest = _run_pack(runner, approved_path, rebuilt_plan_path, work / "byte-final", mode="final")
    check["stale-plan-and-rebuild"].check("rebuilt-final-pack-generates", rebuilt_receipt.get("status") == "planned" and rebuilt_pack_receipt.get("status") == "generated" and rebuilt_manifest.get("status") == "generated", details={"plan_status": rebuilt_receipt.get("status"), "pack_status": rebuilt_pack_receipt.get("status")})

    before_path, before_map = diagnosed[roles["compare_before"]]
    after_path, after_map = diagnosed[roles["compare_after"]]
    compare_path = work / "comparison.json"
    compare_receipt = runner.run("compare-maps", ["--before", before_path, "--after", after_path, "--output", compare_path])
    comparison = _json(compare_path)
    check["map-comparison"].check("revision-comparison-runs", compare_receipt.get("status") == "compared", details={"summary": comparison.get("summary", {})})
    # Reordering a serialized map must not create semantic changes.
    reorder_path = work / "reordered-map.json"
    reordered = dict(after_map)
    reordered["points"] = list(reversed(list(after_map.get("points", ()))))
    _write_json(reorder_path, reordered)
    reorder_output = work / "comparison-reordered.json"
    runner.run("compare-maps", ["--before", after_path, "--after", reorder_path, "--output", reorder_output])
    reordered_result = _json(reorder_output)
    check["map-comparison"].check("row-order-does-not-change-map", int(reordered_result.get("summary", {}).get("changed", 0)) == 0 and int(reordered_result.get("summary", {}).get("added", 0)) == 0 and int(reordered_result.get("summary", {}).get("removed", 0)) == 0, details={"summary": reordered_result.get("summary", {})})
    if after_map.get("points"):
        moved = dict(after_map)
        moved_points = [dict(point) for point in after_map["points"]]
        moved_points[0]["protocol_offset"] = int(moved_points[0].get("protocol_offset", 0)) + 1
        moved["points"] = moved_points
        moved_path = work / "moved-map.json"
        _write_json(moved_path, moved)
        moved_output = work / "comparison-moved.json"
        runner.run("compare-maps", ["--before", after_path, "--after", moved_path, "--output", moved_output])
        moved_result = _json(moved_output)
        check["map-comparison"].check("address-move-is-explicit", int(moved_result.get("summary", {}).get("moved", 0)) == 1, details={"summary": moved_result.get("summary", {})})

    point_pool: list[Mapping[str, Any]] = []
    seen_point_ids = {selected_id}
    for source in (clean_map, safety_map, byte_map):
        for point in source.get("points", ()):
            if not isinstance(point, Mapping):
                continue
            identifier = _point_id(point)
            if identifier and identifier not in seen_point_ids:
                point_pool.append(point)
                seen_point_ids.add(identifier)
    if len(point_pool) < 2:
        raise WorkflowFailure("live analysis needs three distinct real-map points")
    live_capture = _live_capture(selected, point_pool[0], point_pool[1])
    live_capture_path = work / "live-capture.json"
    _write_json(live_capture_path, live_capture)
    json_analysis = work / "capture-analysis.json"
    csv_capture = work / "capture.csv"
    _write_live_capture_csv(csv_capture, live_capture)
    primary_id = selected_id
    flat_id = _point_id(point_pool[0])
    missing_id = _point_id(point_pool[1])
    analysis_options = work / "capture-analysis-options.json"
    _write_json(
        analysis_options,
        {
            "expected_interval_seconds": {primary_id: 10, flat_id: 10},
            "stale_after_seconds": {primary_id: 15, flat_id: 15, missing_id: 15},
            "flatline_min_samples": 3,
            "ranges": {primary_id: {"minimum": 0, "maximum": 100}},
            "rate_limits": {primary_id: 2},
        },
    )
    csv_analysis = work / "capture-csv-analysis.json"
    json_receipt = runner.run(
        "analyze-capture",
        ["--input", live_capture_path, "--now", "2026-08-07T12:01:40Z", "--output", json_analysis],
    )
    csv_receipt = runner.run(
        "analyze-capture",
        ["--input", csv_capture, "--format", "csv", "--options", analysis_options, "--now", "2026-08-07T12:01:40Z", "--output", csv_analysis],
    )
    json_value, csv_value = _json(json_analysis), _json(csv_analysis)
    check["live-capture-analysis"].check("json-capture-is-analyzed", json_receipt.get("status") == "analyzed" and bool(json_value.get("points")), details={"status": json_receipt.get("status")})
    check["live-capture-analysis"].check("csv-capture-is-analyzed", csv_receipt.get("status") == "analyzed" and bool(csv_value.get("points")), details={"status": csv_receipt.get("status")})
    expected_json_summary = {
        "duplicate_samples": 1,
        "estimated_missing_intervals": 1,
        "flatline_points": 1,
        "range_violations": 1,
        "rate_of_change_violations": 2,
        "missing_points": 1,
        "stale_points": 2,
    }
    check["live-capture-analysis"].check(
        "json-analysis-finds-injected-events",
        all(json_value.get("summary", {}).get(key) == value for key, value in expected_json_summary.items())
        and json_value.get("communications", {}).get("error_count") == 1,
        details={"summary": {key: json_value.get("summary", {}).get(key) for key in expected_json_summary}, "communication_errors": json_value.get("communications", {}).get("error_count")},
    )
    expected_csv_summary = {
        key: value for key, value in expected_json_summary.items()
        if key != "missing_points"
    }
    check["live-capture-analysis"].check(
        "csv-analysis-finds-injected-events",
        all(csv_value.get("summary", {}).get(key) == value for key, value in expected_csv_summary.items())
        and not csv_value.get("holds"),
        details={"summary": {key: csv_value.get("summary", {}).get(key) for key in expected_csv_summary}, "hold_codes": sorted(_codes(csv_value))},
    )
    byte_evidence = json_value.get("points", {}).get(primary_id, {}).get("byte_order_evidence", {})
    check["live-capture-analysis"].check(
        "live-byte-order-analysis-is-evidence-only",
        byte_evidence.get("automatic_selection") is False
        and "winner" not in byte_evidence
        and "selected_layout" not in byte_evidence,
        details={"candidate_count": byte_evidence.get("candidate_count"), "automatic_selection": byte_evidence.get("automatic_selection")},
    )

    clean_pack_two = work / "clean-pack-two"
    _, _ = _run_pack(runner, clean_path, clean_plan_path, clean_pack_two, mode="final")
    files_one = {path.relative_to(clean_pack_one).as_posix(): _sha256(path) for path in clean_pack_one.rglob("*") if path.is_file()}
    files_two = {path.relative_to(clean_pack_two).as_posix(): _sha256(path) for path in clean_pack_two.rglob("*") if path.is_file()}
    check["artifact-safety-and-determinism"].check("pack-is-deterministic", files_one == files_two, details={"file_count": len(files_one)})
    with ZipFile(clean_pack_one / "tool-pack.zip") as archive:
        zipped_names = set(archive.namelist())
    check["artifact-safety-and-determinism"].check("pack-zip-has-manifest-and-checksums", {"manifest.json", "checksums.sha256"} <= zipped_names, details={"file_count": len(zipped_names)})
    check["artifact-safety-and-determinism"].check("pack-manifest-has-no-local-or-secret-text", not _contains_forbidden(clean_manifest), details={"checked": "manifest-structure"})
    checksum_valid, checksum_count = _verify_checksum_file(clean_pack_one)
    check["artifact-safety-and-determinism"].check("pack-checksums-verify", checksum_valid, details={"checked_files": checksum_count})
    final_node_red = _node_red_summary(clean_pack_one / "node-red" / "flow.json")
    check["artifact-safety-and-determinism"].check(
        "node-red-final-matches-read-plan",
        final_node_red["counts"].get("inject", 0) == 1
        and 0 < final_node_red["counts"].get("modbus-flex-getter", 0) <= len(clean_plan.get("requests", ()))
        and final_node_red["counts"].get("modbus-read", 0) == 0
        and final_node_red["manual_injects"]
        and final_node_red["tabs_disabled"]
        and final_node_red["write_nodes"] == 0
        and "Run bounded read plan" in final_node_red["names"]
        and "Write capture.json" in final_node_red["names"],
        details={"request_count": len(clean_plan.get("requests", ())), "getter_count": final_node_red["counts"].get("modbus-flex-getter", 0), "inject_count": final_node_red["counts"].get("inject", 0), "tabs_disabled": final_node_red["tabs_disabled"], "write_node_count": final_node_red["write_nodes"]},
    )
    commands = (clean_pack_one / "modpoll" / "gavinying-cli" / "commands.txt").read_text(encoding="utf-8")
    check["artifact-safety-and-determinism"].check(
        "modpoll-is-one-bounded-read-pass",
        "--once" in commands and "write" not in commands.lower(),
        details={"once_flag": "--once" in commands},
    )
    with (clean_pack_one / "modscan" / "read-plan.csv").open(newline="", encoding="utf-8") as source:
        modscan_rows = list(csv.DictReader(source))
    modscan_codes = {int(row["function_code"]) for row in modscan_rows}
    check["artifact-safety-and-determinism"].check(
        "modscan-plan-is-read-only",
        bool(modscan_rows) and modscan_codes <= {1, 2, 3, 4},
        details={"function_codes": sorted(modscan_codes), "request_count": len(modscan_rows)},
    )

    return {
        "schema_version": REPORT_SCHEMA,
        "status": "passed" if all(case.status == "passed" for case in cases) else "failed",
        "corpus": {"map_count": len(map_entries), "case_roles": sorted(roles)},
        "cases": [{"id": case.case_id, "status": case.status, "checks": case.checks, **({"note": case.note} if case.note else {})} for case in cases],
        "wrapper_calls": runner.receipts,
    }


def _markdown(report: Mapping[str, Any]) -> str:
    lines = ["# Human Workflow Test Report", "", f"Status: **{report['status']}**", "", "| Workflow | Status | Checks |", "| --- | --- | ---: |"]
    for case in report.get("cases", ()):
        if not isinstance(case, Mapping):
            continue
        checks = case.get("checks", ())
        passed = sum(1 for item in checks if isinstance(item, Mapping) and item.get("passed"))
        lines.append(f"| `{case.get('id')}` | {case.get('status')} | {passed}/{len(checks)} |")
    lines.extend(["", "The detailed JSON report contains only case IDs, statuses, counts, and finding codes. It does not include map rows, source text, or local paths.", ""])
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", required=True, type=Path, help="local-only map corpus directory")
    parser.add_argument("--output", required=True, type=Path, help="directory for local test artifacts and reports")
    args = parser.parse_args(argv)
    try:
        report = run_workflow(args.corpus_dir.resolve(), args.output.resolve())
    except WorkflowFailure as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _write_json(args.output / "human-workflow-report.json", report)
    (args.output / "human-workflow-report.md").write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": "human-workflow-report.json"}, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
