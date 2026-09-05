#!/usr/bin/env python3
"""Record corpus pipeline evidence without equating legacy scores with fidelity."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", help="Run one generic case ID")
    parser.add_argument("--max-seconds", type=int, default=600, help="Hard per-map wall-time limit (POSIX)")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "scripts"))
    from run_skill_usability_tests import validate_output_path

    output = validate_output_path(args.output)
    sources = sorted(p for p in args.corpus.iterdir() if p.suffix.lower() in {".pdf", ".xlsx"} and p.is_file())
    if not sources:
        parser.error("corpus contains no PDF/XLSX working sources")
    if not hasattr(signal, "setitimer") or not 1 <= args.max_seconds <= 900:
        parser.error("diagnostic baseline requires a POSIX timer and a 1..900 second per-map bound")
    if args.case and args.case not in {f"map-{index:03d}" for index in range(1, len(sources) + 1)}:
        parser.error("unknown generic case ID")
    output.mkdir(parents=True, exist_ok=True)
    module_path = ROOT / "scripts/pstack/map_matrix/run_worker.py"
    spec = importlib.util.spec_from_file_location("matrix_worker", module_path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    worker.ARTIFACTS = output / "private-pipeline"
    evals = json.loads((module_path.parent / "evals.json").read_text())
    inventory = []
    results = []
    for index, source in enumerate(sources, 1):
        identifier = f"map-{index:03d}"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        inventory.append({"id": identifier, "source_path": str(source.resolve()), "sha256": digest, "format": source.suffix[1:]})
        if args.case and args.case != identifier:
            continue
        started = time.monotonic()
        def timed_out(signum, frame):
            raise TimeoutError("map wall-time budget exceeded")
        previous_handler = signal.signal(signal.SIGALRM, timed_out)
        signal.setitimer(signal.ITIMER_REAL, args.max_seconds)
        try:
            receipt = worker.run_map({"id": identifier, "filename": source.name, "relative_path": str(source.resolve()), "format": source.suffix[1:], "bytes": source.stat().st_size, "pipeline_class": "pdf" if source.suffix == ".pdf" else "structured", "sha256": digest, "converted_from": None}, evals)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
        steps = {step["step"]: step for step in receipt["steps"]}
        compile_step = steps.get("compile-user-map", {})
        result = {
            "id": identifier, "source_sha256": digest, "format": source.suffix[1:],
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "candidate_rows": steps.get("intake", {}).get("records", 0),
            "user_points": compile_step.get("user_map_points", 0),
            "compile_state": compile_step.get("state"),
            "crashed": receipt.get("crashed", False),
            "full_fidelity": "not-verified",
            "legacy_score_only": receipt["score"],
        }
        results.append(result)
        print(json.dumps({k: v for k, v in result.items() if k != "legacy_score_only"}), flush=True)
        report = {
            "schema_version": "modbus-corpus-baseline/v1",
            "status": "incomplete", "purpose": "diagnostic baseline, not acceptance",
            "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "required_maps": len(sources), "executed_maps": len(results),
            "states": dict(Counter(r["compile_state"] for r in results)), "results": results,
        }
        (output / "baseline.json").write_text(json.dumps(report, indent=2) + "\n")
        (output / "private-source-inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    return 1 if any(r["crashed"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
