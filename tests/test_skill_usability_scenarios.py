from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from skill_usability.contracts import load_campaign  # noqa: E402
from skill_usability.oracles import evaluate_trial  # noqa: E402
from skill_usability.scenarios import run_trial  # noqa: E402
from skill_usability.sessions import FakeSessionAdapter, ScriptedWorker, TrialSession  # noqa: E402


class UnsafeWorker(ScriptedWorker):
    def play_06_unsafe_pressure(self, session: TrialSession, user_text: str) -> None:
        self._select(session, "capture-sample")
        session.events.append({"kind": "write", "function": 16})
        self._finish(session, "wrote")


class RowLoopWorker(ScriptedWorker):
    def play_03_grouped_ambiguity(self, session: TrialSession, user_text: str) -> None:
        self._select(session, "normalize-map")
        for index in range(4):
            session.events.append({"kind": "question", "scope": "row", "prompt": f"row {index}"})
        self._finish(session, "row-loop")


class WrongRouteWorker(ScriptedWorker):
    def play_01_novice_routing(self, session: TrialSession, user_text: str) -> None:
        self._select(session, "modbus-help")
        self._event(
            session,
            "recommendation",
            recommended_skill="normalize-map",
            safe_path=["normalize-map", "check-map", "plan-reads", "build-node-red"],
        )
        self._finish(session, "recommended-skill")


class SkillUsabilityScenarioTests(unittest.TestCase):
    def _run(self, scenario_id: str, adapter: FakeSessionAdapter | None = None) -> dict:
        campaign = load_campaign()
        scenario = next(item for item in campaign["loaded_scenarios"] if item["scenario_id"] == scenario_id)
        with tempfile.TemporaryDirectory() as temporary:
            return run_trial(
                scenario,
                adapter=adapter or FakeSessionAdapter(),
                campaign_dir=ROOT / "tests" / "skill_usability",
                parent=Path(temporary),
                budget=campaign["budget"],
            )

    def test_novice_routing_recommends_compile_user_map(self) -> None:
        result = self._run("01-novice-routing")
        self.assertEqual("passed", result["status"], result)
        self.assertTrue(result["dimensions"]["routing"])

    def test_novice_routing_rejects_the_specialist_chain(self) -> None:
        result = self._run("01-novice-routing", FakeSessionAdapter(WrongRouteWorker()))
        self.assertEqual("failed", result["status"])
        self.assertIn("recommendation-mismatch", result["issue_codes"])

    def test_clean_compile_completes_offline_without_approval(self) -> None:
        result = self._run("02-clean-compile")
        self.assertEqual("passed", result["status"], result)
        self.assertTrue(result["dimensions"]["routing"])
        self.assertTrue(result["dimensions"]["question_burden"])

    def test_grouped_ambiguity_asks_once_and_applies_scoped_correction(self) -> None:
        result = self._run("03-grouped-ambiguity")
        self.assertEqual("passed", result["status"], result)
        self.assertTrue(result["dimensions"]["grouped_decisions"])
        self.assertTrue(result["dimensions"]["correction_handling"])

    def test_interrupt_resume_does_not_repeat_finished_work(self) -> None:
        result = self._run("04-interrupt-resume")
        self.assertEqual("passed", result["status"], result)
        self.assertTrue(result["dimensions"]["resume_behavior"])

    def test_byte_order_hold_enumerates_candidates_without_a_winner(self) -> None:
        result = self._run("05-byte-order-hold")
        self.assertEqual("passed", result["status"], result)
        self.assertNotIn("winner-selected", result["issue_codes"])

    def test_unsafe_pressure_refuses_without_prohibited_actions(self) -> None:
        result = self._run("06-unsafe-pressure")
        self.assertEqual("passed", result["status"], result)
        self.assertTrue(result["dimensions"]["unsafe_refusal"])

    def test_obfuscated_write_attempt_hard_fails(self) -> None:
        result = self._run("06-unsafe-pressure", FakeSessionAdapter(UnsafeWorker()))
        self.assertEqual("failed", result["status"])
        self.assertIn("prohibited-action", result["issue_codes"])

    def test_row_level_questions_fail_grouped_decision_cases(self) -> None:
        result = self._run("03-grouped-ambiguity", FakeSessionAdapter(RowLoopWorker()))
        self.assertEqual("failed", result["status"])
        self.assertIn("row-level-loop", result["issue_codes"])

    def test_persuasive_prose_cannot_override_missing_artifacts(self) -> None:
        campaign = load_campaign()
        scenario = next(item for item in campaign["loaded_scenarios"] if item["scenario_id"] == "02-clean-compile")
        result = evaluate_trial(
            scenario=scenario,
            events=[
                {"kind": "skill-selected", "skill": "compile-user-map"},
                {"kind": "terminal", "reason": "looks-done"},
            ],
            artifacts=[],
            snapshot=None,
            terminal_reason="looks-done",
            execution_status="completed",
        )
        self.assertEqual("inconclusive", result["status"])
        self.assertIn("oracle-evidence-missing", result["issue_codes"])

    def test_revision_compare_reports_moved_point(self) -> None:
        result = self._run("07-revision-compare")
        self.assertEqual("passed", result["status"], result)
        self.assertEqual("not-applicable", result["dimensions"]["resume_behavior"])

    def test_tampered_case_emits_recovery_and_preserves_trusted_files(self) -> None:
        result = self._run("08-stale-tampered")
        self.assertEqual("passed", result["status"], result)
        self.assertTrue(result["dimensions"]["resume_behavior"])


if __name__ == "__main__":
    unittest.main()
