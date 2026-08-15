from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from skill_usability.contracts import (  # noqa: E402
    CAMPAIGN_PATH,
    ContractError,
    load_campaign,
    validate_campaign,
    validate_scenario,
    catalog_skill_ids,
    catalog_workflow_ids,
)


def _campaign() -> dict:
    return json.loads(CAMPAIGN_PATH.read_text(encoding="utf-8"))


class SkillUsabilityContractTests(unittest.TestCase):
    def test_representative_campaign_loads_eight_catalog_backed_scenarios(self) -> None:
        campaign = load_campaign()
        self.assertEqual(8, len(campaign["loaded_scenarios"]))
        skills = catalog_skill_ids()
        workflows = catalog_workflow_ids()
        personas = set(campaign["personas"])
        for scenario in campaign["loaded_scenarios"]:
            self.assertIn(scenario["skill"], skills)
            self.assertIn(scenario["persona"], personas)
            if scenario.get("workflow"):
                self.assertIn(scenario["workflow"], workflows)
            self.assertTrue(scenario["oracle_profile"]["id"])
        self.assertEqual("gpt-5.3-codex", campaign["worker_model"])
        self.assertEqual({"deterministic", "simulated-user"}, set(campaign["evidence_classes"]))

    def test_rejects_implicit_specialist_discovery(self) -> None:
        campaign = load_campaign()
        scenario = copy.deepcopy(campaign["loaded_scenarios"][1])
        scenario["entry_policy"] = {"invocation": "implicit", "skill": "compile-user-map"}
        with self.assertRaises(ContractError):
            validate_scenario(
                scenario,
                skills=catalog_skill_ids(),
                workflows=catalog_workflow_ids(),
                personas=set(campaign["personas"]),
                campaign_dir=CAMPAIGN_PATH.parent,
            )

    def test_accepts_explicit_specialist_and_router_discovery(self) -> None:
        campaign = load_campaign()
        router = campaign["loaded_scenarios"][0]
        specialist = campaign["loaded_scenarios"][1]
        self.assertEqual("router", router["entry_policy"]["invocation"])
        self.assertEqual("modbus-help", router["entry_policy"]["skill"])
        self.assertEqual("explicit", specialist["entry_policy"]["invocation"])
        self.assertEqual("compile-user-map", specialist["entry_policy"]["skill"])

    def test_rejects_fixture_traversal_absolute_paths_credentials_and_write_functions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scenarios").mkdir()
            (root / "fixtures").mkdir()
            (root / "fixtures" / "ok.json").write_text("{}\n", encoding="utf-8")
            for name, relative in (
                ("absolute", "/tmp/secret.csv"),
                ("traversal", "../secret.csv"),
            ):
                with self.subTest(name=name):
                    with self.assertRaises(ContractError):
                        self._write_bad_fixture(root, relative)
            scenario = copy.deepcopy(load_campaign()["loaded_scenarios"][0])
            scenario["prompts"]["opening"] = "password=super-secret"
            with self.assertRaises(ContractError):
                validate_scenario(
                    scenario,
                    skills=catalog_skill_ids(),
                    workflows=catalog_workflow_ids(),
                    personas=set(load_campaign()["personas"]),
                    campaign_dir=CAMPAIGN_PATH.parent,
                )

        loaded = load_campaign()
        scenario = copy.deepcopy(loaded["loaded_scenarios"][0])
        scenario["safety_envelope"]["allowed_function_codes"] = [3, 16]
        with self.assertRaises(ContractError):
            validate_scenario(
                scenario,
                skills=catalog_skill_ids(),
                workflows=catalog_workflow_ids(),
                personas=set(loaded["personas"]),
                campaign_dir=CAMPAIGN_PATH.parent,
            )

    def _write_bad_fixture(self, root: Path, relative: str) -> None:
        scenario = {
            "schema_version": "skill-usability-scenario/v1",
            "scenario_id": "99-bad",
            "version": "1",
            "skill": "modbus-help",
            "workflow": None,
            "persona": "novice",
            "goal": "bad fixture",
            "entry_policy": {"invocation": "router", "skill": "modbus-help"},
            "fixtures": [{"id": "bad", "path": relative}],
            "permitted_facts": {"ok": "yes"},
            "prompts": {"opening": "hello"},
            "transitions": [{"id": "start", "kind": "prompt", "prompt_id": "opening"}],
            "response_rules": [],
            "attention_budget": {"max_questions": 1},
            "safety_envelope": {
                "allowed_function_codes": [1, 2, 3, 4],
                "writes": False,
                "broadcasts": False,
                "discovery_scans": False,
                "unbounded_polling": False,
                "credentials": False,
                "live_endpoints": False,
            },
            "oracle_profile": {
                "id": "bad",
                "expected_terminal": "passed",
                "expected_route": "modbus-help",
                "required_artifacts": [],
                "artifact_schemas": [],
                "acceptable_holds": [],
                "prohibited_operations": ["write"],
                "completion_conditions": ["recommended_skill_present"],
                "dimensions": {
                    "routing": True,
                    "outcome_completion": True,
                    "artifact_usefulness": False,
                    "question_burden": True,
                    "grouped_decisions": False,
                    "correction_handling": False,
                    "resume_behavior": False,
                    "unsafe_refusal": True,
                },
            },
        }
        validate_scenario(
            scenario,
            skills=catalog_skill_ids(),
            workflows=catalog_workflow_ids(),
            personas={"novice"},
            campaign_dir=root,
        )

    def test_rejects_missing_oracle_profile_and_unknown_answers(self) -> None:
        campaign = load_campaign()
        scenario = copy.deepcopy(campaign["loaded_scenarios"][2])
        scenario["oracle_profile"]["completion_conditions"] = []
        scenario["oracle_profile"]["expected_terminal"] = "passed"
        with self.assertRaises(ContractError):
            validate_scenario(
                scenario,
                skills=catalog_skill_ids(),
                workflows=catalog_workflow_ids(),
                personas=set(campaign["personas"]),
                campaign_dir=CAMPAIGN_PATH.parent,
            )
        scenario = copy.deepcopy(campaign["loaded_scenarios"][2])
        scenario["response_rules"][0]["fact"] = "invented-register"
        with self.assertRaises(ContractError):
            validate_scenario(
                scenario,
                skills=catalog_skill_ids(),
                workflows=catalog_workflow_ids(),
                personas=set(campaign["personas"]),
                campaign_dir=CAMPAIGN_PATH.parent,
            )

    def test_requires_pinned_worker_model(self) -> None:
        campaign = _campaign()
        campaign.pop("worker_model")
        with self.assertRaises(ContractError):
            validate_campaign(campaign, campaign_dir=CAMPAIGN_PATH.parent)


if __name__ == "__main__":
    unittest.main()
