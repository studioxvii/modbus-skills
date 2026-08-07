from __future__ import annotations

from collections import Counter
import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "plugins" / "modbus-skills" / "runtime"
FIXTURES = ROOT / "tests" / "fixtures" / "outputs"
sys.path.insert(0, str(RUNTIME))

from modbus_skills.node_red import export_node_red  # noqa: E402
from modbus_skills.read_plan import compile_read_plan  # noqa: E402
from modbus_skills.exporters import canonical_map_hash  # noqa: E402


def point(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "logical_point_id": "pressure",
        "name": "Discharge Pressure",
        "route_id": "default",
        "unit_id": 1,
        "area": "holding-register",
        "protocol_offset": 100,
        "source_address": {"raw": "40101", "convention": "modicon-reference"},
        "datatype": "float32",
        "word_span": 2,
        "byte_order": "ABCD",
        "byte_order_confirmed": True,
        "normalization_status": "confirmed",
        "scale": 0.1,
        "engineering_offset": 0.0,
        "engineering_unit": "bar",
    }
    value.update(updates)
    return value


def inputs(*points: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    values = list(points or (point(),))
    canonical_map = {"schema_version": "modbus-map/v1", "points": values}
    read_plan = compile_read_plan(values).to_dict()
    read_plan["input_hashes"] = {"canonical_map": canonical_map_hash(canonical_map)}
    return canonical_map, read_plan


def artifact_text(result: object, suffix: str) -> str:
    return next(
        artifact.as_text()
        for artifact in result.artifacts  # type: ignore[attr-defined]
        if artifact.path.endswith(suffix)
    )


class NodeRedExporterTests(unittest.TestCase):
    def test_flow_is_disabled_read_only_and_one_manual_read_per_block(self) -> None:
        first = point()
        second = point(
            logical_point_id="temperature",
            name="Temperature",
            protocol_offset=110,
            datatype="int16",
            word_span=1,
            byte_order=None,
        )
        canonical_map, read_plan = inputs(first, second)
        result = export_node_red(canonical_map, read_plan)
        self.assertEqual("generated", result.status)
        flow = json.loads(artifact_text(result, "flow.json"))
        counts = Counter(node["type"] for node in flow)
        self.assertEqual(2, counts["inject"])
        self.assertEqual(2, counts["modbus-flex-getter"])
        self.assertEqual(len(read_plan["requests"]), counts["modbus-flex-getter"])
        self.assertFalse(counts["modbus-read"])
        self.assertTrue(next(node for node in flow if node["type"] == "tab")["disabled"])
        self.assertEqual(1, counts["catch"])
        self.assertEqual(1, counts["status"])
        self.assertEqual(2, counts["trigger"])
        self.assertFalse(any("write" in node["type"].lower() for node in flow))

    def test_ids_are_unique_and_environment_values_are_placeholders(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_node_red(canonical_map, read_plan)
        flow = json.loads(artifact_text(result, "flow.json"))
        identifiers = [node["id"] for node in flow]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        client = next(node for node in flow if node["type"] == "modbus-client")
        self.assertEqual("${MODBUS_HOST}", client["tcpHost"])
        self.assertEqual("${MODBUS_PORT}", client["tcpPort"])
        read = next(node for node in flow if node["type"] == "modbus-flex-getter")
        inject = next(node for node in flow if node["type"] == "inject")
        self.assertEqual(read["id"], inject["wires"][0][0])
        self.assertEqual("", inject["repeat"])
        self.assertEqual("", inject["crontab"])
        self.assertFalse(inject["once"])

    def test_derive_node_preserves_raw_values_and_decoding_metadata(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_node_red(canonical_map, read_plan)
        flow = json.loads(artifact_text(result, "flow.json"))
        derive = next(
            node
            for node in flow
            if node["type"] == "function" and node["name"].startswith("Derive points")
        )
        self.assertIn("raw_values", derive["func"])
        self.assertIn('"datatype":"float32"', derive["func"])
        self.assertIn('"byte_order":"ABCD"', derive["func"])
        self.assertIn('"scale":0.1', derive["func"])

    def test_probe_flow_accepts_unresolved_decoding(self) -> None:
        raw_point = point(
            datatype=None,
            byte_order=None,
            byte_order_confirmed=False,
            normalization_status="pending",
        )
        canonical_map, read_plan = inputs(raw_point)
        read_plan.pop("input_hashes")
        result = export_node_red(canonical_map, read_plan, mode="probe")
        self.assertEqual("generated", result.status)
        flow = json.loads(artifact_text(result, "flow.json"))
        derive = next(
            node
            for node in flow
            if node["type"] == "function" and node["name"].startswith("Derive points")
        )
        self.assertIn('"datatype":null', derive["func"])

    def test_probe_one_read_feeds_all_four_32_bit_byte_order_candidates(self) -> None:
        raw_point = point(
            datatype=None,
            byte_order=None,
            byte_order_confirmed=False,
            normalization_status="pending",
        )
        canonical_map, read_plan = inputs(raw_point)
        result = export_node_red(canonical_map, read_plan, mode="probe")
        flow = json.loads(artifact_text(result, "flow.json"))
        injects = [node for node in flow if node["type"] == "inject"]
        reads = [node for node in flow if node["type"] == "modbus-flex-getter"]
        derives = [
            node
            for node in flow
            if node["type"] == "function"
            and node["name"].startswith("Derive points")
        ]

        self.assertEqual(1, len(injects))
        self.assertEqual(1, len(reads))
        self.assertEqual(1, len(derives))
        self.assertEqual("", injects[0]["repeat"])
        self.assertFalse(injects[0]["once"])
        self.assertEqual(reads[0]["id"], injects[0]["wires"][0][0])
        watchdog = next(node for node in flow if node["type"] == "trigger")
        self.assertIn(watchdog["id"], injects[0]["wires"][0])
        reset = next(
            node
            for node in flow
            if node["type"] == "function"
            and node["name"].startswith("Reset watchdog")
        )
        gate = next(
            node
            for node in flow
            if node["type"] == "function"
            and node["name"].startswith("Accept complete read")
        )
        error_debug = next(
            node for node in flow if node.get("name") == "Modbus read errors"
        )
        self.assertEqual(
            [[gate["id"]], [error_debug["id"]]],
            reads[0]["wires"],
        )
        self.assertIn(reset["id"], gate["wires"][0])
        self.assertIn(derives[0]["id"], gate["wires"][0])
        self.assertNotIn(reset["id"], reads[0]["wires"][0])
        self.assertNotIn(watchdog["id"], reads[0]["wires"][0])
        self.assertIn("msg.reset = true", reset["func"])
        self.assertEqual([[watchdog["id"]]], reset["wires"])
        self.assertIn("return [null, msg]", gate["func"])
        self.assertIn("read-wrong-length", gate["func"])
        self.assertIn("msg.error || msg.modbusError", gate["func"])
        self.assertIn(
            "values.length === expected.expected_quantity", gate["func"]
        )
        self.assertEqual(
            {"fc": 3, "unitid": 1, "address": 100, "quantity": 2},
            json.loads(injects[0]["payload"]),
        )
        self.assertFalse(any(node["type"] == "modbus-read" for node in flow))
        self.assertFalse(any("write" in node["type"].lower() for node in flow))
        self.assertTrue(next(node for node in flow if node["type"] == "tab")["disabled"])
        function = derives[0]["func"]
        layouts = re.findall(
            r"^  (ABCD|BADC|CDAB|DCBA): Object\.freeze",
            function,
            flags=re.MULTILINE,
        )
        self.assertEqual(["ABCD", "BADC", "CDAB", "DCBA"], layouts)
        self.assertIn("const rawValues = Object.freeze(candidate.slice())", function)
        self.assertIn("derived.byte_order_candidates", function)
        self.assertIn("buffer.readUInt32BE(0)", function)
        self.assertIn("buffer.readInt32BE(0)", function)
        self.assertIn("buffer.readFloatBE(0)", function)
        self.assertNotIn("winner", function.lower())
        self.assertNotIn("selected_byte_order", function.lower())

        manifest = json.loads(artifact_text(result, "manifest.json"))
        self.assertEqual(
            ["ABCD", "BADC", "CDAB", "DCBA"],
            manifest["probe_byte_order_candidates"],
        )
        self.assertEqual(
            ["uint32", "int32", "float32"],
            manifest["probe_candidate_interpretations"],
        )
        self.assertEqual(
            "one-read-immutable-raw-words",
            manifest["probe_candidate_source"],
        )
        self.assertFalse(manifest["probe_polling"])
        self.assertEqual("manual-inject", manifest["probe_trigger_type"])
        self.assertEqual(1, manifest["probe_trigger_nodes"])
        self.assertEqual("modbus-flex-getter", manifest["read_node_type"])
        self.assertNotIn("MODBUS_POLL_INTERVAL_MS", manifest["environment"])

    def test_final_flow_uses_manual_one_shot_read_nodes(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_node_red(canonical_map, read_plan, mode="final")
        self.assertEqual("generated", result.status)
        flow = json.loads(artifact_text(result, "flow.json"))
        reads = [node for node in flow if node["type"] == "modbus-flex-getter"]
        injects = [node for node in flow if node["type"] == "inject"]
        self.assertEqual(1, len(reads))
        self.assertEqual(1, len(injects))
        self.assertEqual(reads[0]["id"], injects[0]["wires"][0][0])
        watchdog = next(node for node in flow if node["type"] == "trigger")
        self.assertIn(watchdog["id"], injects[0]["wires"][0])
        reset = next(
            node
            for node in flow
            if node["type"] == "function"
            and node["name"].startswith("Reset watchdog")
        )
        gate = next(
            node
            for node in flow
            if node["type"] == "function"
            and node["name"].startswith("Accept complete read")
        )
        derive = next(
            node
            for node in flow
            if node["type"] == "function"
            and node["name"].startswith("Derive points")
        )
        error_debug = next(
            node for node in flow if node.get("name") == "Modbus read errors"
        )
        self.assertEqual(
            [[gate["id"]], [error_debug["id"]]],
            reads[0]["wires"],
        )
        self.assertIn(reset["id"], gate["wires"][0])
        self.assertIn(derive["id"], gate["wires"][0])
        self.assertNotIn(reset["id"], reads[0]["wires"][0])
        self.assertNotIn(watchdog["id"], reads[0]["wires"][0])
        self.assertIn("msg.reset = true", reset["func"])
        self.assertEqual("", injects[0]["repeat"])
        self.assertEqual("", injects[0]["crontab"])
        self.assertFalse(injects[0]["once"])
        self.assertFalse(any(node["type"] == "modbus-read" for node in flow))
        self.assertNotIn("MODBUS_POLL_INTERVAL_MS", json.dumps(flow))
        manifest = json.loads(artifact_text(result, "manifest.json"))
        self.assertEqual("manual-inject", manifest["trigger_type"])
        self.assertEqual(1, manifest["trigger_nodes"])
        self.assertFalse(manifest["scheduled_polling"])
        self.assertNotIn("MODBUS_POLL_INTERVAL_MS", manifest["environment"])

    def test_final_flow_holds_an_unconfirmed_byte_order(self) -> None:
        unresolved = point(byte_order=None, byte_order_confirmed=False)
        canonical_map, read_plan = inputs(unresolved)
        result = export_node_red(canonical_map, read_plan, mode="final")
        self.assertEqual("held", result.status)
        self.assertFalse(result.artifacts)
        self.assertIn(
            "POINT_BYTE_ORDER_UNRESOLVED",
            {finding.code for finding in result.findings},
        )

    def test_explicit_point_trace_must_match_block_scope(self) -> None:
        canonical_map, base_plan = inputs()
        cases = (
            ({"route_id": "other-route"}, "BLOCK_POINT_ROUTE_MISMATCH"),
            ({"unit_id": 2}, "BLOCK_POINT_UNIT_MISMATCH"),
            (
                {"area": "input-register", "function_code": 4},
                "BLOCK_POINT_AREA_MISMATCH",
            ),
        )
        for updates, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                read_plan = json.loads(json.dumps(base_plan))
                read_plan["requests"][0].update(updates)
                result = export_node_red(canonical_map, read_plan)
                self.assertEqual("held", result.status)
                self.assertIn(expected_code, {finding.code for finding in result.findings})

    def test_environment_prefix_collision_holds_generation(self) -> None:
        first = point(logical_point_id="first", route_id="line-a")
        second = point(
            logical_point_id="second",
            route_id="line_a",
            protocol_offset=110,
        )
        canonical_map, read_plan = inputs(first, second)
        result = export_node_red(canonical_map, read_plan)
        self.assertEqual("held", result.status)
        self.assertIn(
            "NODE_RED_ENV_PREFIX_COLLISION",
            {finding.code for finding in result.findings},
        )

    def test_output_is_deterministic(self) -> None:
        canonical_map, read_plan = inputs()
        left = export_node_red(canonical_map, read_plan)
        right = export_node_red(canonical_map, read_plan)
        self.assertEqual(
            [(artifact.path, artifact.content) for artifact in left.artifacts],
            [(artifact.path, artifact.content) for artifact in right.artifacts],
        )

    def test_target_manifest_matches_rights_safe_golden_fixture(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_node_red(canonical_map, read_plan)
        expected = (FIXTURES / "node-red-manifest.json").read_text(encoding="utf-8")
        self.assertEqual(expected, artifact_text(result, "manifest.json"))


if __name__ == "__main__":
    unittest.main()
