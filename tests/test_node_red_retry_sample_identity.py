from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))

from modbus_skills.analysis import analyze_capture  # noqa: E402
from modbus_skills.node_red import _capture_function  # noqa: E402


HARNESS = r"""
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const state = {modbusSkillsRunId: 'synthetic-run'};
const flow = {get: key => state[key], set: (key, value) => {state[key] = value;}};
let clock = Date.parse('2026-01-01T00:00:00.000Z');
class FixedDate extends Date {
  constructor(...args) { super(...(args.length ? args : [clock])); }
  static now() { return clock; }
}
const capture = new Function('msg', 'flow', 'Date', 'env', input.function);
const env = {get: () => null};
const point = {point_id: 'point', relative_offset: 0, word_count: 1,
  datatype: 'uint16', byte_order: 'AB', scale: 1, offset: 0};
const continuations = [];
for (const item of input.messages) {
  clock += 250;
  const request = {block_id: 'block', route_id: 'synthetic-route', unit_id: 7,
    area: 'holding-register', function_code: 3, start_offset: 401,
    point_specs: [point], started_at_ms: clock - 125};
  if (Object.hasOwn(item, 'attempt')) request.attempt = item.attempt;
  const success = item.outcome === 'success';
  const msg = {modbusSkillsRequest: request,
    payload: success ? [{...point, raw_values: [43981], decode_status: 'decoded',
      decoded_value: 43981, engineering_value: 43981}] : {state: 'short-response'},
    ...(success ? {} : {modbusSkillsReadError: {reason: 'incomplete-response'}})};
  continuations.push(capture(msg, flow, FixedDate, env)[1]);
}
const finalized = capture({modbusSkillsFinalize: true, payload: {state: 'drained'}}, flow, FixedDate, env);
process.stdout.write(JSON.stringify({capture: JSON.parse(finalized[0].payload), continuations}));
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is required to execute generated capture code")
class RetrySampleIdentityTests(unittest.TestCase):
    def capture(self, *messages: dict[str, object]) -> dict[str, object]:
        result = subprocess.run(
            [shutil.which("node"), "-e", HARNESS],
            input=json.dumps({
                "function": _capture_function("map", "plan", ["block"], [7]),
                "messages": list(messages),
            }),
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_first_missing_and_noninteger_attempts_preserve_legacy_ids(self) -> None:
        variants = [{}] + [{"attempt": value} for value in (0, -1, 1.5, "1", True, False, None, [1], {"value": 1})]
        for attempt in variants:
            for outcome, suffix in (("error", ":error"), ("success", "")):
                with self.subTest(attempt=attempt, outcome=outcome):
                    result = self.capture({**attempt, "outcome": outcome})
                    sample = result["capture"]["samples"][0]
                    self.assertEqual("synthetic-run:block:point" + suffix, sample["sample_id"])
                    self.assertEqual("synthetic-run:block", sample["request_id"])
                    self.assertEqual("block", sample["block_id"])

    def test_retry_errors_have_distinct_ids_and_survive_actual_analysis(self) -> None:
        result = self.capture({"attempt": 0}, {"attempt": 1})
        capture = result["capture"]
        samples = capture["samples"]
        self.assertEqual(
            ["synthetic-run:block:point:error", "synthetic-run:block:point:error:attempt-1"],
            [sample["sample_id"] for sample in samples],
        )
        for sample in samples:
            self.assertEqual("synthetic-run:block", sample["request_id"])
            self.assertEqual("block", sample["block_id"])
            self.assertEqual("incomplete-response", sample["error"])
            self.assertEqual([], sample["raw_words"])
            self.assertEqual(125, sample["response_time_ms"])
            self.assertFalse(sample["success"])
            self.assertNotIn("derived_values", sample)
        self.assertNotEqual(samples[0]["timestamp"], samples[1]["timestamp"])
        self.assertEqual([True, False], [item["modbusSkillsRetry"] for item in result["continuations"]])
        self.assertEqual(capture["expected_request_ids"], capture["completed_request_ids"])
        analysis = analyze_capture(capture)
        self.assertEqual(2, analysis["bounds"]["unique_sample_count"])
        self.assertEqual(2, analysis["communications"]["error_count"])
        self.assertEqual(0, analysis["summary"]["duplicate_samples"])

    def test_true_duplicate_delivery_still_deduplicates(self) -> None:
        capture = self.capture({"attempt": 0})["capture"]
        capture["samples"].append(deepcopy(capture["samples"][0]))
        analysis = analyze_capture(capture)
        self.assertEqual(2, analysis["bounds"]["input_sample_count"])
        self.assertEqual(1, analysis["bounds"]["unique_sample_count"])
        self.assertEqual(1, analysis["summary"]["duplicate_samples"])
        self.assertEqual(1, analysis["communications"]["error_count"])

    def test_error_then_success_retry_is_distinct_without_changing_values(self) -> None:
        result = self.capture({"attempt": 0}, {"attempt": 1, "outcome": "success"})
        capture = result["capture"]
        first, second = capture["samples"]
        self.assertEqual("synthetic-run:block:point:error", first["sample_id"])
        self.assertEqual("synthetic-run:block:point:attempt-1", second["sample_id"])
        self.assertEqual(first["request_id"], second["request_id"])
        self.assertEqual(first["block_id"], second["block_id"])
        self.assertEqual([43981], second["raw_words"])
        self.assertEqual(43981, second["derived_values"]["decoded_value"])
        self.assertEqual(43981, second["derived_values"]["engineering_value"])
        analysis = analyze_capture(capture)
        self.assertEqual(2, analysis["bounds"]["unique_sample_count"])
        self.assertEqual(1, analysis["communications"]["error_count"])
        self.assertEqual(0, analysis["summary"]["duplicate_samples"])

    def test_positive_integer_retry_suffix_is_deterministic(self) -> None:
        for attempt in (1, 2, 5):
            with self.subTest(attempt=attempt):
                first = self.capture({"attempt": attempt})
                second = self.capture({"attempt": attempt})
                self.assertEqual(first, second)
                self.assertEqual(
                    f"synthetic-run:block:point:error:attempt-{attempt}",
                    first["capture"]["samples"][0]["sample_id"],
                )


if __name__ == "__main__":
    unittest.main()
