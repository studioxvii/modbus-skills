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
            "modbus-help", "normalize-map", "parse-map", "plan-reads",
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
        self.assertIn("Select one next skill", router)

    def test_skill_handoffs_reference_existing_skills(self) -> None:
        skill_root = ROOT / "plugins" / "modbus-skills" / "skills"
        skill_ids = {path.name for path in skill_root.iterdir() if path.is_dir()}
        for skill_id in skill_ids - {"modbus-help"}:
            text = (skill_root / skill_id / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=skill_id):
                self.assertIn("## Handoff", text)
                for target in re.findall(r"\$([a-z0-9]+(?:-[a-z0-9]+)*)", text):
                    self.assertIn(target, skill_ids)

    def test_workflows_reference_existing_skills(self) -> None:
        skills = json.loads((ROOT / "catalog" / "skills.json").read_text(encoding="utf-8"))
        workflows = json.loads((ROOT / "catalog" / "workflows.json").read_text(encoding="utf-8"))
        skill_ids = {skill["id"] for skill in skills["skills"]}
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
