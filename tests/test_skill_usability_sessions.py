from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from skill_usability.contracts import load_campaign  # noqa: E402
from skill_usability.sessions import (  # noqa: E402
    CodexSessionAdapter,
    FakeSessionAdapter,
    PreflightUnavailable,
    ScriptedWorker,
    SessionError,
    hash_tree,
    interrupt_and_continue,
    seed_workspace,
    stripped_worker_env,
    tamper_durable_case,
    work_file_hashes,
    worker_python_reads,
)


class SkillUsabilitySessionTests(unittest.TestCase):
    def test_virtual_environment_access_is_read_only_and_not_parent_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            venv = root / "venv"
            venv.mkdir()
            (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
            library = venv / "lib/site-packages"
            with mock.patch("skill_usability.sessions.sys.prefix", str(venv)), mock.patch(
                "skill_usability.sessions.sys.base_prefix", "/usr"
            ), mock.patch("skill_usability.sessions.sys.executable", str(venv / "bin/python")), mock.patch(
                "skill_usability.sessions.sysconfig.get_path", return_value=str(library)
            ):
                permissions = worker_python_reads()
                self.assertEqual({str(venv / "bin/python"), str(venv / "pyvenv.cfg"), str(library)}, set(permissions))
                self.assertEqual({"read"}, set(permissions.values()))
                self.assertNotIn(str(root), permissions)
                with mock.patch("skill_usability.sessions.sysconfig.get_path", return_value=str(root)):
                    with self.assertRaisesRegex(PreflightUnavailable, "outside-virtual"):
                        worker_python_reads()

    def test_real_adapter_restart_closes_rpc_and_starts_fresh_thread_with_remaining_budgets(self):
        scenario = load_campaign()["loaded_scenarios"][3]
        budget = {"max_seconds": 120, "max_turns": 8, "max_tool_calls": 20, "max_output_bytes": 1000}
        with tempfile.TemporaryDirectory() as temporary:
            session = seed_workspace(scenario, campaign_dir=ROOT / "tests/skill_usability", parent=Path(temporary))
            (session.work / "case.json").write_text('{"state":"complete"}')
            (session.work / "trusted.json").write_text('{"trusted":true}')
            before = work_file_hashes(session)
            first = mock.Mock(bytes_read=125, pending=[], reader=None)
            second = mock.Mock(bytes_read=75, pending=[], reader=None)
            first.call.side_effect = [{}, {"thread": {"id": "thread-one"}, "model": "test-model"}]
            second.call.side_effect = [{}, {"thread": {"id": "thread-two"}, "model": "test-model"}]
            created = []

            def create(*_args, **kwargs):
                if created:
                    first.close.assert_called_once()
                created.append(kwargs["max_bytes"])
                return first if len(created) == 1 else second

            adapter = CodexSessionAdapter(budget=budget)
            with mock.patch("skill_usability.sessions.shutil.which", return_value="/usr/bin/codex"), mock.patch(
                "skill_usability.codex_rpc.CodexRpc", side_effect=create
            ), mock.patch("skill_usability.sessions.time.monotonic", return_value=10):
                adapter.start(session)
                deadline = session.state["deadline"]
                transcript = session.state["transcript"]
                transcript.append({"durable": "prior turn"})
                session.turn_count = 2
                session.tool_calls = 7
                previous = session.session_id
                session.terminal = True
                # Real workers never set the scripted interrupted flag.
                self.assertFalse(session.interrupted)
                interrupt_and_continue(adapter, session)
                self.assertEqual([1000, 875], created)
                self.assertEqual(deadline, session.state["deadline"])
                self.assertEqual(2, session.turn_count)
                self.assertEqual(7, session.tool_calls)
                self.assertIs(transcript, session.state["transcript"])
                self.assertEqual({"durable": "prior turn"}, transcript[0])
                self.assertEqual(before, work_file_hashes(session))
                self.assertNotEqual(previous, session.session_id)
                self.assertEqual("thread-two", session.state["thread_id"])
                self.assertTrue(session.state["thread_first_turn"])
                self.assertFalse(session.terminal)
                for rpc in (first, second):
                    self.assertEqual(["initialize", "thread/start"], [call.args[0] for call in rpc.call.call_args_list])
                    self.assertTrue(all(call.kwargs["deadline"] == deadline for call in rpc.call.call_args_list))
                first_context = first.call.call_args_list[1].args[1]["developerInstructions"]
                resumed_context = second.call.call_args_list[1].args[1]["developerInstructions"]
                self.assertNotIn("Saved case directory:", first_context)
                self.assertIn('Saved case directory: ".".', resumed_context)
                self.assertNotIn('"state":"complete"', resumed_context)
                self.assertTrue(adapter.cleanup(session)["cleaned"])
            second.close.assert_called_once()
            self.assertEqual(200, session.state["output_bytes_used"])
            self.assertFalse(session.workspace.exists())

    def test_saved_case_reference_is_quoted_path_not_state_or_expected_answers(self):
        scenario = load_campaign()["loaded_scenarios"][3]
        with tempfile.TemporaryDirectory() as temporary:
            session = seed_workspace(scenario, campaign_dir=ROOT / "tests/skill_usability", parent=Path(temporary))
            relative = 'saved "case"\nreference'
            session.durable_case = session.work / relative
            session.durable_case.mkdir()
            (session.durable_case / "case.json").write_text('{"hidden-state-sentinel":true}')
            rpc = mock.Mock(bytes_read=0, pending=[], reader=None)
            rpc.call.side_effect = [{}, {"thread": {"id": "fresh"}, "model": "test-model"}]
            adapter = CodexSessionAdapter()
            with mock.patch("skill_usability.sessions.shutil.which", return_value="/usr/bin/codex"), mock.patch(
                "skill_usability.codex_rpc.CodexRpc", return_value=rpc
            ):
                adapter.start(session)
                context = rpc.call.call_args_list[1].args[1]["developerInstructions"]
                self.assertIn(f"Saved case directory: {json.dumps(relative)}.", context)
                self.assertNotIn(relative, context)
                self.assertNotIn("hidden-state-sentinel", context)
                self.assertNotIn("oracle_profile", context)
                self.assertNotIn("selected_subject_ids", context)
                self.assertTrue(adapter.cleanup(session)["cleaned"])

    def test_restart_cannot_reset_exhausted_deadline_or_output_budget(self):
        scenario = load_campaign()["loaded_scenarios"][3]
        for deadline, used, expected in ((9, 10, "time-budget"), (20, 100, "output-budget")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                session = seed_workspace(scenario, campaign_dir=ROOT / "tests/skill_usability", parent=Path(temporary))
                (session.work / "case.json").write_text('{}')
                old = mock.Mock(bytes_read=used, reader=None)
                session.state.update({"rpc": old, "deadline": deadline, "thread_id": "old", "transcript": [{"old": True}]})
                adapter = CodexSessionAdapter(budget={"max_seconds": 120, "max_output_bytes": 100})
                with mock.patch("skill_usability.sessions.shutil.which", return_value="/usr/bin/codex"), mock.patch(
                    "skill_usability.codex_rpc.CodexRpc"
                ) as factory, mock.patch("skill_usability.sessions.time.monotonic", return_value=10):
                    with self.assertRaisesRegex(SessionError, expected):
                        adapter.continue_session(session, None)
                    factory.assert_not_called()
                old.close.assert_called_once()
                self.assertEqual(deadline, session.state["deadline"])
                self.assertEqual([{"old": True}], session.state["transcript"])
                adapter.cleanup(session)

    def test_restart_failure_releases_new_rpc_without_claiming_resume(self):
        scenario = load_campaign()["loaded_scenarios"][3]
        with tempfile.TemporaryDirectory() as temporary:
            session = seed_workspace(scenario, campaign_dir=ROOT / "tests/skill_usability", parent=Path(temporary))
            (session.work / "case.json").write_text('{}')
            old = mock.Mock(bytes_read=1, reader=None)
            new = mock.Mock(bytes_read=2, reader=None)
            new.call.side_effect = [{}, {"thread": {"id": "same"}}]
            session.state.update({"rpc": old, "thread_id": "same"})
            adapter = CodexSessionAdapter()
            with mock.patch("skill_usability.sessions.shutil.which", return_value="/usr/bin/codex"), mock.patch(
                "skill_usability.codex_rpc.CodexRpc", return_value=new
            ):
                with self.assertRaisesRegex(SessionError, "reused-thread"):
                    adapter.continue_session(session, None)
            old.close.assert_called_once()
            new.close.assert_called_once()
            self.assertNotIn("rpc", session.state)
            self.assertFalse(any(event["kind"] == "session-resume" for event in session.events))
            adapter.cleanup(session)

    def test_tamper_is_unique_scoped_and_preserves_all_other_hashes(self):
        scenario = load_campaign()["loaded_scenarios"][7]
        with tempfile.TemporaryDirectory() as temporary:
            session = seed_workspace(scenario, campaign_dir=ROOT / "tests/skill_usability", parent=Path(temporary))
            case = session.work / "saved" / "case.json"
            case.parent.mkdir()
            case.write_text('{"state":"complete","case_id":"synthetic"}')
            trusted = case.parent / "output.json"
            trusted.write_text('{"trusted":true}')
            before = work_file_hashes(session)
            session.state["transcript"] = []
            tamper_durable_case(session)
            after = work_file_hashes(session)
            self.assertNotEqual(before["saved/case.json"], after["saved/case.json"])
            self.assertEqual(before["saved/output.json"], after["saved/output.json"])
            event = session.events[-1]
            self.assertEqual(before["saved/case.json"], event["before_sha256"])
            self.assertEqual(after["saved/case.json"], event["after_sha256"])
            self.assertEqual("test-harness", event["actor"])
            self.assertEqual(["tamper"], [event["kind"] for event in session.events])
            self.assertEqual(event, session.state["transcript"][-1]["event"])

    def test_missing_ambiguous_invalid_or_symlink_case_never_mutates(self):
        scenario = load_campaign()["loaded_scenarios"][7]
        for mode in ("missing", "ambiguous", "invalid", "symlink"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                session = seed_workspace(scenario, campaign_dir=ROOT / "tests/skill_usability", parent=Path(temporary))
                outside = session.workspace / "case.json"
                outside.write_text('{"state":"complete"}')
                if mode == "symlink":
                    (session.work / "case.json").symlink_to(outside)
                elif mode != "missing":
                    (session.work / "case.json").write_text('{}' if mode == "invalid" else outside.read_text())
                    if mode == "ambiguous":
                        (session.work / "other").mkdir()
                        (session.work / "other/case.json").write_text(outside.read_text())
                before = work_file_hashes(session)
                with self.assertRaises(SessionError):
                    tamper_durable_case(session)
                self.assertEqual(before, work_file_hashes(session))
                self.assertEqual('{"state":"complete"}', outside.read_text())
                self.assertFalse(session.events)

    def test_seed_failure_removes_partial_workspace(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch(
            "skill_usability.sessions.copy_plugin", side_effect=OSError("copy failed")
        ):
            with self.assertRaises(OSError):
                seed_workspace({}, campaign_dir=ROOT / "tests/skill_usability", parent=Path(temporary))
            self.assertEqual([], list(Path(temporary).iterdir()))

    def test_markdown_recommendation_and_non_question_mark_request(self):
        scenario = load_campaign()["loaded_scenarios"][0]
        with tempfile.TemporaryDirectory() as temporary:
            session = seed_workspace(scenario, campaign_dir=ROOT / "tests/skill_usability", parent=Path(temporary))
            CodexSessionAdapter()._observe_final(session, "Recommended next: **`compile-user-map`**\nPlease provide the map.")
            self.assertEqual("compile-user-map", next(e["recommended_skill"] for e in session.events if e["kind"] == "recommendation"))
            self.assertTrue(session.awaiting_user)

    def test_receipt_hold_count_is_not_an_evidence_array(self):
        scenario = load_campaign()["loaded_scenarios"][0]
        with tempfile.TemporaryDirectory() as temporary:
            session = seed_workspace(scenario, campaign_dir=ROOT / "tests/skill_usability", parent=Path(temporary))
            (session.work / "receipt.json").write_text('{"holds": 0}')
            (session.work / "evidence.json").write_text(json.dumps({"holds": [{"code": "actual-hold"}]}))
            CodexSessionAdapter()._observe_artifacts(session)
            self.assertEqual(["actual-hold"], [event["code"] for event in session.events if event["kind"] == "hold"])

    def test_two_fake_trials_get_distinct_workspaces_and_session_ids(self) -> None:
        campaign = load_campaign()
        scenario = campaign["loaded_scenarios"][0]
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            first = seed_workspace(scenario, campaign_dir=ROOT / "tests" / "skill_usability", parent=parent)
            second = seed_workspace(scenario, campaign_dir=ROOT / "tests" / "skill_usability", parent=parent)
            self.assertNotEqual(first.workspace, second.workspace)
            self.assertNotEqual(first.session_id, second.session_id)
            self.assertTrue(first.work.exists())
            self.assertTrue(second.work.exists())
            self.assertFalse((first.work / "shared.json").exists())

    def test_missing_codex_capability_is_not_run_before_worker_start(self) -> None:
        adapter = CodexSessionAdapter()
        with mock.patch("skill_usability.sessions.shutil.which", return_value=None):
            with self.assertRaises(PreflightUnavailable) as raised:
                adapter.preflight()
        self.assertEqual("codex-cli", raised.exception.capability)

    def test_worker_env_strips_secrets_and_cannot_read_oracle_expected(self) -> None:
        campaign = load_campaign()
        scenario = campaign["loaded_scenarios"][1]
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            session = seed_workspace(scenario, campaign_dir=ROOT / "tests" / "skill_usability", parent=parent)
            env = stripped_worker_env(home=session.home, runtime=session.plugin_root / "runtime")
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("CODEX_API_KEY", env)
            oracle = session.workspace / "oracle-expected" / "expected.json"
            self.assertTrue(oracle.is_file())
            self.assertNotIn(str(oracle), env.values())
            sibling = parent / "other-trial"
            self.assertFalse(sibling.exists())

    def test_altered_plugin_hash_stops_before_first_turn(self) -> None:
        campaign = load_campaign()
        scenario = campaign["loaded_scenarios"][0]
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            session = seed_workspace(scenario, campaign_dir=ROOT / "tests" / "skill_usability", parent=parent)
            marker = session.plugin_root / "skills" / "modbus-help" / "SKILL.md"
            marker.write_text(marker.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
            session.loaded_plugin_hash = hash_tree(session.plugin_root)
            adapter = FakeSessionAdapter()
            with self.assertRaises(SessionError):
                adapter.start(session)

    def test_continuation_rejects_a_different_session_id_mismatch_via_new_id(self) -> None:
        campaign = load_campaign()
        scenario = campaign["loaded_scenarios"][3]
        with tempfile.TemporaryDirectory() as temporary:
            session = seed_workspace(
                scenario,
                campaign_dir=ROOT / "tests" / "skill_usability",
                parent=Path(temporary),
            )
            adapter = FakeSessionAdapter()
            adapter.start(session)
            previous = session.session_id
            session.interrupted = True
            resumed = adapter.continue_session(session, None)
            self.assertNotEqual(previous, resumed.session_id)

    def test_budgets_are_present_on_the_campaign(self) -> None:
        campaign = load_campaign()
        for key in ("max_turns", "max_seconds", "max_tool_calls", "max_output_bytes"):
            self.assertGreater(campaign["budget"][key], 0)
            self.assertLess(campaign["budget"][key], 10_000_001)


if __name__ == "__main__":
    unittest.main()
