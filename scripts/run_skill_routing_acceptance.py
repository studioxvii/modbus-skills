#!/usr/bin/env python3
"""Actual-session help routing matrix; never claims implicit host activation."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from run_skill_usability_tests import run_campaign, validate_output_path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/skill_usability"


def routing_inputs():
    declaration = json.loads((SOURCE / "routing-cases.json").read_text())
    campaign = json.loads((SOURCE / "campaign.json").read_text())
    template = json.loads((SOURCE / "scenarios/01-novice-routing.json").read_text())
    scenarios = []
    for case in declaration["cases"]:
        scenario = copy.deepcopy(template)
        scenario.update(scenario_id=case["id"], version=declaration["version"],
                        goal=case["prompt"], permitted_facts={}, prompts={"opening": case["prompt"]})
        scenario["attention_budget"]["max_final_words"] = 200 if case.get("refusal") else 120
        profile = scenario["oracle_profile"]
        profile["id"] = "read-only-routing-v1"
        profile["handoff_policy"] = "recommendation-only"
        profile["expected_recommended_skill"] = case["expected_skill"]
        profile["completion_conditions"] = (["recommended_skill_present", "recommended_skill_matches"]
            if case["expected_skill"] else ["no-recommendation"])
        if case.get("refusal"):
            profile["completion_conditions"].append("expected-refusal")
            profile["acceptable_holds"] = ["unsafe-request-refused"]
        if case.get("required_text"):
            profile["required_handoff_text"] = case["required_text"]
        if case["category"] == "negative":
            profile["required_handoff_text"] = r"(?:\bno\b|\bnone\b|\bnot\b|outside|unrelated|doesn't|does not)"
            profile["forbidden_handoff_skills"] = sorted({row["expected_skill"] for row in declaration["cases"] if row["expected_skill"]})
        scenarios.append(scenario)
    campaign.update(campaign_kind="extended", campaign_id="all-skill-routing-v1",
                    version="1", scenarios=[item["scenario_id"] for item in scenarios])
    return declaration, campaign, scenarios


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--case", action="append")
    parser.add_argument("--plugin-source", type=Path)
    args = parser.parse_args()
    output = validate_output_path(Path(args.output))
    declaration, campaign, scenarios = routing_inputs()
    config = output / "inputs"
    (config / "scenarios").mkdir(parents=True)
    for item in scenarios:
        (config / "scenarios" / f"{item['scenario_id']}.json").write_text(json.dumps(item, indent=2) + "\n")
    (config / "campaign.json").write_text(json.dumps(campaign, indent=2) + "\n")
    (config / "declaration.json").write_text(json.dumps(declaration, indent=2) + "\n")
    report = run_campaign(mode="real-model", output=output / "results", campaign_path=config / "campaign.json",
                          scenario_ids=args.case, repetitions_override=args.repetitions, model=args.model,
                          plugin_source=args.plugin_source)
    print(json.dumps({"status": report["status"], "declared_cases": len(scenarios),
                      "selected_cases": args.case or "all", "scope": declaration["scope"]}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
