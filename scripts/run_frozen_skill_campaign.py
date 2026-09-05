#!/usr/bin/env python3
"""Freeze the entire evaluator and public inputs before a long actual-model run."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINTS = {"representative": "run_skill_usability_tests.py",
               "routing": "run_skill_routing_acceptance.py",
               "specialist": "run_specialist_execution_acceptance.py"}


def files(root):
    candidates = list((root / "scripts").glob("*.py"))
    for name in ("scripts/skill_usability", "plugins/modbus-skills", "tests/skill_usability"):
        candidates.extend((root / name).rglob("*"))
    candidates.extend(root / "catalog" / name for name in ("skills.json", "workflows.json"))
    result = []
    for path in sorted(set(candidates)):
        if "__pycache__" in path.parts or path.suffix == ".pyc": continue
        if path.is_symlink(): raise ValueError("frozen campaign source may not contain symlinks")
        if path.is_file(): result.append(path)
    return result


def hashes(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files(root)}


def freeze(source, destination):
    if destination.exists(): raise ValueError("snapshot destination must not exist")
    before = hashes(source)
    for name in before:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    if hashes(source) != before or hashes(destination) != before:
        raise ValueError("campaign source changed during snapshot")
    return before


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=ENTRYPOINTS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--repetitions", type=int, default=3, choices=range(1, 21))
    parser.add_argument("--case", action="append")
    args = parser.parse_args()
    # Import only path validation before freezing; the worker/evaluator process
    # imports all runtime and oracle code from the separate verified copy.
    from run_skill_usability_tests import validate_output_path
    output = validate_output_path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    snapshot = output / "frozen-source"
    before = freeze(ROOT, snapshot)
    command = [sys.executable, "-B", str(snapshot / "scripts" / ENTRYPOINTS[args.kind]),
               "--output", str(output / "campaign"), "--model", args.model, "--repetitions", str(args.repetitions)]
    if args.kind == "representative": command += ["--mode", "real-model"]
    for case in args.case or []:
        command += ["--scenario" if args.kind == "representative" else "--case", case]
    manifest = {"schema_version": "frozen-skill-campaign/v1", "source_hashes": before,
                "command": command, "python": platform.python_version(), "platform": platform.platform(),
                "kind": args.kind, "model": args.model, "repetitions": args.repetitions,
                "status": "running", "scope": "All evaluator code, plugin, catalog and public scenario inputs frozen; native/runtime dependencies remain external."}
    receipt = output / "snapshot-receipt.json"
    receipt.write_text(json.dumps(manifest, indent=2) + "\n")
    try:
        completed = subprocess.run(command, check=False)
        manifest["exit_code"] = completed.returncode
    finally:
        unchanged = hashes(snapshot) == before
        manifest["snapshot_unchanged"] = unchanged
        manifest["status"] = "finished" if unchanged and "exit_code" in manifest else "inconclusive"
        receipt.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest["exit_code"] if unchanged else 2


if __name__ == "__main__":
    raise SystemExit(main())
