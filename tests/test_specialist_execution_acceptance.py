from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import shlex
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_specialist_execution_acceptance import create_inputs, environment_preflight
from skill_usability.contracts import load_campaign
from skill_usability.execution_evidence import observe_execution, wrapper_tokens
from skill_usability.oracles import evaluate_trial
from skill_usability.sessions import seed_workspace


class SpecialistExecutionTests(unittest.TestCase):
    def test_missing_pdf_dependency_cannot_start_a_positive_pdf_cohort(self):
        with mock.patch.dict(sys.modules, {"pdfplumber": None}):
            self.assertEqual("unavailable", environment_preflight(require_pdf=True)["status"])
            self.assertEqual("ready", environment_preflight(require_pdf=False)["status"])
        with mock.patch.dict(sys.modules, {"pdfplumber": mock.Mock(__version__="synthetic-version")}):
            receipt = environment_preflight(require_pdf=True)
            self.assertEqual("ready", receipt["status"])
            self.assertEqual("synthetic-version", receipt["pdfplumber_version"])

    def test_all19_positive_cases_validate_and_requests_are_portable(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            campaign_path = create_inputs(output)
            campaign = load_campaign(campaign_path)
            self.assertEqual(19, len(campaign["loaded_scenarios"]))
            inputs = campaign_path.parent / "fixtures"
            self.assertEqual("../fixtures/good-raw.json", json.loads((inputs / "compile-positive.json").read_text())["source"]["path"])
            self.assertEqual("../fixtures/good.json", json.loads((inputs / "pack-positive.json").read_text())["map"])
            for scenario in campaign["loaded_scenarios"]:
                names = {Path(item["path"]).name for item in scenario["fixtures"]}
                self.assertEqual(names, set(scenario["oracle_profile"]["fixture_hashes"]))
                self.assertNotIn("model_needed", scenario)

    def test_nested_requests_run_from_worker_cwd_without_fixture_repairs(self):
        from run_direct_skill_acceptance import command_arguments
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = create_inputs(root / "inputs")
            for skill in ("compile-user-map", "build-tool-pack"):
                scenario = next(s for s in load_campaign(path)["loaded_scenarios"] if s["skill"] == skill)
                session = seed_workspace(scenario, campaign_dir=path.parent, parent=root)
                command = [sys.executable, "-B", str(session.plugin_root / f"skills/{skill}/scripts/run.py"),
                           *command_arguments(skill, "positive", Path("../fixtures"), Path("."))]
                completed = subprocess.run(command, cwd=session.work, env=session.env,
                                           capture_output=True, text=True, timeout=20)
                self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                item = {"id": "direct-request-replay", "type": "commandExecution", "command": shlex.join(command),
                        "cwd": str(session.work), "exitCode": completed.returncode, "aggregatedOutput": completed.stdout}
                session.state["transcript"] = [{"method": method, "params": {"item": copy.deepcopy(item)}}
                                               for method in ("item/started", "item/completed")]
                self.assertTrue(observe_execution(session, session.work)["proven"])

    def test_worker_prose_cannot_substitute_for_actual_wrapper_and_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign_path = create_inputs(root / "inputs")
            scenario = next(item for item in load_campaign(campaign_path)["loaded_scenarios"] if item["skill"] == "parse-map")
            session = seed_workspace(scenario, campaign_dir=campaign_path.parent, parent=root)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            session.state["transcript"] = [{"method": "item/completed", "params": {"item": {
                "type": "agentMessage", "text": "All fields verified; wrapper executed successfully."}}}]
            proof = observe_execution(session, snapshot)
            self.assertFalse(proof["proven"])
            self.assertFalse(next(check for check in proof["checks"] if check["name"] == "actual trusted wrapper completed")["passed"])
            result = evaluate_trial(scenario=scenario, events=[{"kind": "skill-selected", "skill": "parse-map"}, proof],
                                    artifacts=[], snapshot=snapshot, terminal_reason="completed", execution_status="completed")
            self.assertIn("specialist-execution-unproven", result["issue_codes"])
            (session.fixtures / "parse-good.json").write_text("{}")
            proof = observe_execution(session, snapshot)
            self.assertFalse(next(check for check in proof["checks"] if check["name"] == "all fixture bytes unchanged")["passed"])

    def test_observer_checks_actual_wrapper_bytes_and_final_fields_not_claims(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign_path = create_inputs(root / "inputs")
            scenario = next(item for item in load_campaign(campaign_path)["loaded_scenarios"] if item["skill"] == "parse-map")
            session = seed_workspace(scenario, campaign_dir=campaign_path.parent, parent=root)
            command = [sys.executable, "-B", str(session.plugin_root / "skills/parse-map/scripts/run.py"),
                       "--input", str(session.fixtures / "parse-good.json"), "--output", str(session.work / "result.json")]
            completed = subprocess.run(command, text=True, capture_output=True, check=True, timeout=10)
            item = {"id": "unit-test-replay", "type": "commandExecution", "command": shlex.join(command),
                    "exitCode": completed.returncode, "aggregatedOutput": completed.stdout}
            session.state["transcript"] = [{"method": method, "params": {"item": copy.deepcopy(item)}}
                                           for method in ("item/started", "item/completed")]
            self.assertTrue(observe_execution(session, session.work)["proven"])
            session.state["transcript"].pop(0)
            self.assertFalse(observe_execution(session, session.work)["proven"])
            session.state["transcript"].insert(0, {"method": "item/started", "params": {"item": copy.deepcopy(item)}})
            result = json.loads((session.work / "result.json").read_text())
            result["records"][0]["protocol_offset"] = 11
            (session.work / "result.json").write_text(json.dumps(result))
            self.assertFalse(observe_execution(session, session.work)["proven"])

    def test_read_only_prefix_can_precede_final_wrapper_but_arbitrary_execution_cannot(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plugin, work = root / "plugin", root / "work"
            plugin.mkdir()
            work.mkdir()
            (plugin / "SKILL.md").write_text("synthetic")
            session = SimpleNamespace(plugin_root=plugin, work=work)
            final = "python3 ../plugin/run.py --input source.json --output result.json"
            command = lambda prefix: {"command": shlex.join(["/usr/bin/bash", "-lc", prefix + "; " + final]), "cwd": str(work)}
            self.assertEqual(shlex.split(final), wrapper_tokens(command("rg -n -A 10 synthetic ../plugin/SKILL.md; cat ../plugin/SKILL.md"), session))
            for prefix in ("exit 0", "python3 mutate.py", "cat /etc/passwd", "rg --pre mutate synthetic ../plugin/SKILL.md"):
                self.assertEqual([], wrapper_tokens(command(prefix), session))

    def test_successful_and_chain_proves_wrapper_not_arbitrary_or_failed_suffix(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plugin, work = root / "plugin", root / "work"
            plugin.mkdir()
            work.mkdir()
            (plugin / "SKILL.md").write_text("synthetic")
            (work / "result.json").write_text("{}")
            session = SimpleNamespace(plugin_root=plugin, work=work)
            final = "python3 ../plugin/run.py --output result.json"
            def item(script, code=0):
                return {"command": shlex.join(["/usr/bin/bash", "-lc", script]), "cwd": str(work), "exitCode": code}
            for script in ("cat ../plugin/SKILL.md && " + final, final + " && cat result.json",
                           "cat ../plugin/SKILL.md; " + final + " && cat result.json"):
                self.assertEqual(shlex.split(final), wrapper_tokens(item(script), session))
                self.assertEqual([], wrapper_tokens(item(script, 1), session))
            for script in (final + "; cat result.json", final + " && touch result.json",
                           final + " || cat result.json", final + " | cat result.json",
                           "exit 0; " + final, final + " && cat /etc/passwd",
                           "python3 forge.py; " + final):
                self.assertEqual([], wrapper_tokens(item(script), session), script)

    def test_scoped_pdf_temporary_directory_preserves_wrapper_proof(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary) / "work"
            work.mkdir()
            session = SimpleNamespace(work=work, plugin_root=Path(temporary) / "plugin")
            final = "python3 -B ../plugin/run.py --input ../fixtures/good.pdf --output bundle"
            expected = shlex.split(final)
            expected.remove("-B")
            def item(script, cwd=work):
                return {"command": shlex.join(["/usr/bin/bash", "-lc", script]), "cwd": str(cwd)}
            for prefix in ('TMPDIR="$PWD" ', 'PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD" '):
                self.assertEqual(expected, wrapper_tokens(item(prefix + final), session))
            self.assertEqual([], wrapper_tokens(item('TMPDIR="$PWD" ' + final, work.parent), session))
            for prefix in ('TMPDIR=\'$PWD\' ', 'TMPDIR="${PWD}" ', 'TMPDIR="$(pwd)" ',
                           'TMPDIR=/untrusted ', 'TMPDIR="$PWD" PYTHONPATH=/untrusted ',
                           'PYTHONPATH=/untrusted TMPDIR="$PWD" ', 'TMPDIR="$PWD" env '):
                self.assertEqual([], wrapper_tokens(item(prefix + final), session), prefix)
            for suffix in ('; true', ' && cat bundle/result.json', ' | cat', ' > output', '\ntrue'):
                self.assertEqual([], wrapper_tokens(item('TMPDIR="$PWD" ' + final + suffix), session), suffix)

    def test_temporary_directory_wrapper_still_requires_faithful_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign = create_inputs(root / "inputs")
            scenario = next(s for s in load_campaign(campaign)["loaded_scenarios"] if s["skill"] == "parse-map")
            session = seed_workspace(scenario, campaign_dir=campaign.parent, parent=root)
            script = 'PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD" ' + shlex.join([
                sys.executable, "-B", str(session.plugin_root / "skills/parse-map/scripts/run.py"),
                "--input", str(session.fixtures / "parse-good.json"), "--output", "result.json"])
            command = ["/usr/bin/bash", "-lc", script]
            completed = subprocess.run(command, cwd=session.work, env=session.env,
                                       capture_output=True, text=True, timeout=20)
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            item = {"id": "scoped-temp-replay", "type": "commandExecution", "command": shlex.join(command),
                    "cwd": str(session.work), "exitCode": completed.returncode, "aggregatedOutput": completed.stdout}
            session.state["transcript"] = [{"method": method, "params": {"item": copy.deepcopy(item)}}
                                           for method in ("item/started", "item/completed")]
            self.assertTrue(observe_execution(session, session.work)["proven"])
            result = session.work / "result.json"
            payload = json.loads(result.read_text())
            payload["records"][0]["protocol_offset"] = 65535
            result.write_text(json.dumps(payload))
            self.assertFalse(observe_execution(session, session.work)["proven"])


if __name__ == "__main__":
    unittest.main()
