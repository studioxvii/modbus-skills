from __future__ import annotations

import sys
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
    seed_workspace,
    stripped_worker_env,
)


class SkillUsabilitySessionTests(unittest.TestCase):
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
