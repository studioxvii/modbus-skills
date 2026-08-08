from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowCatalogTests(unittest.TestCase):
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
                "compile-modbus-read-plan",
                "build-modbus-tool-pack",
                "external-gate",
                "evaluate-modbus-byte-order",
                "human-gate",
                "apply-modbus-review-decisions",
                "compile-modbus-read-plan",
                "build-modbus-tool-pack",
            ],
        )
        self.assertEqual(workflow["steps"][1]["output"], "modbus-tool-pack/v1")
        self.assertIn("final-tool-pack-request/v1", workflow["steps"][-1]["inputs"])


if __name__ == "__main__":
    unittest.main()
