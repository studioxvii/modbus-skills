from __future__ import annotations

import json
from unittest import mock

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from skill_usability.contracts import load_campaign  # noqa: E402
from skill_usability.oracles import evaluate_trial  # noqa: E402
from skill_usability.scenarios import BoundedUserActor, run_trial  # noqa: E402
from skill_usability.sessions import FakeSessionAdapter, ScriptedWorker, SessionError, TrialSession  # noqa: E402


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
    def test_terminal_turn_does_not_skip_interrupt_or_reply_before_restart(self):
        class Probe(FakeSessionAdapter):
            def __init__(self):
                self.actions = []

            def turn(self, session, text):
                self.actions.append("turn")
                session.turn_count += 1
                session.terminal = True
                session.awaiting_user = True
                session.events.append({"kind": "question", "prompt": "Selection?"})
                return session.events[-1:]

            def continue_session(self, session, text):
                self.actions.append("restart")
                self.asserted_deadline = session.state["deadline"]
                return super().continue_session(session, text)

        adapter = Probe()
        with mock.patch.object(BoundedUserActor, "reply", return_value=None) as reply:
            self._run("04-interrupt-resume", adapter)
        self.assertEqual(["turn", "restart", "turn"], adapter.actions)
        # Only the last turn may solicit a further permitted actor reply.
        self.assertEqual(1, reply.call_count)

    def test_terminal_turn_does_not_skip_real_harness_tamper(self):
        class Probe(FakeSessionAdapter):
            def __init__(self):
                self.observed_states = []

            def turn(self, session, text):
                self.session = session
                session.turn_count += 1
                case = session.work / "case.json"
                trusted = session.work / "trusted.json"
                if session.turn_count == 1:
                    case.write_text('{"state":"complete"}')
                    trusted.write_text('{"point":123}')
                else:
                    self.observed_states.append(json.loads(case.read_text())["state"])
                    self.asserted_trusted = trusted.read_text()
                session.terminal = True
                return []

        adapter = Probe()
        result = self._run("08-stale-tampered", adapter)
        self.assertEqual(["tampered"], adapter.observed_states)
        self.assertEqual('{"point":123}', adapter.asserted_trusted)
        tamper = next(event for event in adapter.session.events if event["kind"] == "tamper")
        self.assertNotEqual(tamper["before_sha256"], tamper["after_sha256"])
        self.assertFalse(any(event["kind"] == "recovery" for event in adapter.session.events))
        self.assertNotEqual("passed", result["status"])
        self.assertFalse(adapter.session.workspace.exists())

    def test_missing_tamper_case_stops_before_resume_and_still_cleans(self):
        adapter = FakeSessionAdapter()

        def turn(session, _text):
            session.turn_count += 1
            session.terminal = True
            return []

        with mock.patch.object(adapter, "turn", side_effect=turn) as turns, mock.patch.object(
            adapter, "cleanup", wraps=adapter.cleanup
        ) as cleanup:
            result = self._run("08-stale-tampered", adapter)
        self.assertEqual(1, turns.call_count)
        self.assertNotEqual("passed", result["status"])
        self.assertEqual("durable-case-missing", cleanup.call_args.args[0].terminal_reason)
        self.assertFalse(cleanup.call_args.args[0].workspace.exists())

    def test_known_selection_reply_precedes_tampering_when_initial_request_is_ambiguous(self):
        class Probe(FakeSessionAdapter):
            def __init__(self):
                self.prompts = []
                self.resumed_state = None

            def turn(self, session, text):
                self.session = session
                self.prompts.append(text)
                session.turn_count += 1
                session.terminal = True
                case = session.work / "case.json"
                if session.turn_count == 1:
                    session.awaiting_user = True
                    session.events.append({"kind": "question", "prompt": "Which measurements should I include?"})
                elif session.turn_count == 2:
                    session.awaiting_user = False
                    case.write_text('{"state":"complete"}')
                else:
                    self.resumed_state = json.loads(case.read_text())["state"]
                return session.events[-1:]

        adapter = Probe()
        result = self._run("08-stale-tampered", adapter)
        self.assertEqual(3, len(adapter.prompts))
        self.assertEqual("Include only Temperature from the supplied map.", adapter.prompts[1])
        self.assertEqual("tampered", adapter.resumed_state)
        kinds = [event["kind"] for event in adapter.session.events]
        self.assertLess(kinds.index("actor-response"), kinds.index("tamper"))
        self.assertEqual(1, kinds.count("actor-response"))
        # Supplying a legitimate test fact cannot replace tamper detection proof.
        self.assertNotEqual("passed", result["status"])
        self.assertFalse(adapter.session.workspace.exists())

    def test_start_restart_and_evaluation_failures_always_cleanup(self):
        for failure in ("start", "continue_session", "evaluation"):
            with self.subTest(failure=failure):
                adapter = FakeSessionAdapter()
                target = (mock.patch("skill_usability.scenarios.evaluate_trial", side_effect=RuntimeError("unsupported observation"))
                          if failure == "evaluation" else mock.patch.object(adapter, failure, side_effect=SessionError("failure")))
                with target, mock.patch.object(adapter, "cleanup", wraps=adapter.cleanup) as cleanup:
                    result = self._run("04-interrupt-resume", adapter)
                self.assertNotEqual("passed", result["status"])
                cleanup.assert_called_once()
                self.assertFalse(cleanup.call_args.args[0].workspace.exists())

    def test_declared_actual_final_word_gate_retains_words_and_evidence(self):
        class OutputProbe(FakeSessionAdapter):
            name = "codex"

            def turn(self, session, text):
                session.turn_count += 1
                session.terminal = True
                session.state["final_text"] = "one two three four"
                session.state["transcript"] = [{"text": session.state["final_text"]}]
                session.events.append({"kind": "recommendation", "recommended_skill": "compile-user-map"})
                return session.events

        campaign = load_campaign()
        for limit in (None, 3, 4):
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as temporary:
                scenario = json.loads(json.dumps(campaign["loaded_scenarios"][0]))
                scenario["attention_budget"].pop("max_final_words", None)
                if limit is not None:
                    scenario["attention_budget"]["max_final_words"] = limit
                result = run_trial(scenario, adapter=OutputProbe(), parent=Path(temporary), budget=campaign["budget"],
                                   evidence_root=Path(temporary) / "evidence")
                self.assertEqual(4, result["final_words"])
                transcript = next((Path(temporary) / "evidence").rglob("transcript.json"))
                self.assertEqual("one two three four", json.loads(transcript.read_text())[0]["text"])
                if limit == 3:
                    self.assertEqual("failed", result["status"])
                    self.assertIn("final-output-budget-exceeded", result["issue_codes"])
                else:
                    self.assertNotIn("final-output-budget-exceeded", result["issue_codes"])

    def test_actor_never_repeats_a_correction_or_invents_unknown_facts(self):
        scenario = load_campaign()["loaded_scenarios"][2]
        actor = BoundedUserActor(scenario)
        questions = [{"kind": "question", "prompt": "What convention?"}]
        self.assertEqual(scenario["prompts"]["correction"], actor.reply(questions))
        self.assertIsNone(actor.reply(questions))
        self.assertIsNone(BoundedUserActor(load_campaign()["loaded_scenarios"][5]).reply(questions))

    def test_semantic_artifact_accepts_user_chosen_name_but_rejects_wrong_move(self):
        scenario = load_campaign()["loaded_scenarios"][6]
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary)
            payload = {"schema_version": "modbus-map-diff/v1", "moved": [{
                "logical_point_id": "comparison_point", "before_identity": {"protocol_offset": 5},
                "after_identity": {"protocol_offset": 6}}]}
            artifact = snapshot / "my-comparison.json"
            events = [{"kind": "skill-selected", "skill": "compare-maps"}, {"kind": "comparison", "moved": True}]
            def evaluate():
                artifact.write_text(json.dumps(payload))
                return evaluate_trial(scenario=scenario, events=events, artifacts=[], snapshot=snapshot,
                                      terminal_reason="completed", execution_status="completed")
            self.assertEqual("passed", evaluate()["status"])
            payload["moved"][0]["after_identity"]["protocol_offset"] = 7
            self.assertIn("comparison-fidelity-mismatch", evaluate()["issue_codes"])
            payload.clear()
            self.assertIn("artifact-schema-missing", evaluate()["issue_codes"])

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

    def test_snapshot_failure_still_cleans_trial_workspace(self):
        adapter = FakeSessionAdapter()
        with mock.patch.object(adapter, "snapshot", side_effect=OSError("snapshot unavailable")), mock.patch.object(adapter, "cleanup", wraps=adapter.cleanup) as cleanup:
            result = self._run("01-novice-routing", adapter)
            self.assertEqual("blocked", result["status"])
            cleanup.assert_called_once()
            self.assertFalse(cleanup.call_args.args[0].workspace.exists())

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

    def test_claimed_files_wrong_values_and_false_completion_fail(self) -> None:
        campaign = load_campaign()
        scenario = next(item for item in campaign["loaded_scenarios"] if item["scenario_id"] == "02-clean-compile")
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary)
            for name in scenario["oracle_profile"]["required_artifacts"]:
                (snapshot / name).write_text("{}" if name.endswith(".json") else "placeholder")
            (snapshot / "user-map.json").write_text(json.dumps({
                "schema_version": "modbus-user-map/v1",
                "points": [{"name": "Temperature", "protocol_offset": 40010}],
            }))
            result = evaluate_trial(
                scenario=scenario, events=[{"kind": "skill-selected", "skill": "compile-user-map"}],
                artifacts=[{"name": name} for name in scenario["oracle_profile"]["required_artifacts"]],
                snapshot=snapshot, terminal_reason="done", execution_status="completed",
            )
            self.assertEqual("failed", result["status"])
            self.assertIn("point-fidelity-mismatch", result["issue_codes"])
            self.assertIn("offline-completion-unproven", result["issue_codes"])
            (snapshot / "user-map.csv").unlink()
            result = evaluate_trial(
                scenario=scenario, events=[], artifacts=[{"name": "user-map.csv"}],
                snapshot=snapshot, terminal_reason="done", execution_status="completed",
            )
            self.assertIn("missing-artifact", result["issue_codes"])

    def test_tampered_case_emits_recovery_and_preserves_trusted_files(self) -> None:
        result = self._run("08-stale-tampered")
        self.assertEqual("passed", result["status"], result)
        self.assertTrue(result["dimensions"]["resume_behavior"])


if __name__ == "__main__":
    unittest.main()
