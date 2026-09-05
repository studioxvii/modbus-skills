from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.artifacts import stable_input_hash  # noqa: E402
from modbus_skills.cli import run_cli  # noqa: E402
from modbus_skills.comparison import MapComparisonError, compare_maps  # noqa: E402


class DirectSkillRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def point(self, **updates):
        return {"logical_point_id": "synthetic-point", "name": "Synthetic Readback", "route_id": "synthetic-route",
                "unit_id": 7, "area": "holding-register", "protocol_offset": 10, "datatype": "uint16", "word_span": 1,
                "access": "read-only", "function_code": 3, "normalization_status": "confirmed", **updates}

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value))
        return path

    def call(self, command, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = run_cli(command, list(map(str, arguments)))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_comparison_rejects_unsupported_and_mismatched_schemas(self):
        before = {"schema_version": "modbus-map/v1", "points": [self.point()]}
        for schema in ("unrelated/v99", "modbus-map/v2", "modbus-runtime-map/v1", None, []):
            with self.subTest(schema=schema):
                with self.assertRaises(MapComparisonError):
                    compare_maps(before, {"schema_version": schema, "points": [self.point()]})
        with self.assertRaises(MapComparisonError):
            compare_maps({"schema_version": [], "points": []}, [])
        with self.assertRaises(MapComparisonError):
            compare_maps({}, {})

    def test_unknown_composite_fields_never_become_moves_or_unchanged_points(self):
        for field, value in (("route_id", None), ("unit_id", None), ("unit_id", ""), ("unit_id", 0),
                             ("unit_id", True), ("unit_id", 255), ("area", "unknown"),
                             ("protocol_offset", None), ("protocol_offset", 65536), ("logical_point_id", "")):
            with self.subTest(field=field, value=value):
                after = self.point(**{field: value})
                for before in ([self.point()], [after]):
                    with self.assertRaisesRegex(MapComparisonError, "held.*" + field):
                        compare_maps(before, [after])

    def test_valid_zero_offset_and_physical_move_remain_comparable(self):
        result = compare_maps([self.point(protocol_offset=0)], [self.point(protocol_offset=1)])
        self.assertEqual(1, result["summary"]["moved"])
        self.assertEqual(0, result["moved"][0]["before_identity"]["protocol_offset"])
        result = compare_maps({"points": [self.point()]}, {"points": [self.point()]})
        self.assertEqual(1, result["summary"]["unchanged"])

    def test_comparison_cli_holds_without_creating_a_resolved_diff(self):
        before = self.write("before.json", {"schema_version": "modbus-map/v1", "points": [self.point()]})
        after = self.write("after.json", {"schema_version": "modbus-map/v1", "points": [self.point(unit_id=None)]})
        original = after.read_bytes()
        output = self.root / "diff.json"
        code, _, error = self.call("compare-maps", "--before", before, "--after", after, "--output", output)
        self.assertNotEqual(0, code)
        self.assertIn("unit_id", error)
        self.assertNotIn("Traceback", error)
        self.assertFalse(output.exists())
        self.assertEqual(original, after.read_bytes())

    def test_remap_applies_notation_but_preserves_existing_safety_holds(self):
        hold = {"code": "point.write-only-not-readable", "severity": "hold", "blocking": True, "point_ids": ["synthetic-point"]}
        source_hold = {"code": "synthetic-source-unresolved", "severity": "hold", "blocking": True}
        source = self.write("held.json", {"points": [self.point(access="write-only", function_code=6, normalization_status="pending")],
                                          "holds": [hold], "source_holds": [source_hold, hold]})
        original = source.read_bytes()
        output = self.root / "remap.json"
        code, _, error = self.call("remap-addresses", "--input", source, "--from", "protocol-offset", "--to", "modicon-reference", "--output", output)
        self.assertEqual(0, code, error)
        remapped = json.loads(output.read_text())
        self.assertTrue(remapped["applied"])
        self.assertEqual("held", remapped["status"])
        self.assertEqual([hold, source_hold], remapped["holds"])
        point = remapped["points"][0]
        self.assertEqual(10, point["protocol_offset"])
        self.assertEqual("40011", str(point["display_address"]))
        self.assertEqual(6, point["function_code"])
        self.assertEqual("write-only", point["access"])
        self.assertEqual("pending", point["normalization_status"])
        self.assertEqual(original, source.read_bytes())

    def test_nonblocking_remap_evidence_does_not_add_an_approval_gate(self):
        note = {"code": "synthetic-retained-note", "severity": "warning", "blocking": False}
        source = self.write("clean.json", {"points": [self.point()], "holds": [note]})
        output = self.root / "remap.json"
        code, _, error = self.call("remap-addresses", "--input", source, "--from", "protocol-offset", "--to", "modicon-reference", "--output", output)
        self.assertEqual(0, code, error)
        result = json.loads(output.read_text())
        self.assertEqual("ready", result["status"])
        self.assertEqual([note], result["holds"])
        self.assertTrue(result["applied"])

    def test_invalid_remap_still_does_not_apply_and_malformed_holds_are_rejected(self):
        for holds in ({}, ["not a hold"]):
            source = self.write("source.json", {"points": [self.point()], "holds": holds})
            code, _, error = self.call("remap-addresses", "--input", source, "--from", "protocol-offset", "--to", "modicon-reference", "--output", self.root / "bad.json")
            self.assertNotEqual(0, code)
            self.assertIn("hold objects", error)
        source = self.write("source.json", {"points": [self.point(protocol_offset=65536)]})
        output = self.root / "held.json"
        code, _, _ = self.call("remap-addresses", "--input", source, "--from", "protocol-offset", "--to", "modicon-reference", "--output", output)
        self.assertEqual(0, code)
        result = json.loads(output.read_text())
        self.assertFalse(result["applied"])
        self.assertNotIn("points", result)

    def prepare_pack(self):
        canonical = {"schema_version": "modbus-map/v1", "points": [self.point()]}
        map_path = self.write("map.json", canonical)
        plan_path = self.root / "plan.json"
        code, _, error = self.call("compile-read-plan", "--input", map_path, "--output", plan_path)
        self.assertEqual(0, code, error)
        request = {"mode": "final", "map": "map.json", "read_plan": "plan.json", "targets": [{"id": "node-red"}, {"id": "modpoll", "profile": "proconx-cli"}, {"id": "modscan"}]}
        return canonical, map_path, plan_path, request

    def test_tool_pack_preserves_exact_bare_map_and_plan_hashes(self):
        canonical, map_path, plan_path, request = self.prepare_pack()
        before_map, before_plan = map_path.read_bytes(), plan_path.read_bytes()
        request_path = self.write("pack.json", request)
        output = self.root / "bundle"
        code, _, error = self.call("build-tool-pack", "--request", request_path, "--output", output)
        self.assertEqual(0, code, error)
        result = json.loads((output / "tool-pack-result.json").read_text())
        self.assertEqual("generated", result["status"])
        self.assertEqual(stable_input_hash(canonical), result["input_hashes"]["canonical_map"])
        self.assertEqual(stable_input_hash(json.loads(before_plan)), result["input_hashes"]["read_plan"])
        self.assertEqual({"generated"}, {target["status"] for target in result["targets"]})
        self.assertEqual(before_map, map_path.read_bytes())
        self.assertEqual(before_plan, plan_path.read_bytes())

    def test_tool_pack_does_not_repair_a_genuinely_stale_plan(self):
        _, _, plan_path, request = self.prepare_pack()
        plan = json.loads(plan_path.read_text())
        plan["input_hashes"]["canonical_map"] = "0" * 64
        plan_path.write_text(json.dumps(plan))
        original = plan_path.read_bytes()
        request_path = self.write("pack.json", request)
        output = self.root / "bundle"
        code, _, error = self.call("build-tool-pack", "--request", request_path, "--output", output)
        self.assertEqual(0, code, error)
        result = json.loads((output / "tool-pack-result.json").read_text())
        self.assertEqual("held", result["status"])
        self.assertIn("PLAN_MAP_HASH_MISMATCH", {item["code"] for item in result["holds"]})
        self.assertEqual(original, plan_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
