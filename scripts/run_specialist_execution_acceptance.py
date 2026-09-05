#!/usr/bin/env python3
"""Actual-model positive execution for all19 specialist wrappers, not routing only."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import sys
import platform

from run_direct_skill_acceptance import (
    CONTRACTS, SKILLS, command_arguments, fixtures, prepare_plans, tree_hash, write_json,
)
from run_skill_usability_tests import run_campaign, validate_output_path

ROOT = Path(__file__).resolve().parents[1]


def environment_preflight(*, require_pdf):
    try:
        import pdfplumber
        pdf_version = pdfplumber.__version__
    except ImportError:
        pdf_version = None
    return {"schema_version": "specialist-environment/v1", "python_executable": sys.executable,
            "python_version": platform.python_version(), "pdfplumber_version": pdf_version,
            "pdf_required": require_pdf, "status": "unavailable" if require_pdf and pdf_version is None else "ready",
            "scope": "Existing interpreter and dependencies only; no installation or host PATH changes."}


def create_inputs(output):
    source = ROOT / "tests/skill_usability"
    config = output / "inputs"
    config.mkdir(parents=True)
    all_inputs = config / "fixtures"
    fixtures(all_inputs)
    with tempfile.TemporaryDirectory(prefix="specialist-prepare-") as temporary:
        private = Path(temporary)
        plugin = private / "plugin"
        shutil.copytree(ROOT / "plugins/modbus-skills", plugin, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        prepare_plans(all_inputs, output / "preparation", plugin, private, tree_hash(plugin))
    # Make request references portable into each worker's read-only fixture folder.
    def portable(value):
        if isinstance(value, dict): return {key: portable(item) for key, item in value.items()}
        if isinstance(value, list): return [portable(item) for item in value]
        if isinstance(value, str) and value.startswith(str(all_inputs) + "/"):
            return Path(value).name
        return value
    for path in all_inputs.glob("*.json"):
        try: payload = json.loads(path.read_text())
        except ValueError: continue
        write_json(path, portable(payload))
    template = json.loads((source / "scenarios/07-revision-compare.json").read_text())
    cases = []
    for skill in SKILLS:
        if skill == "modbus-help": continue
        arguments = command_arguments(skill, "positive", Path("../fixtures"), Path("."))
        names = {Path(value).name for value in arguments if value.startswith("../fixtures/")}
        if skill == "compile-user-map": names.add("good-raw.json")
        if skill == "build-tool-pack": names.update({"good.json", "good-plan.json"})
        hashes = {name: hashlib.sha256((all_inputs / name).read_bytes()).hexdigest() for name in sorted(names)}
        prompt = (f"Use ${skill} to complete this offline task: {CONTRACTS[skill][0]}. "
                  "Use only the supplied synthetic fixtures. Produce the actual skill output, not just a recommendation. "
                  "The following input/options specify the requested scope and output locations (relative to the work folder): "
                  + " ".join(arguments) + ". "
                  "Read the skill instructions and use its wrapper. Do not execute generated clients, start a native application, "
                  "connect to a device or network, or treat a synthetic review role as human approval. "
                  "Stop after offline output generation. Give a concise result with the primary useful deliverable.")
        scenario = copy.deepcopy(template)
        scenario.update(scenario_id=f"execute-{skill}-positive", version="1", skill=skill,
                        goal=prompt, entry_policy={"invocation": "explicit", "skill": skill},
                        fixtures=[{"id": name.replace(".", "-"), "path": "fixtures/" + name} for name in sorted(names)],
                        permitted_facts={}, prompts={"opening": prompt})
        scenario["attention_budget"]["max_questions"] = 1 if skill == "capture-sample" else 0
        profile = scenario["oracle_profile"]
        profile.update(id="specialist-execution-v1", expected_route=skill,
                       artifact_schemas=[], expected_moves=[], completion_conditions=["specialist-execution"],
                       direct_skill_case=f"{skill}--positive", fixture_hashes=hashes)
        # Actual worker permissions and observed wrapper/output evidence are
        # assessed separately; absent invented unsafe events cannot prove safety.
        profile["dimensions"]["unsafe_refusal"] = False
        write_json(config / "scenarios" / f"{scenario['scenario_id']}.json", scenario)
        cases.append(scenario["scenario_id"])
    campaign = json.loads((source / "campaign.json").read_text())
    campaign.update(campaign_kind="extended", campaign_id="specialist-execution-v1", version="1", scenarios=cases)
    write_json(config / "campaign.json", campaign)
    return config / "campaign.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--case", action="append")
    args = parser.parse_args()
    output = validate_output_path(args.output)
    preflight = environment_preflight(require_pdf=not args.case or "execute-extract-pdf-map-positive" in args.case)
    write_json(output / "environment-preflight.json", preflight)
    if preflight["status"] != "ready":
        print(json.dumps({"status": "unavailable", "reason": "selected-interpreter-missing-pdfplumber"}))
        return 2
    campaign = create_inputs(output)
    report = run_campaign(mode="real-model", output=output / "results", campaign_path=campaign,
                          scenario_ids=args.case, repetitions_override=args.repetitions, model=args.model)
    print(json.dumps({"status": report["status"], "scope": "actual explicit specialist execution, synthetic inputs only"}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
