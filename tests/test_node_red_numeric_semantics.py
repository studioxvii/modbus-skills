"""Execute generated JavaScript against independent synthetic numeric encodings."""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
CAPTURE_CONTRACT = json.loads((ROOT / "tests/node_red_live/fixtures/campaign.json").read_text())["capture"]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.exporters import canonical_map_hash
from modbus_skills.node_red import export_node_red
from modbus_skills.read_plan import compile_read_plan


def point(**updates):
    value = {"logical_point_id": "synthetic-value", "name": "Synthetic value", "route_id": "synthetic-route", "unit_id": 7, "area": "holding-register", "function_code": 3, "protocol_offset": 16, "word_span": 1, "datatype": "uint16", "normalization_status": "confirmed", "access": "read-only", "scale": 1, "engineering_offset": 0}
    value.update(updates)
    return value


def generate(points, mode="final"):
    canonical = {"schema_version": "modbus-map/v1", "points": points}
    # Planning addresses does not depend on transforms. Use valid structural
    # point copies so malformed-transform controls reach the target validator.
    plan = compile_read_plan([{**p, "scale": 1, "engineering_offset": 0} for p in points]).to_dict()
    plan["input_hashes"] = {"canonical_map": canonical_map_hash(canonical)}
    return export_node_red(canonical, plan, mode=mode)


def flow_of(result):
    return json.loads(next(a.as_text() for a in result.artifacts if a.path.endswith("flow.json")))


HARNESS = r"""
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const state = new Map();
const flow = {get: k => state.get(k), set: (k, v) => state.set(k, v)};
const env = {get: () => null};
const fn = name => input.flow.find(n => n.name === name).func;
const call = (name, msg) => Function('msg', 'flow', 'env', fn(name))(msg, flow, env);
const request = input.flow.find(n => n.modbusSkillsBlocks).modbusSkillsBlocks[0];
request.attempt = 0;
request.started_at_ms = Date.now();
if (input.specMutation) Object.assign(request.point_specs[0], input.specMutation);
let words = input.words;
if (input.special === 'nan') words = [NaN];
if (input.special === 'infinity') words = [Infinity];
flow.set('modbusSkillsActiveBlockId', request.block_id);
flow.set('modbusSkillsRunId', 'synthetic-offline');
let msg = {payload: input.objectPayload ? {data: words} : words, modbusSkillsRequest: request};
const gated = call('04 Validate response', msg);
msg = gated[0] ? call('05 Decode points', gated[0]) : gated[1];
const derived = gated[0] ? msg.payload : null;
const rawFrozen = Boolean(msg.modbusSkills && Object.isFrozen(msg.modbusSkills.raw_values));
const pointRawFrozen = derived ? derived.every(p => Object.isFrozen(p.raw_values)) : null;
const terminal = call('06 Terminal gate', msg);
const continuation = terminal[0] ? call('07 Build capture/v1', terminal[0])[1] : null;
if (input.oldCapture) flow.set('modbusSkillsCapture', input.oldCapture);
const html = call('Render live dashboard', {}).payload;
console.log(JSON.stringify({accepted: Boolean(gated[0]), rawFrozen, pointRawFrozen, derived,
  samples: flow.get('modbusSkillsCapture') || [], continuation, html, original: words}));
"""


class NodeRedNumericSemanticsTests(unittest.TestCase):
    def run_generated(self, points, words, *, mode="final", **options):
        if not shutil.which("node"):
            self.skipTest("Node.js required for generated-JavaScript execution")
        result = generate(points, mode)
        self.assertEqual("generated", result.status, [f.to_dict() for f in result.findings])
        proc = subprocess.run(["node", "-e", HARNESS], input=json.dumps({"flow": flow_of(result), "words": words, **options}), text=True, capture_output=True, timeout=5, check=True)
        observed = json.loads(proc.stdout)
        for sample in observed["samples"]:
            # Exercise the real capture contract for every generated response,
            # including malformed raw, NaN/Infinity and transform overflow.
            if options.get("oldCapture"):
                continue  # Historical dashboard-only controls are not new rows.
            for field in CAPTURE_CONTRACT["required_identity"] + CAPTURE_CONTRACT["required_fields"]:
                self.assertIn(field, sample)
            if sample["success"]:
                for field in CAPTURE_CONTRACT["success_fields"]:
                    self.assertIn(field, sample)
            else:
                for field in CAPTURE_CONTRACT["error_forbidden_fields"]:
                    self.assertNotIn(field, sample)
                self.assertTrue(sample["error"])
        return observed

    def assert_value(self, observed, expected):
        self.assertTrue(observed["accepted"])
        self.assertTrue(observed["rawFrozen"])
        self.assertTrue(observed["pointRawFrozen"])
        sample = observed["samples"][0]
        self.assertTrue(sample["success"])
        self.assertEqual("decoded", sample["derived_values"]["decode_status"])
        self.assertEqual(expected, sample["derived_values"]["engineering_value"])
        self.assertFalse(observed["continuation"]["modbusSkillsRetry"])

    def test_signed_unsigned16_scale_once_and_optional_identity(self):
        for dtype, words, updates, expected in [
            ("int16", [65534], {}, -2),
            ("uint16", [65535], {}, 65535),
            ("int16", [65279], {"byte_order": "BA"}, -2),
            ("int16", [65534], {"scale": 2, "engineering_offset": 9}, 5),
            ("uint16", [1234], {"scale": None, "engineering_offset": None}, 1234),
            ("uint16", [65535], {"scale": 0, "engineering_offset": 7}, 7),
        ]:
            with self.subTest(dtype=dtype, updates=updates):
                observed = self.run_generated([point(datatype=dtype, **updates)], words)
                self.assert_value(observed, expected)
                self.assertIn(f"<td>{expected}</td>", observed["html"])
                self.assertEqual(words, observed["samples"][0]["raw_words"])
                self.assertEqual(words, observed["original"])

    def test_all_four_independently_encoded32_bit_layouts(self):
        layouts = {"ABCD": [0, 1, 2, 3], "BADC": [1, 0, 3, 2], "CDAB": [2, 3, 0, 1], "DCBA": [3, 2, 1, 0]}
        for dtype, fmt, value in [("float32", ">f", 123.456), ("int32", ">i", -20000001), ("uint32", ">I", 0x12345678)]:
            raw = struct.pack(fmt, value)
            decoded = struct.unpack(fmt, raw)[0]
            for order, indices in layouts.items():
                with self.subTest(dtype=dtype, layout=order):
                    words = list(struct.unpack(">HH", bytes(raw[i] for i in indices)))
                    observed = self.run_generated([point(datatype=dtype, word_span=2, byte_order=order, byte_order_confirmed=True, scale=0.5, engineering_offset=-4)], words)
                    self.assert_value(observed, decoded * 0.5 - 4)
                    self.assertEqual(decoded, observed["derived"][0]["decoded_value"])
                    self.assertEqual(words, observed["samples"][0]["raw_words"])

    def test_strict_bit_scalars(self):
        for area, fc in [("coil", 1), ("discrete-input", 2)]:
            for raw in [True, False, 0, 1]:
                observed = self.run_generated([point(area=area, function_code=fc, datatype="bool")], [raw])
                self.assert_value(observed, bool(raw))
                self.assertIsInstance(observed["derived"][0]["engineering_value"], bool)
            for raw in [2, -1, None, "1", 0.5]:
                observed = self.run_generated([point(area=area, function_code=fc, datatype="boolean")], [raw])
                self.assertFalse(observed["accepted"])
                self.assertFalse(observed["samples"][0]["success"])

    def test_malformed_short_extra_responses_fail_and_preserve_raw_evidence(self):
        for words in [[None], ["12"], [-1], [65536], [1.5], [True], [], [1, 2]]:
            with self.subTest(words=words):
                observed = self.run_generated([point()], words)
                self.assertFalse(observed["accepted"])
                self.assertFalse(observed["samples"][0]["success"])
                self.assertEqual(words, observed["samples"][0]["raw_response"])
                self.assertNotIn("engineering_value", observed["samples"][0])
                self.assertIn("Unavailable (error)", observed["html"])
        for special in ["nan", "infinity"]:
            observed = self.run_generated([point()], [0], special=special)
            self.assertFalse(observed["accepted"])
        self.assert_value(self.run_generated([point()], [19], objectPayload=True), 19)

    def test_point_specific_nan_and_infinity_errors_do_not_retry(self):
        for words in [[0x7fc0, 0, 17], [0x7f80, 0, 17]]:
            observed = self.run_generated([point(datatype="float32", word_span=2, byte_order="ABCD", byte_order_confirmed=True), point(logical_point_id="good", protocol_offset=18)], words)
            self.assertTrue(observed["accepted"])
            self.assertFalse(observed["samples"][0]["success"])
            self.assertEqual("error", observed["derived"][0]["decode_status"])
            self.assertIsNone(observed["derived"][0]["engineering_value"])
            self.assertEqual(words[:2], observed["samples"][0]["raw_words"])
            self.assertTrue(observed["samples"][1]["success"])
            self.assertEqual(17, observed["derived"][1]["engineering_value"])
            self.assertFalse(observed["continuation"]["modbusSkillsRetry"])
            self.assertTrue(observed["continuation"]["modbusSkillsContinue"])

    def test_nonfinite_and_unsafe_integer_transform_results_are_errors(self):
        for p, words in [(point(scale=1e20), [65535]), (point(datatype="float32", word_span=2, byte_order="ABCD", byte_order_confirmed=True, scale=1e308), [0x7f7f, 0xffff])]:
            observed = self.run_generated([p], words)
            self.assertFalse(observed["samples"][0]["success"])
            self.assertIsNone(observed["derived"][0]["engineering_value"])
            self.assertFalse(observed["continuation"]["modbusSkillsRetry"])

    def test_unsupported64_string_bitfield_and_invalid_final_semantics_are_held(self):
        variants = [point(datatype=d, word_span=4, byte_order="ABCDEFGH", byte_order_confirmed=True) for d in ["uint64", "int64", "float64"]]
        variants += [point(datatype="string4", word_span=2), point(datatype="bitfield"), point(datatype="float32", word_span=3, byte_order="ABCD"), point(byte_order="DCBA"), point(area="coil", function_code=1), point(datatype="bool")]
        variants += [point(scale=v) for v in [True, "", "unknown", "0.1", math.inf, math.nan]]
        variants += [point(engineering_offset=v) for v in [False, "", math.inf, math.nan]]
        variants += [point(area="coil", function_code=1, datatype="bool", scale=2)]
        for p in variants:
            with self.subTest(point=p):
                result = generate([p])
                self.assertEqual("held", result.status)
                self.assertFalse(result.artifacts)

    def test_unresolved_probe_preserves_candidates_without_engineering_value(self):
        observed = self.run_generated([point(datatype=None, word_span=2, byte_order=None, byte_order_confirmed=False, normalization_status="pending", scale=2, engineering_offset=9)], [0x42f6, 0xe979], mode="probe")
        self.assertTrue(observed["samples"][0]["success"])
        self.assertEqual("raw", observed["derived"][0]["decode_status"])
        self.assertIsNone(observed["derived"][0]["engineering_value"])
        self.assertEqual({"ABCD", "BADC", "CDAB", "DCBA"}, set(observed["derived"][0]["byte_order_candidates"]))
        self.assertEqual(struct.unpack(">f", bytes.fromhex("42f6e979"))[0], observed["derived"][0]["byte_order_candidates"]["ABCD"]["float32"])
        self.assertIn("Unavailable (raw only)", observed["html"])
        self.assertFalse(observed["continuation"]["modbusSkillsRetry"])
        self.assertEqual("generated", generate([point(datatype="uint64", word_span=4, byte_order=None)], "probe").status)

    def test_dashboard_escapes_html_and_never_recalculates_old_raw_capture(self):
        observed = self.run_generated([point(logical_point_id='<img src=x onerror="bad">', engineering_unit="<script>bad</script>")], [12])
        self.assertNotIn("<img", observed["html"])
        self.assertNotIn("<script>", observed["html"])
        self.assertIn("&lt;img", observed["html"])
        old = [{"success": True, "point_id": "legacy", "unit_id": 7, "raw_words": [65534], "derived_values": {"raw_values": [65534], "scale": 2, "offset": 9}}]
        observed = self.run_generated([point()], [1], oldCapture=old)
        self.assertIn("Unavailable (raw only)", observed["html"])
        self.assertNotIn("131077", observed["html"])

    def test_defensive_point_decode_rejects_mutated_span_transform_or_layout(self):
        for mutation in [{"word_count": 2}, {"relative_offset": -1}, {"scale": "1"}, {"byte_order": "unknown"}]:
            observed = self.run_generated([point()], [1], specMutation=mutation)
            self.assertFalse(observed["samples"][0]["success"])
            self.assertFalse(observed["continuation"]["modbusSkillsRetry"])

    def test_unresolved_layout_is_not_reported_twice(self):
        result = generate([point(datatype="float32", word_span=2, byte_order=None)])
        codes = [finding.code for finding in result.findings]
        self.assertIn("POINT_BYTE_ORDER_UNRESOLVED", codes)
        self.assertNotIn("NODE_RED_FINAL_LAYOUT_UNSUPPORTED", codes)
        result = generate([point(datatype="float32", word_span=2, byte_order="not-a-layout")])
        self.assertIn("NODE_RED_FINAL_LAYOUT_UNSUPPORTED", [finding.code for finding in result.findings])


if __name__ == "__main__":
    unittest.main()
