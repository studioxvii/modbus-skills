from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowCatalogTests(unittest.TestCase):
    def test_skill_names_and_user_invocation_policy(self) -> None:
        expected = {
            "analyze-capture", "apply-review", "build-custom-export", "build-modpoll",
            "build-modscan", "build-node-red", "build-tool-pack", "capture-sample",
            "check-byte-order", "check-map", "compare-maps", "extract-pdf-map",
            "compile-user-map", "modbus-help", "normalize-map", "parse-map", "plan-reads",
            "remap-addresses", "review-evidence", "review-map",
        }
        skill_root = ROOT / "plugins" / "modbus-skills" / "skills"
        self.assertEqual({path.name for path in skill_root.iterdir() if path.is_dir()}, expected)
        for skill_id in expected:
            with self.subTest(skill=skill_id):
                metadata = (skill_root / skill_id / "agents" / "openai.yaml").read_text(encoding="utf-8")
                self.assertIn("allow_implicit_invocation: false", metadata)

    def test_router_uses_shared_paths_and_verifies_selected_skill(self) -> None:
        router = (
            ROOT / "plugins" / "modbus-skills" / "skills" / "modbus-help" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("../../references/user-paths.md", router)
        self.assertIn("Read that skill's current `SKILL.md`", router)
        self.assertIn("`compile-user-map`", router)
        self.assertIn("explicitly requested stage", router)

    def test_router_defaults_to_complete_safe_chain_but_keeps_direct_stage_routes(self) -> None:
        router = (
            ROOT / "plugins" / "modbus-skills" / "skills" / "modbus-help" / "SKILL.md"
        ).read_text(encoding="utf-8")
        paths = (
            ROOT / "plugins" / "modbus-skills" / "references" / "user-paths.md"
        ).read_text(encoding="utf-8")
        complete_chain = (
            "normalize-map -> check-map -> plan-reads -> build-node-red -> "
            "capture-sample -> analyze-capture -> check-byte-order"
        )
        self.assertIn(complete_chain, router)
        self.assertIn(complete_chain, paths)
        self.assertIn("Route an explicitly requested stage directly", router)

    def test_shared_completion_contract_recommends_one_actionable_next_step(self) -> None:
        contract = (
            ROOT / "plugins" / "modbus-skills" / "references" / "interaction-contract.md"
        ).read_text(encoding="utf-8")
        self.assertIn("## Completion and next step", contract)
        self.assertIn("choose exactly one `Recommended next:` skill", contract)
        self.assertIn("Show at most two `Other options:`", contract)
        self.assertIn("Reply `proceed` to continue.", contract)
        self.assertIn("read the named sibling skill's current `SKILL.md`", contract)
        self.assertRegex(contract, r"execute it with the exact\s+artifacts named in `Uses:`")
        self.assertIn("Do not rely on host-specific implicit invocation", contract)
        self.assertIn("next_action: none", contract)

        skill_root = ROOT / "plugins" / "modbus-skills" / "skills"
        for skill_dir in skill_root.iterdir():
            if not skill_dir.is_dir():
                continue
            with self.subTest(skill=skill_dir.name):
                skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn("../../references/interaction-contract.md", skill)

    def test_compiler_finishes_with_outcome_aware_guidance(self) -> None:
        skill = (
            ROOT / "plugins" / "modbus-skills" / "skills" / "compile-user-map" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("## Finish", skill)
        self.assertIn("user-map holds and target statuses", skill)
        self.assertIn("never treat `next_action: none` alone as proof of completion", skill)
        self.assertIn("recommend continuing", skill)

    def test_skill_handoffs_reference_existing_skills(self) -> None:
        skill_root = ROOT / "plugins" / "modbus-skills" / "skills"
        skill_ids = {path.name for path in skill_root.iterdir() if path.is_dir()}
        for skill_id in skill_ids - {"modbus-help"}:
            text = (skill_root / skill_id / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=skill_id):
                handoffs = [line for line in text.splitlines() if "suggest" in line]
                for line in handoffs:
                    for target in re.findall(r"`([a-z0-9]+(?:-[a-z0-9]+)*)`", line):
                        self.assertIn(target, skill_ids)

    def test_workflows_reference_existing_skills(self) -> None:
        workflows = json.loads((ROOT / "catalog" / "workflows.json").read_text(encoding="utf-8"))
        skill_root = ROOT / "plugins" / "modbus-skills" / "skills"
        skill_ids = {path.name for path in skill_root.iterdir() if path.is_dir()}
        workflow_ids = {workflow["id"] for workflow in workflows["workflows"]}
        self.assertTrue(workflows["workflows"])
        for workflow in workflows["workflows"]:
            with self.subTest(workflow=workflow["id"]):
                self.assertTrue(workflow["steps"])
                self.assertTrue(workflow["stop_conditions"])
                for step in workflow["steps"]:
                    self.assertIn(step["kind"], {"skill", "workflow", "human-gate", "external-gate"})
                    self.assertTrue(step["inputs"])
                    for input_type in step["inputs"]:
                        self.assertRegex(input_type, r"/v1$")
                    self.assertRegex(step["output"], r"/v1$")
                    if step["kind"] == "skill":
                        self.assertIn(step["skill"], skill_ids)
                    elif step["kind"] == "workflow":
                        self.assertIn(step["workflow"], workflow_ids)
                    else:
                        self.assertTrue(step["instruction"])

    def test_primary_outcome_is_one_skill_step_without_stage_choreography(self) -> None:
        workflows = json.loads((ROOT / "catalog" / "workflows.json").read_text(encoding="utf-8"))
        workflow = next(item for item in workflows["workflows"] if item["id"] == "compile-user-map")
        self.assertEqual(1, len(workflow["steps"]))
        self.assertEqual("compile-user-map", workflow["steps"][0]["skill"])

        skill = (
            ROOT / "plugins" / "modbus-skills" / "skills" / "compile-user-map" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("## Handoff", skill)
        for stage in ("$extract-pdf-map", "$normalize-map", "$review-evidence", "$plan-reads"):
            self.assertNotIn(stage, skill)

        wrapper = (
            ROOT / "plugins" / "modbus-skills" / "skills" / "compile-user-map" / "scripts" / "run.py"
        ).read_text(encoding="utf-8")
        self.assertIn('run_cli("compile-user-map"', wrapper)

    def test_byte_order_workflow_probes_before_final_generation(self) -> None:
        workflows = json.loads((ROOT / "catalog" / "workflows.json").read_text(encoding="utf-8"))
        workflow = next(
            item
            for item in workflows["workflows"]
            if item["id"] == "probe-resolve-finalize-tool-pack"
        )
        steps = [step.get("skill", step["kind"]) for step in workflow["steps"]]
        self.assertEqual(
            steps,
            [
                "plan-reads",
                "build-tool-pack",
                "external-gate",
                "check-byte-order",
                "human-gate",
                "apply-review",
                "plan-reads",
                "build-tool-pack",
            ],
        )
        self.assertEqual(workflow["steps"][1]["output"], "modbus-tool-pack/v1")
        self.assertIn("final-tool-pack-request/v1", workflow["steps"][-1]["inputs"])


if __name__ == "__main__":
    unittest.main()
