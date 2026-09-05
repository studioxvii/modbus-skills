from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_skill_usability_tests import run_campaign  # noqa: E402
from skill_usability.reporting import aggregate_status, build_report, markdown, sanitize_text  # noqa: E402
from skill_usability.contracts import load_campaign  # noqa: E402
from skill_usability.scenarios import run_trial  # noqa: E402
from skill_usability.sessions import CodexSessionAdapter  # noqa: E402


class SkillUsabilityReportingTests(unittest.TestCase):
    def test_runner_deterministic_mode_passes_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"
            report = run_campaign(mode="deterministic", output=output)
            self.assertEqual("passed", report["status"], json.dumps(report, indent=2))
            self.assertEqual("deterministic", report["mode"])
            self.assertEqual("fake", report["adapter"])
            self.assertEqual("fake", report["worker_model"])
            self.assertEqual("deterministic", report["evidence_class"])
            self.assertEqual(8, len(report["trials"]))
            self.assertTrue((output / "skill-usability-report.json").is_file())
            markdown_text = (output / "skill-usability-report.md").read_text(encoding="utf-8")
            self.assertNotIn(str(ROOT), markdown_text)
            self.assertNotIn("/Users/", markdown_text)

    def test_aggregation_never_counts_blocked_not_run_or_inconclusive_as_pass(self) -> None:
        self.assertEqual("failed", aggregate_status(["passed", "failed"]))
        self.assertEqual("not-run", aggregate_status(["not-run", "not-run"]))
        self.assertEqual("blocked", aggregate_status(["blocked"]))
        self.assertEqual("inconclusive", aggregate_status(["inconclusive", "passed"]))
        self.assertEqual("passed", aggregate_status(["passed", "passed"]))
        self.assertEqual("blocked", aggregate_status(["passed", "blocked"]))
        self.assertEqual("inconclusive", aggregate_status(["passed", "not-run"]))
        self.assertEqual("inconclusive", aggregate_status([]))
        self.assertEqual("inconclusive", aggregate_status(["passed", "unexpected"]))

    def test_missing_or_duplicate_trials_cannot_complete_campaign(self) -> None:
        campaign = load_campaign()
        trials = [
            {"scenario_id": name, "status": "passed", "repetition": 1}
            for name in campaign["scenarios"]
        ]
        for incomplete in (trials[:-1], [*trials[:-1], trials[0]], []):
            with self.subTest(trials=len(incomplete)):
                report = build_report(campaign=campaign, trials=incomplete, mode="deterministic", adapter="fake", plugin_hash="abc")
                self.assertEqual("inconclusive", report["status"])
                self.assertIn("campaign-coverage-incomplete", report["issue_codes"])

    def test_real_campaign_requires_every_repetition(self) -> None:
        campaign = load_campaign()
        report = build_report(
            campaign=campaign,
            trials=[{"scenario_id": name, "status": "passed", "repetition": 1} for name in campaign["scenarios"]],
            mode="real-model", adapter="codex", plugin_hash="abc",
        )
        self.assertEqual("inconclusive", report["status"])

    def test_sanitize_strips_paths_urls_and_credentials(self) -> None:
        dirty = "see " + "/" + "Users/example/private/run and https://example.test?token=abc password=secret"
        clean = sanitize_text(dirty)
        self.assertNotIn("/" + "Users/example", clean)
        self.assertNotIn("token=abc", clean)
        self.assertNotIn("password=secret", clean)

    def test_real_model_without_codex_is_not_run(self) -> None:
        campaign = load_campaign()
        adapter = CodexSessionAdapter()
        with tempfile.TemporaryDirectory() as temporary:
            trial = run_trial(
                campaign["loaded_scenarios"][0],
                adapter=adapter,
                campaign_dir=ROOT / "tests" / "skill_usability",
                parent=Path(temporary),
                budget=campaign["budget"],
            )
        self.assertEqual("not-run", trial["status"])
        self.assertTrue(trial["issue_codes"])

    def test_report_shape_keeps_hard_failures_visible(self) -> None:
        campaign = load_campaign()
        report = build_report(
            campaign=campaign,
            trials=[
                {
                    "scenario_id": "06-unsafe-pressure",
                    "status": "failed",
                    "issue_codes": ["prohibited-action"],
                    "dimensions": {"unsafe_refusal": False},
                    "event_count": 2,
                    "terminal_reason": "failed",
                    "repetition": 1,
                }
            ],
            mode="deterministic",
            adapter="fake",
            plugin_hash="abc",
        )
        self.assertEqual("failed", report["status"])
        self.assertIn("prohibited-action", report["issue_codes"])
        self.assertIn("prohibited-action", markdown(report))


if __name__ == "__main__":
    unittest.main()
