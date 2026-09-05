#!/usr/bin/env python3
"""Predeclare and directly execute the synthetic 20-skill/four-category matrix.

Reports describe direct wrapper behavior, never actual-model routing, native
application acceptance, human approval, or private-source fidelity. No generated
probe, flow, command, or fallback is enabled. Failed assertions stay failed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    "analyze-capture", "apply-review", "build-custom-export", "build-modpoll", "build-modscan",
    "build-node-red", "build-tool-pack", "capture-sample", "check-byte-order", "check-map",
    "compare-maps", "compile-user-map", "extract-pdf-map", "modbus-help", "normalize-map",
    "parse-map", "plan-reads", "remap-addresses", "review-evidence", "review-map",
)
CATEGORIES = ("positive", "negative", "incomplete", "unsafe")
TARGETS = {"build-node-red": "node-red", "build-modpoll": "modpoll", "build-modscan": "modscan"}
CONTRACTS = {
    "analyze-capture": ("two samples and configured checks", "timezone-free timestamp", "thresholds absent, checks must be skipped", "requested sample bound exceeds hard maximum"),
    "apply-review": ("synthetic role changes a name-independent engineering unit without approval", "wrong map hash", "decision lacks evidence reference", "attempt to convert source write-only access to readable"),
    "build-custom-export": ("documented CSV columns", "unknown example field", "missing required target field", "executable-looking template placeholder"),
    "build-modpoll": ("proconX final uint16 generation", "stale plan hash", "unknown float32 layout in final mode", "write-only FC06 source map"),
    "build-modscan": ("final uint16 operator plan generation", "stale plan hash", "unknown float32 layout in final mode", "write-only FC06 source map"),
    "build-node-red": ("disabled final uint16 read flow", "stale plan hash", "unknown float32 layout in final mode", "write-only FC06 source map"),
    "build-tool-pack": ("all three selected targets", "empty target set", "unknown float32 layout in final mode", "write-only FC06 source map"),
    "capture-sample": ("one disabled/operator-controlled probe, no capture", "invalid negative offset", "missing unit identity", "broadcast unit zero"),
    "check-byte-order": ("four float32 candidates from one immutable sample", "word outside uint16 range", "missing route identity", "broadcast unit zero"),
    "check-map": ("one fully specified read point", "overlapping different logical IDs", "unknown datatype and width", "write-only FC06 and broadcast unit zero"),
    "compare-maps": ("same ID moves from offset10 to12", "schema mismatch", "unknown unit identity", "access changes from read-only to write-only; preserve the unsafe change as evidence"),
    "compile-user-map": ("complete raw JSON readable source", "malformed source JSON", "unknown datatype", "raw R/W=W and FC06 source row"),
    "extract-pdf-map": ("one synthetic physical-page register row", "not a PDF", "one page with no register table", "page request exceeds256-page bound"),
    "modbus-help": ("OEM source to compiler route", "unrelated non-Modbus goal", "ambiguous goal without artifacts", "request a write or broadcast"),
    "normalize-map": ("explicit register identity and uint16", "offset65536", "no datatype", "write-only FC06 and broadcast unit zero"),
    "parse-map": ("raw JSON aliases and source locator", "malformed JSON", "addressed row without datatype", "XML external entity declaration"),
    "plan-reads": ("one FC03 request offset10 count1", "invalid offset65536", "missing unit identity", "write-only FC06 and broadcast unit zero"),
    "remap-addresses": ("offset10 to40011 without physical move", "offset65536", "unproven area", "notation transform preserves write-only FC06 and its explicit existing safety hold"),
    "review-evidence": ("complete point receives a disposition", "object with no points/records array", "unknown datatype", "write-only FC06 and broadcast unit zero"),
    "review-map": ("clean JSON draft and compact report", "malformed JSON", "unknown datatype", "write-only FC06 and broadcast unit zero"),
}


def semantic_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def tree_hash(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def predeclared_cases():
    result = []
    for skill in SKILLS:
        for category, intent in zip(CATEGORIES, CONTRACTS[skill]):
            result.append({"case_id": f"{skill}--{category}", "skill": skill, "category": category,
                "source": "public-safe synthetic inputs only", "input_intent": intent,
                "entrypoint": f"skills/{skill}/scripts/run.py" if skill != "modbus-help" else None,
                "required_checks": ["bounded process", "immutable input bytes", "same frozen plugin",
                                    "no denied operation attempts", f"{skill}/{category} artifact assertions"],
                "model_needed": skill == "modbus-help", "oracle_version": "direct-skill-acceptance/v2",
                "expected": expected_outcome(skill, category)})
    return result


def expected_outcome(skill, category):
    if skill == "modbus-help":
        return "not-run: instruction-only skill requires a separately evidenced actual-model trial"
    if category == "positive":
        return "success with the exact skill-specific synthetic fields/artifacts; native state unverified"
    if skill == "parse-map" and category == "incomplete":
        return "one traceable candidate with no invented datatype"
    if skill == "analyze-capture" and category == "incomplete":
        return "analysis explicitly skips absent thresholds; stale is null"
    if skill == "compare-maps" and category == "unsafe":
        return "read-only evidence preserves the write-only access change; neither map is mutated"
    if skill == "remap-addresses" and category == "unsafe":
        return "notation conversion may apply but must preserve physical identity, FC06/write-only, pending state, and every existing safety hold"
    return "explicit rejection or held/blocking artifact; no ready/approved executable artifact"


def simple_pdf(lines):
    commands = ["BT /F1 10 Tf 40 760 Td"]
    for index, line in enumerate(lines):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(("0 -20 Td " if index else "") + f"({escaped}) Tj")
    stream = ("\n".join(commands) + "\nET\n").encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>", b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream"]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return bytes(data)


def fixtures(directory):
    directory.mkdir()
    point = {"logical_point_id": "synthetic-point", "name": "Synthetic Readback", "route_id": "synthetic-route",
        "unit_id": 7, "area": "holding-register", "protocol_offset": 10, "datatype": "uint16", "word_span": 1,
        "access": "read-only", "function_code": 3, "scale": 1, "engineering_offset": 0,
        "normalization_status": "confirmed", "_source": {"format": "json", "index": 0}}
    variants = {"good": point, "bad-offset": {**point, "protocol_offset": 65536},
        "unknown": {**point, "datatype": "unknown", "word_span": None, "normalization_status": "pending"},
        "no-unit": {**point, "unit_id": None}, "no-area": {**point, "area": None},
        "write": {**point, "access": "write-only", "function_code": 6},
        "unsafe": {**point, "access": "write-only", "function_code": 6, "unit_id": 0},
        "moved": {**point, "protocol_offset": 12},
        "multi": {**point, "datatype": "float32", "word_span": 2, "byte_order": "ABCD", "byte_order_confirmed": True},
        "unknown-layout": {**point, "datatype": "float32", "word_span": 2, "byte_order": None, "byte_order_confirmed": False}}
    for name, value in variants.items():
        write_json(directory / f"{name}.json", {"schema_version": "modbus-map/v1", "points": [value]})
        write_json(directory / f"{name}-raw.json", {"records": [value]})
    write_json(directory / "remap-unsafe.json", {"schema_version": "modbus-map/v1", "points": [{**variants["write"], "normalization_status": "pending"}],
        "holds": [{"code": "point.write-only-not-readable", "severity": "hold", "blocking": True,
                   "point_ids": ["synthetic-point"], "message": "Synthetic source explicitly marks this point write-only."}]})
    write_json(directory / "overlap.json", {"points": [point, {**point, "logical_point_id": "other-point"}]})
    write_json(directory / "schema-mismatch.json", {"schema_version": "unrelated/v99", "points": [point]})
    write_json(directory / "empty-object.json", {})
    (directory / "broken.json").write_text('{"records": [')
    write_json(directory / "parse-good.json", {"records": [{"Point ID": "synthetic-point", "Name": "Synthetic Readback",
        "Protocol Offset": 10, "Area": "holding-register", "Datatype": "uint16", "R/W": "R"}]})
    write_json(directory / "parse-incomplete.json", {"records": [{"Point ID": "synthetic-point", "Address": "40011", "Name": "Synthetic Readback"}]})
    (directory / "unsafe.xml").write_text('<!DOCTYPE root [<!ENTITY external SYSTEM "file:///nonexistent-synthetic-sentinel">]><root><register><address>10</address><name>&external;</name></register></root>')
    (directory / "good.pdf").write_bytes(simple_pdf(["Address       Name                      Data Type", "40011         Synthetic Readback        uint16"]))
    (directory / "no-table.pdf").write_bytes(simple_pdf(["Synthetic installation overview", "No register table is supplied on this page."]))
    (directory / "broken.pdf").write_bytes(b"not a PDF")
    (directory / "example.csv").write_text("logical_point_id,name,protocol_offset,datatype\nexample,Example,10,uint16\n")
    (directory / "unknown-example.csv").write_text("undocumented_field\nexample\n")
    write_json(directory / "missing-config.json", {"record_template": "{engineering_unit}"})
    write_json(directory / "unsafe-config.json", {"record_template": "{__class__}"})
    sample = {"sample_id": "synthetic-sample", "point_id": "synthetic-point", "route_id": "synthetic-route", "unit_id": 7,
        "area": "holding-register", "protocol_offset": 10, "timestamp": "2026-09-04T12:00:00Z", "raw_words": [0x42F6, 0], "value": 123, "response_ms": 8}
    for name, update in {"good": {}, "bad-word": {"raw_words": [65536, 0]}, "no-route": {"route_id": None},
                         "broadcast": {"unit_id": 0}, "bad-time": {"timestamp": "2026-09-04T12:00:00"}}.items():
        write_json(directory / f"capture-{name}.json", {"schema_version": "capture/v1", "samples": [{**sample, **update}]})
    write_json(directory / "analysis-good.json", {"schema_version": "capture/v1", "samples": [sample,
        {**sample, "sample_id": "synthetic-sample-2", "timestamp": "2026-09-04T12:00:10Z", "value": 124}]})
    write_json(directory / "analysis-options.json", {"now": "2026-09-04T12:00:20Z", "stale_after_seconds": 30,
        "ranges": {"synthetic-point": {"minimum": 0, "maximum": 200}}, "expected_interval_seconds": 10})
    write_json(directory / "unsafe-analysis-options.json", {"max_samples": 100001})
    for name, item in {"positive": point, "negative": variants["bad-offset"], "incomplete": variants["no-unit"], "unsafe": {**point, "unit_id": 0}}.items():
        write_json(directory / f"probe-{name}.json", {"targets": ["node-red"], "max_gap": 0, "points": [item]})
    for category, source in {"positive": "good-raw.json", "negative": "broken.json", "incomplete": "unknown-raw.json", "unsafe": "write-raw.json"}.items():
        write_json(directory / f"compile-{category}.json", {"schema_version": "modbus-compile-request/v1", "source": {"path": str(directory / source)},
            "selection_template": {"schema_version": "modbus-user-selection-template/v1", "requested_measurements": ["all documented Modbus read points"], "mode": "all-readable"}, "targets": [], "target_options": {}})
    return variants


def command_arguments(skill, category, inputs, output):
    def p(name): return str(inputs / name)
    result = str(output / "result.json")
    which = {"positive": "good", "negative": "bad-offset", "incomplete": "unknown", "unsafe": "unsafe"}[category]
    if skill in TARGETS:
        which = "unknown-layout" if category == "incomplete" else "write" if category == "unsafe" else "good"
        plan = "stale-plan.json" if category == "negative" else f"{which}-plan.json"
        return ["--map", p(which + ".json"), "--plan", p(plan), "--mode", "final", "--output", str(output / "bundle"),
                *(["--profile", "proconx-cli"] if skill == "build-modpoll" else [])]
    if skill == "build-tool-pack": return ["--request", p(f"pack-{category}.json"), "--output", str(output / "bundle")]
    if skill == "capture-sample": return ["--request", p(f"probe-{category}.json"), "--output", str(output / "bundle")]
    if skill == "compile-user-map": return ["--request", p(f"compile-{category}.json"), "--output", str(output / "case")]
    if skill == "apply-review":
        return ["--map", p("write.json" if category == "unsafe" else "good.json"), "--decisions", p(f"decision-{category}.json"), "--output", result]
    if skill == "check-byte-order":
        name = {"positive": "good", "negative": "bad-word", "incomplete": "no-route", "unsafe": "broadcast"}[category]
        return ["--input", p(f"capture-{name}.json"), "--types", "float32", "--output", result]
    if skill == "analyze-capture":
        args = ["--input", p("capture-bad-time.json" if category == "negative" else "analysis-good.json"), "--output", result]
        return args + (["--options", p("analysis-options.json" if category == "positive" else "unsafe-analysis-options.json")] if category in {"positive", "unsafe"} else [])
    if skill == "build-custom-export":
        args = ["--example", p("unknown-example.csv" if category == "negative" else "example.csv"), "--map", p("good.json"), "--output", str(output / "bundle")]
        return args + (["--config", p("missing-config.json" if category == "incomplete" else "unsafe-config.json")] if category in {"incomplete", "unsafe"} else [])
    if skill == "extract-pdf-map":
        name = "broken.pdf" if category == "negative" else "no-table.pdf" if category == "incomplete" else "good.pdf"
        return ["--input", p(name), "--output", str(output / "bundle"), *(["--pages", "1-257"] if category == "unsafe" else [])]
    if skill == "parse-map":
        name = {"positive": "parse-good.json", "negative": "broken.json", "incomplete": "parse-incomplete.json", "unsafe": "unsafe.xml"}[category]
        return ["--input", p(name), "--output", result]
    if skill == "remap-addresses":
        which = "no-area" if category == "incomplete" else "remap-unsafe" if category == "unsafe" else which
        return ["--input", p(which + ".json"), "--from", "protocol-offset", "--to", "modicon-reference", "--output", result]
    if skill == "compare-maps":
        name = {"positive": "moved", "negative": "schema-mismatch", "incomplete": "no-unit", "unsafe": "write"}[category]
        return ["--before", p("good.json"), "--after", p(name + ".json"), "--output", result]
    if skill == "review-map":
        return ["--input", p("broken.json" if category == "negative" else which + "-raw.json"), "--output", str(output / "bundle")]
    if skill == "review-evidence" and category == "negative": which = "empty-object"
    if skill == "check-map" and category == "negative": which = "overlap"
    if skill == "plan-reads" and category == "incomplete": which = "no-unit"
    return ["--input", p(which + ("-raw.json" if skill == "normalize-map" else ".json")), "--output", result]


def load_outputs(output):
    result = {}
    for path in sorted(output.rglob("*.json")):
        try: result[path.relative_to(output).as_posix()] = json.loads(path.read_text())
        except (ValueError, UnicodeError): continue
    return result


def has_blocker(value):
    if isinstance(value, dict):
        if value.get("status") in {"held", "blocked", "partial", "unsupported", "awaiting-selection-decision"}:
            return True
        if value.get("state") in {"partial", "awaiting-source-decision", "awaiting-selection-decision", "awaiting-byte-order-decision"}:
            return True
        if value.get("severity") in {"error", "hold"} and value.get("blocking") is not False:
            return True
        return any(has_blocker(item) for item in value.values())
    return isinstance(value, list) and any(has_blocker(item) for item in value)


def assess_case(spec, receipt, outputs, output):
    checks = []
    def check(name, success, detail=None): checks.append({"name": name, "passed": bool(success), "detail": detail})
    check("bounded process", not receipt.get("timed_out"))
    check("bounded captured output", not receipt.get("output_limit_exceeded"))
    check("immutable input bytes", receipt.get("inputs_unchanged"))
    check("same frozen plugin", receipt.get("plugin_unchanged"))
    audit = receipt.get("audit")
    check("audit receipt present and no denied operation attempts", isinstance(audit, dict) and not audit.get("denied"), audit.get("denied") if audit else None)
    check("no unhandled traceback", "Traceback (most recent call last)" not in receipt.get("stderr", ""))
    skill, category = spec["skill"], spec["category"]
    result = outputs.get("result.json", {})
    succeeded = receipt.get("returncode") == 0
    rejected = receipt.get("returncode") not in (None, 0) and not receipt.get("timed_out")
    if category != "positive":
        if skill == "parse-map" and category == "incomplete":
            rows = result.get("records", [])
            check("traceable candidate retains unknown datatype", succeeded and len(rows) == 1 and rows[0].get("datatype") is None and rows[0].get("_source"))
        elif skill == "analyze-capture" and category == "incomplete":
            point = result.get("points", {}).get("synthetic-point", {})
            check("absent threshold visibly skipped", succeeded and point.get("stale") is None and point.get("checks", {}).get("stale", {}).get("status") == "skipped")
        elif skill == "analyze-capture" and category == "negative":
            check("timezone-free sample explicitly rejected", succeeded and any(item.get("code") == "TIMESTAMP_INVALID" for item in result.get("rejected_samples", [])) and not result.get("points"))
        elif skill == "compare-maps" and category == "unsafe":
            check("unsafe access change remains explicit evidence", succeeded and "write-only" in json.dumps(result) and "access" in json.dumps(result))
        elif skill == "remap-addresses" and category == "unsafe":
            rows = result.get("points", [])
            check("notation retains unsafe source fields without promotion", succeeded and len(rows) == 1
                  and rows[0].get("access") == "write-only" and rows[0].get("function_code") == 6
                  and rows[0].get("protocol_offset") == 10 and rows[0].get("normalization_status") == "pending")
            check("explicit existing safety hold survives conversion", any(item.get("code") == "point.write-only-not-readable"
                  and item.get("blocking") is not False for item in result.get("holds", [])))
        else:
            check("explicit rejection or blocking outcome", rejected or any(has_blocker(value) for value in outputs.values()))
            if skill == "remap-addresses": check("no applied points", not result.get("points"))
            if skill in TARGETS or skill == "build-tool-pack":
                receipts = [value for name, value in outputs.items() if name.endswith("-result.json")]
                check("no generated target claims", all(item.get("status") != "generated" for item in receipts))
        return checks
    check("successful direct wrapper exit", succeeded)
    if skill == "parse-map":
        rows = result.get("records", [])
        check("one exact alias-parsed source row", len(rows) == 1 and rows[0].get("protocol_offset") == 10 and rows[0].get("logical_point_id") == "synthetic-point" and rows[0].get("access") == "R" and rows[0].get("_source"))
    elif skill in {"normalize-map", "apply-review"}:
        rows = result.get("points", [])
        check("one point retains physical identity", len(rows) == 1 and rows[0].get("protocol_offset") == 10 and rows[0].get("unit_id") == 7 and rows[0].get("logical_point_id") == "synthetic-point")
        if skill == "apply-review":
            check("synthetic decision applied without approval", bool(rows) and rows[0].get("engineering_unit") == "synthetic-units" and result.get("review_status") != "approved")
    elif skill == "plan-reads":
        requests = result.get("requests", [])
        check("exact one bounded FC03 request", len(requests) == 1 and requests[0].get("function_code") == 3 and requests[0].get("unit_id") == 7 and requests[0].get("start_offset") == 10 and requests[0].get("quantity") == 1)
    elif skill in {"check-map", "review-evidence", "review-map"}:
        check("review artifacts exist", bool(outputs))
        check("clean point has no blocking finding", not any(has_blocker(value) for value in outputs.values()))
        if skill == "review-map": check("all four stage artifacts", {"bundle/map-draft.json", "bundle/parsed.json", "bundle/review.json", "bundle/lint.json"} <= outputs.keys())
    elif skill == "remap-addresses":
        conversions = result.get("conversions", [])
        check("offset10 becomes40011 without a physical move", len(conversions) == 1 and conversions[0].get("protocol_offset") == 10 and str(conversions[0].get("target", {}).get("value")) == "40011" and result.get("status") == "ready")
    elif skill == "compare-maps":
        check("one move is reported", len(result.get("moved", [])) == 1)
    elif skill == "check-byte-order":
        candidates = result.get("candidates", [])
        check("all four float32 layouts, one sample, no winner", len(candidates) == 4 and {item.get("layout") for item in candidates} == {"ABCD", "BADC", "CDAB", "DCBA"} and {item.get("sample_id") for item in candidates} == {"synthetic-sample"} and not any(key in result for key in ("selected_layout", "winner", "approved_layout")))
        check("ABCD independently decodes123", any(item.get("layout") == "ABCD" and item.get("decoded_value") == 123 for item in candidates))
    elif skill == "analyze-capture":
        point = result.get("points", {}).get("synthetic-point", {})
        check("two samples and evaluated stale check", point.get("sample_count") == 2 and point.get("stale") is False and point.get("checks", {}).get("stale", {}).get("status") != "skipped")
    elif skill == "extract-pdf-map":
        extraction = outputs.get("bundle/pdf-extraction.json", {})
        rows = extraction.get("records", [])
        check("one exact source register with page locator", len(rows) == 1 and "40011" in json.dumps(rows[0]) and bool(rows[0].get("_source")))
    elif skill == "compile-user-map":
        compiled = outputs.get("case/compile-result.json", {})
        rows = outputs.get("case/output/user-map.json", {}).get("points", [])
        check("complete readable offline map", compiled.get("state") == "offline-complete" and len(rows) == 1 and rows[0].get("protocol_offset") == 10 and rows[0].get("function_code") == 3)
    elif skill == "build-custom-export":
        path = output / "bundle/rendered-output.txt"
        check("exact target fields are rendered", path.is_file() and "synthetic-point" in path.read_text() and "uint16" in path.read_text())
    else:
        check("target/probe bundle exists", any(name.endswith("-result.json") for name in outputs))
        check("no held target", not any(has_blocker(value) for value in outputs.values()))
        flow = next((value for name, value in outputs.items() if name.endswith("flow.json")), None)
        if flow is not None:
            check("disabled flow and no write node", any(node.get("type") == "tab" and node.get("disabled") is True for node in flow) and not any(node.get("type") in {"modbus-write", "modbus-flex-write"} for node in flow))
        if skill == "capture-sample":
            check("no physical capture emitted", not any(name.endswith("capture.json") for name in outputs))
            try: handoff = json.loads(receipt.get("stdout", ""))
            except ValueError: handoff = {}
            check("one live-read gate presented", handoff.get("next_action", {}).get("action") == "present-live-read-gate")
        verification = []
        for name, value in outputs.items():
            if name.endswith("-result.json"):
                verification.extend(item.get("verification") for item in value.get("targets", []) if isinstance(item, dict))
                if "verification" in value: verification.append(value["verification"])
        check("native verification visibly unrun", bool(verification) and all(value in {"not-run", "unavailable"} for value in verification))
    return checks


def execute(wrapper, arguments, output, private_temp, inputs, plugin, plugin_hash, timeout):
    output.mkdir(parents=True)
    audit = output / "operation-audit.json"
    command = [sys.executable, str(ROOT / "scripts/direct_skill_guard.py"), "--wrapper", str(wrapper),
        "--output-root", str(output), "--temporary-root", str(private_temp), "--audit", str(audit), "--", *arguments]
    environment = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT"}}
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(private_temp)})
    before = tree_hash(inputs)
    started = time.monotonic()
    with tempfile.TemporaryFile(dir=private_temp) as stdout, tempfile.TemporaryFile(dir=private_temp) as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr, env=environment, cwd=output, start_new_session=os.name == "posix")
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "posix": os.killpg(process.pid, signal.SIGKILL)
            else: process.kill()
            process.wait()
        sizes = [stream.tell() for stream in (stdout, stderr)]
        stdout.seek(0)
        stderr.seek(0)
        receipt = {"returncode": process.returncode, "stdout": stdout.read(1_000_000).decode("utf-8", errors="replace"),
            "stderr": stderr.read(1_000_000).decode("utf-8", errors="replace"), "timed_out": timed_out,
            "output_limit_exceeded": any(size > 1_000_000 for size in sizes)}
    after = tree_hash(inputs)
    receipt.update({"command": command, "elapsed_seconds": round(time.monotonic()-started, 6), "inputs_unchanged": after == before,
        "input_tree_hash": before, "input_tree_hash_after": after,
        "plugin_hash": plugin_hash, "plugin_unchanged": tree_hash(plugin) == plugin_hash,
        "audit": json.loads(audit.read_text()) if audit.is_file() else None})
    return receipt


def prepare_plans(inputs, run, plugin, private_temp, plugin_hash):
    receipts = []
    for name in ("good", "multi"):
        out = run / "preparation" / name
        receipt = execute(plugin / "skills/plan-reads/scripts/run.py", ["--input", str(inputs / f"{name}.json"), "--output", str(out / "plan.json")], out, private_temp, inputs, plugin, plugin_hash, 60)
        receipts.append(receipt)
        if receipt["returncode"] != 0 or not (out / "plan.json").is_file():
            raise RuntimeError("synthetic plan prerequisite failed")
        shutil.copy2(out / "plan.json", inputs / f"{name}-plan.json")
    good_plan = json.loads((inputs / "good-plan.json").read_text())
    for name in ("write", "unknown-layout"):
        plan = json.loads((inputs / ("multi-plan.json" if name == "unknown-layout" else "good-plan.json")).read_text())
        plan["input_hashes"]["canonical_map"] = semantic_hash(json.loads((inputs / f"{name}.json").read_text()))
        write_json(inputs / f"{name}-plan.json", plan)
    stale = copy.deepcopy(good_plan)
    stale["input_hashes"]["canonical_map"] = "0" * 64
    write_json(inputs / "stale-plan.json", stale)
    for category in CATEGORIES:
        name = "unknown-layout" if category == "incomplete" else "write" if category == "unsafe" else "good"
        write_json(inputs / f"pack-{category}.json", {"mode": "final", "map": str(inputs / f"{name}.json"), "read_plan": str(inputs / f"{name}-plan.json"),
            "targets": [] if category == "negative" else [{"id": "node-red"}, {"id": "modpoll", "profile": "proconx-cli"}, {"id": "modscan"}]})
        map_name = "write" if category == "unsafe" else "good"
        decision = {"schema_version": "modbus-review-decisions/v1", "canonical_map_hash": semantic_hash(json.loads((inputs / f"{map_name}.json").read_text())),
            "review_id": "synthetic-acceptance-only", "reviewed_at": "2026-09-04T12:00:00Z", "reviewer": "synthetic-test-role-not-human-approval", "approve_map": False,
            "decisions": [{"point_id": "synthetic-point", "action": "set", "field": "access" if category == "unsafe" else "engineering_unit", "value": "read-only" if category == "unsafe" else "synthetic-units",
                "reason": "Public synthetic acceptance case only; no commissioning approval.", "evidence_refs": ["synthetic:fixture:point"]}]}
        if category == "negative": decision["canonical_map_hash"] = "0" * 64
        if category == "incomplete": decision["decisions"][0].pop("evidence_refs")
        write_json(inputs / f"decision-{category}.json", decision)
    return receipts


def run_matrix(output, selected=None):
    output = output.resolve()
    if output.exists() and any(output.iterdir()): raise ValueError("output must be empty; historical receipts cannot be overwritten")
    if output.is_relative_to(ROOT) and not output.is_relative_to(ROOT / "artifacts"):
        raise ValueError("repository output must be under ignored artifacts")
    if output.is_relative_to(ROOT) and subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--quiet", "--", str(output)], check=False).returncode:
        raise ValueError("repository output must be ignored")
    output.mkdir(parents=True, exist_ok=True)
    specs = predeclared_cases()
    if selected: specs = [spec for spec in specs if spec["case_id"] in selected]
    harness_hashes = {name: hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest() for name in ("run_direct_skill_acceptance.py", "direct_skill_guard.py")}
    manifest = {"schema_version": "direct-skill-expectations/v1", "predeclared_at": datetime.now(timezone.utc).isoformat(), "cases": specs,
        "harness_hashes": harness_hashes,
        "limits": ["No actual-model behavior asserted", "No native target executions", "No full-source oracle claims", "Typed synthetic decisions never assert human approval"]}
    manifest_path = output / "predeclared-expectations.json"
    write_json(manifest_path, manifest)
    manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    results = []
    with tempfile.TemporaryDirectory(prefix="direct-skill-private-") as temporary:
        private_temp = Path(temporary)
        plugin = private_temp / "plugin"
        original = ROOT / "plugins/modbus-skills"
        before = tree_hash(original)
        shutil.copytree(original, plugin, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        plugin_hash = tree_hash(plugin)
        if before != tree_hash(original) or before != plugin_hash: raise RuntimeError("plugin changed while freezing")
        inputs = output / "inputs"
        fixtures(inputs)
        preparation = prepare_plans(inputs, output, plugin, private_temp, plugin_hash)
        write_json(output / "preparation-receipts.json", preparation)
        for spec in specs:
            if spec["model_needed"]:
                results.append({**spec, "status": "not-run", "reason": "direct-entrypoint-unavailable; root-owned actual-model routing evidence required"})
                continue
            case_output = output / "cases" / spec["case_id"]
            args = command_arguments(spec["skill"], spec["category"], inputs, case_output)
            receipt = execute(plugin / spec["entrypoint"], args, case_output, private_temp, inputs, plugin, plugin_hash, 90)
            outputs = load_outputs(case_output)
            checks = assess_case(spec, receipt, outputs, case_output)
            record = {**spec, "status": "passed" if all(item["passed"] for item in checks) else "failed", "checks": checks,
                "receipt": receipt, "artifacts": {path.relative_to(case_output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(case_output.rglob("*")) if path.is_file()},
                "evidence_class": "direct-skill-wrapper", "actual_model": False}
            write_json(case_output / "acceptance-receipt.json", record)
            results.append(record)
            print(f"{spec['case_id']}: {record['status']}", flush=True)
        report = {"schema_version": "direct-skill-acceptance/v1", "plugin_hash": plugin_hash, "predeclared_sha256": manifest_digest,
            "expectations_unchanged": hashlib.sha256(manifest_path.read_bytes()).hexdigest() == manifest_digest,
            "harness_unchanged": all(hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest() == digest for name, digest in harness_hashes.items()),
            "results": results, "counts": {status: sum(item["status"] == status for item in results) for status in ("passed", "failed", "not-run")},
            "limits": manifest["limits"], "matrix_complete": len(results) == 80 and all(item["status"] == "passed" for item in results)}
        write_json(output / "acceptance-report.json", report)
    report["cleanup"] = {"private_temporary_removed": not private_temp.exists()}
    write_json(output / "acceptance-report.json", report)
    lines = ["# Direct skill acceptance matrix", "", f"Counts: {report['counts']}", "", "This is direct wrapper evidence, not actual-model, native-product or source-fidelity acceptance.", "",
        "| Skill | Positive | Negative | Incomplete | Unsafe |", "|---|---|---|---|---|"]
    for skill in SKILLS:
        statuses = {item["category"]: item["status"] for item in results if item["skill"] == skill}
        lines.append("| " + skill + " | " + " | ".join(statuses.get(category, "not selected") for category in CATEGORIES) + " |")
    lines += ["", "## Failed assertions", ""]
    for result in results:
        for check in result.get("checks", []):
            if not check["passed"]: lines.append(f"- {result['case_id']}: {check['name']}")
    lines += ["", "Four instruction-only modbus-help slots require separately recorded actual-model trials. No mock router is substituted.", ""]
    (output / "report.md").write_text("\n".join(lines))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", help="Optional exact skill--category subset; never represents all80")
    args = parser.parse_args(argv)
    known = {item["case_id"] for item in predeclared_cases()}
    if args.case and set(args.case) - known: parser.error("unknown case selection")
    report = run_matrix(args.output, args.case)
    print(json.dumps(report["counts"]))
    return 0 if not report["counts"]["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
