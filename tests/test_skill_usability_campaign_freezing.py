from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_skill_usability_tests as runner  # noqa: E402
from skill_usability.sessions import SessionError, hash_tree, seed_workspace  # noqa: E402


class CampaignFreezingTests(unittest.TestCase):
    def inputs(self, root):
        plugin = root / "source-plugin"
        plugin.mkdir()
        (plugin / "build.txt").write_text("original plugin")
        campaign_dir = root / "source-campaign"
        fixture = campaign_dir / "fixtures" / "nested" / "input.json"
        fixture.parent.mkdir(parents=True)
        fixture.write_text('{"original": true}')
        scenario = {"scenario_id": "synthetic", "fixtures": [
            {"id": "input", "path": "fixtures/nested/input.json"},
        ]}
        campaign = {
            "campaign_id": "synthetic", "worker_model": "fake", "budget": {},
            "scenarios": ["synthetic"], "loaded_scenarios": [scenario],
            "real_model_repetitions": 2,
        }
        return plugin, campaign_dir, fixture, campaign

    def test_repetitions_use_identical_frozen_plugin_and_fixture_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plugin, directory, fixture, campaign = self.inputs(root)
            original_hash = hash_tree(plugin)
            observed = []
            parents = []

            def trial(scenario, **kwargs):
                parents.append(kwargs["parent"])
                self.assertNotEqual(plugin, kwargs["plugin_source"])
                self.assertNotEqual(directory, kwargs["campaign_dir"])
                # Campaign-relative fixture paths remain resolvable in the copy.
                frozen_fixture = kwargs["campaign_dir"] / scenario["fixtures"][0]["path"]
                self.assertEqual('{"original": true}', frozen_fixture.read_text())
                session = seed_workspace(scenario, campaign_dir=kwargs["campaign_dir"],
                    parent=kwargs["parent"], plugin_source=kwargs["plugin_source"])
                observed.append((session.loaded_plugin_hash, (session.fixtures / "input.json").read_text()))
                shutil.rmtree(session.workspace)
                (plugin / "build.txt").write_text("changed live plugin")
                fixture.write_text('{"changed": true}')
                return {"scenario_id": "synthetic", "repetition": kwargs["repetition"],
                        "status": "passed", "plugin_hash": session.loaded_plugin_hash}

            with mock.patch.object(runner, "load_campaign", return_value=campaign), mock.patch.object(
                runner, "make_adapter", return_value=runner.make_adapter("deterministic")
            ), mock.patch.object(runner, "run_trial", side_effect=trial):
                report = runner.run_campaign(mode="real-model", output=root / "output",
                    campaign_path=directory / "campaign.json", plugin_source=plugin)
            self.assertEqual("passed", report["status"])
            self.assertEqual([(original_hash, '{"original": true}')] * 2, observed)
            self.assertEqual([original_hash] * 2, [t["plugin_hash"] for t in report["trials"]])
            self.assertEqual(original_hash, report["hashes"]["plugin"])
            self.assertTrue(report["input_snapshot"]["copy_verified"])
            self.assertFalse(parents[0].exists())

    def test_plugin_mutation_during_copy_is_rejected_and_seed_is_cleaned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plugin, directory, _, campaign = self.inputs(root)
            parent = root / "trials"
            parent.mkdir()
            original_copy = shutil.copytree

            def changing_copy(source, target, **kwargs):
                result = original_copy(source, target, **kwargs)
                (plugin / "build.txt").write_text("changed during copy")
                return result

            with mock.patch("skill_usability.sessions.shutil.copytree", side_effect=changing_copy):
                with self.assertRaisesRegex(SessionError, "changed while copying"):
                    seed_workspace(campaign["loaded_scenarios"][0], campaign_dir=directory,
                                   parent=parent, plugin_source=plugin)
            self.assertEqual([], list(parent.iterdir()))

    def test_fixture_mutation_during_freeze_is_rejected_before_trials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plugin, directory, fixture, campaign = self.inputs(root)
            original_copy = shutil.copy2
            snapshot_parents = []

            def changing_copy(source, target, *args, **kwargs):
                result = original_copy(source, target, *args, **kwargs)
                if Path(source) == fixture:
                    snapshot_parents.append(Path(target).parents[4])
                    fixture.write_text("changed during copy")
                return result

            with mock.patch.object(runner, "load_campaign", return_value=campaign), mock.patch.object(
                runner.shutil, "copy2", side_effect=changing_copy
            ), mock.patch.object(runner, "run_trial") as trial:
                with self.assertRaisesRegex(runner.RunnerError, "fixtures changed"):
                    runner.run_campaign(mode="deterministic", output=root / "output",
                        campaign_path=directory / "campaign.json", plugin_source=plugin)
            trial.assert_not_called()
            self.assertFalse(snapshot_parents[0].exists())

    def test_frozen_fixture_tampering_cannot_produce_a_passing_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plugin, directory, _, campaign = self.inputs(root)

            def tamper(scenario, **kwargs):
                (kwargs["campaign_dir"] / scenario["fixtures"][0]["path"]).write_text("tampered")
                return {"scenario_id": "synthetic", "status": "passed", "repetition": 1,
                        "plugin_hash": hash_tree(kwargs["plugin_source"])}

            with mock.patch.object(runner, "load_campaign", return_value=campaign), mock.patch.object(
                runner, "run_trial", side_effect=tamper
            ):
                with self.assertRaisesRegex(runner.RunnerError, "frozen campaign inputs changed"):
                    runner.run_campaign(mode="deterministic", output=root / "output",
                        campaign_path=directory / "campaign.json", plugin_source=plugin)
            report = json.loads((root / "output/skill-usability-report.json").read_text())
            self.assertEqual("inconclusive", report["status"])
            self.assertIn("campaign-input-snapshot-changed", report["issue_codes"])

    def test_copy_failure_cleans_campaign_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plugin, directory, _, campaign = self.inputs(root)
            parents = []

            def failed_copy(destination, **kwargs):
                parents.append(destination.parent)
                raise OSError("synthetic copy failure")

            with mock.patch.object(runner, "load_campaign", return_value=campaign), mock.patch.object(
                runner, "copy_plugin", side_effect=failed_copy
            ), mock.patch.object(runner, "run_trial") as trial:
                with self.assertRaisesRegex(OSError, "synthetic copy failure"):
                    runner.run_campaign(mode="deterministic", output=root / "output",
                        campaign_path=directory / "campaign.json", plugin_source=plugin)
            trial.assert_not_called()
            self.assertFalse(parents[0].exists())

    def test_fixture_basename_collision_is_held_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plugin, directory, _, campaign = self.inputs(root)
            other = directory / "fixtures/other/input.json"
            other.parent.mkdir()
            other.write_text('{"other": true}')
            scenario = campaign["loaded_scenarios"][0]
            scenario["fixtures"].append({"id": "other", "path": "fixtures/other/input.json"})
            parent = root / "trials"
            parent.mkdir()
            with self.assertRaisesRegex(SessionError, "basenames collide"):
                seed_workspace(scenario, campaign_dir=directory, parent=parent, plugin_source=plugin)
            self.assertEqual([], list(parent.iterdir()))
            self.assertEqual('{"other": true}', other.read_text())

    def test_cleanup_failure_overwrites_complete_checkpoint_as_nonpassing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plugin, directory, _, campaign = self.inputs(root)
            original_cleanup = tempfile.TemporaryDirectory.cleanup

            def failed_cleanup(instance):
                original_cleanup(instance)
                raise OSError("synthetic cleanup failure")

            def trial(scenario, **kwargs):
                return {"scenario_id": "synthetic", "status": "passed", "repetition": 1,
                        "plugin_hash": hash_tree(kwargs["plugin_source"])}

            with mock.patch.object(runner, "load_campaign", return_value=campaign), mock.patch.object(
                runner, "run_trial", side_effect=trial
            ), mock.patch.object(tempfile.TemporaryDirectory, "cleanup", new=failed_cleanup):
                with self.assertRaisesRegex(OSError, "synthetic cleanup failure"):
                    runner.run_campaign(mode="deterministic", output=root / "output",
                        campaign_path=directory / "campaign.json", plugin_source=plugin)
            report = json.loads((root / "output/skill-usability-report.json").read_text())
            self.assertEqual("inconclusive", report["status"])
            self.assertIn("campaign-cleanup-failed", report["issue_codes"])


if __name__ == "__main__":
    unittest.main()
