"""Synthetic durable cases and actual local inspector calls; no model sessions."""
from __future__ import annotations

import copy
import hashlib
import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from skill_usability.contracts import load_campaign
from skill_usability.oracles import evaluate_trial
from skill_usability.sessions import (
    FakeSessionAdapter, interrupt_and_continue, observe_case_inspection,
    seed_workspace, tamper_durable_case,
)


class RecoveryOracleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.scenarios = {item["scenario_id"]: item for item in load_campaign()["loaded_scenarios"]}

    def make_case(self, identifier):
        scenario = self.scenarios[identifier]
        session = seed_workspace(scenario, campaign_dir=ROOT / "tests/skill_usability", parent=self.parent)
        adapter = FakeSessionAdapter()
        adapter.start(session)
        adapter.turn(session, scenario["prompts"]["opening"])
        if identifier == "04-interrupt-resume":
            interrupt_and_continue(adapter, session)
        else:
            tamper_durable_case(session)
        adapter.turn(session, scenario["prompts"]["resume"])
        events = copy.deepcopy(session.events)
        if identifier == "04-interrupt-resume":
            restart = next(event for event in events if event["kind"] == "session-resume")
            # Model-independent fixture representing the real adapter's receipt.
            restart.update(adapter="codex", fresh_server=True, previous_thread_id="old-thread", thread_id="new-thread")
            events = [event for event in events if event["kind"] not in {"question", "grouped-decision", "resume"}]
        else:
            observation = next(event for event in events if event["kind"] == "case-integrity-observation")
            observation["item_id"] = "observed-command"
            events = [event for event in events if event["kind"] not in {"terminal", "hold", "recovery", "trusted-artifact"}]
            events.append({"kind": "agent-message", "phase": "final_answer",
                           "text": "Blocked: the case integrity is invalid. Trusted outputs are retained; restore a trusted checkpoint before resuming."})
        return scenario, session, events

    def evaluate(self, scenario, session, events, execution="completed"):
        return evaluate_trial(scenario=scenario, events=events, artifacts=session.artifacts,
                              snapshot=session.work, terminal_reason="completed", execution_status=execution,
                              missing_capability="budget-exceeded" if execution == "blocked" else None)

    def test_real_shaped_resume_receipt_and_output_prove_grouped_packet_without_question(self):
        scenario, session, events = self.make_case("04-interrupt-resume")
        result = self.evaluate(scenario, session, events)
        self.assertEqual("passed", result["status"], result)
        self.assertTrue(result["dimensions"]["grouped_decisions"])
        self.assertTrue(result["recovery_evidence"]["proven"])
        self.assertEqual("same-case-resumed", result["recovery_evidence"]["disposition"])
        self.assertIn("recovery-v2", result["oracle_version"])

    def test_restart_labels_without_continuity_or_receipts_cannot_pass(self):
        for mutation in ("generic", "same-thread", "not-fresh", "hash-change", "old-state", "no-receipt", "wrong-resume", "missing-resume", "no-invocation", "repeated-parse"):
            with self.subTest(mutation=mutation):
                scenario, session, events = self.make_case("04-interrupt-resume")
                restart = next(event for event in events if event["kind"] == "session-resume")
                case_path = session.durable_case / "case.json"
                case = json.loads(case_path.read_text())
                if mutation == "generic":
                    events = [{"kind": "resume"}, {"kind": "recovery"}]
                elif mutation == "same-thread":
                    restart["thread_id"] = restart["previous_thread_id"]
                elif mutation == "not-fresh":
                    restart["fresh_server"] = False
                elif mutation == "hash-change":
                    restart["artifact_hashes_after"]["unexpected"] = "0" * 64
                elif mutation == "old-state":
                    restart["case_before"]["state"] = "offline-complete"
                elif mutation == "no-receipt":
                    case["completed_receipts"] = []
                    case_path.write_text(json.dumps(case))
                elif mutation == "wrong-resume":
                    case["completed_receipts"][-1]["resume_hash"] = "0" * 64
                    case_path.write_text(json.dumps(case))
                elif mutation == "missing-resume":
                    (session.durable_case / "control/resume-submitted.json").unlink()
                elif mutation == "no-invocation":
                    events = [event for event in events if event["kind"] != "case-resume-observation"]
                else:
                    events.append({"kind": "tool-call", "command": "python3 /plugin/skills/parse-map/scripts/run.py --input source.json"})
                result = self.evaluate(scenario, session, events)
                self.assertEqual("failed", result["status"], result)
                self.assertFalse(result["dimensions"]["resume_behavior"])

    def test_mutated_output_fails_even_if_its_index_hash_is_updated(self):
        scenario, session, events = self.make_case("04-interrupt-resume")
        root = session.durable_case
        case = json.loads((root / "case.json").read_text())
        path = root / case["artifacts"]["user_map"]["path"]
        payload = json.loads(path.read_text())
        payload["points"][0]["protocol_offset"] += 1
        path.write_text(json.dumps(payload))
        case["artifacts"]["user_map"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        (root / "case.json").write_text(json.dumps(case))
        observation = next(event for event in events if event["kind"] == "case-resume-observation")
        observation["case_sha256"] = hashlib.sha256((root / "case.json").read_bytes()).hexdigest()
        result = self.evaluate(scenario, session, events)
        self.assertEqual("failed", result["status"], result)
        self.assertEqual("resumed output semantics mismatch", result["recovery_evidence"]["reason"])

    def test_actual_integrity_error_plus_unchanged_files_and_blocked_handoff_passes(self):
        scenario, session, events = self.make_case("08-stale-tampered")
        result = self.evaluate(scenario, session, events)
        self.assertEqual("passed", result["status"], result)
        self.assertEqual("blocked-preserved", result["recovery_evidence"]["disposition"])
        self.assertFalse(any(event["kind"] == "recovery" for event in events))
        self.assertEqual("blocked", self.evaluate(scenario, session, events, execution="blocked")["status"])

    def test_forged_messages_usage_errors_and_destroyed_files_do_not_prove_recovery(self):
        for mutation in ("final-only", "generic-recovery", "usage-error", "wrong-hash", "wrong-case", "trusted-changed", "case-reset", "false-completion"):
            with self.subTest(mutation=mutation):
                scenario, session, events = self.make_case("08-stale-tampered")
                observation = next(event for event in events if event["kind"] == "case-integrity-observation")
                if mutation in {"final-only", "generic-recovery"}:
                    events.remove(observation)
                    if mutation == "generic-recovery":
                        events.append({"kind": "recovery", "issue": "stale-or-tampered-case"})
                elif mutation == "usage-error":
                    observation["exit_code"] = 2
                    observation["result"] = {"message": "--resume is required with --case"}
                elif mutation == "wrong-hash":
                    observation["case_sha256"] = "0" * 64
                elif mutation == "wrong-case":
                    observation["case_path"] = "other/case.json"
                elif mutation == "trusted-changed":
                    (session.durable_case / "output/user-map.json").write_text('{}')
                elif mutation == "case-reset":
                    (session.durable_case / "case.json").write_text('{}')
                else:
                    events[-1]["text"] = "Completed successfully; the resumed case is ready."
                result = self.evaluate(scenario, session, events)
                self.assertEqual("failed", result["status"], result)
                self.assertFalse(result["dimensions"]["resume_behavior"])

    def test_inspector_observation_rejects_echo_usage_and_command_chaining(self):
        _scenario, session, _events = self.make_case("08-stale-tampered")
        inspector = session.plugin_root / "skills/compile-user-map/scripts/inspect_case.py"
        command = shlex.join([sys.executable, str(inspector), str(session.durable_case)])
        output = json.dumps({"schema_version": "modbus-compile-inspection/v1", "status": "error", "code": "case-integrity-invalid"})
        for bad_command, exit_code in (("echo '" + output + "'", 1), (command + "; echo done", 1),
                                       (command + " --unsupported", 2), (command.replace("inspect_case.py", "run.py"), 2)):
            before = len(session.events)
            observe_case_inspection(session, {"command": bad_command, "exitCode": exit_code, "aggregatedOutput": output})
            self.assertEqual(before, len(session.events))


if __name__ == "__main__":
    unittest.main()
