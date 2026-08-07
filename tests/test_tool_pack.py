from __future__ import annotations

from itertools import combinations
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "plugins" / "modbus-skills" / "runtime"
sys.path.insert(0, str(RUNTIME))

from modbus_skills.artifacts import assert_artifact_envelope  # noqa: E402
from modbus_skills.exporters import Artifact, ExporterInputError  # noqa: E402
from modbus_skills.read_plan import compile_read_plan  # noqa: E402
from modbus_skills.tool_pack import SUPPORTED_TARGETS, build_tool_pack  # noqa: E402


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
    }
    value.update(updates)
    return value


def inputs(value: dict[str, object] | None = None) -> tuple[dict[str, object], object]:
    selected = value or point()
    return {"schema_version": "modbus-map/v1", "points": [selected]}, compile_read_plan([selected])


class ToolPackTests(unittest.TestCase):
    def test_all_seven_non_empty_target_combinations(self) -> None:
        canonical_map, read_plan = inputs()
        cases = [
            selection
            for size in range(1, len(SUPPORTED_TARGETS) + 1)
            for selection in combinations(SUPPORTED_TARGETS, size)
        ]
        self.assertEqual(7, len(cases))
        for selection in cases:
            with self.subTest(targets=selection):
                pack = build_tool_pack(canonical_map, read_plan, targets=selection)
                self.assertEqual("generated", pack.status)
                self.assertEqual(set(selection), {result.target for result in pack.target_results})
                self.assertTrue(all(result.map_hash == pack.map_hash for result in pack.target_results))
                self.assertTrue(all(result.read_plan_hash == pack.read_plan_hash for result in pack.target_results))

    def test_actual_compile_read_plan_object_is_accepted_end_to_end(self) -> None:
        canonical_map, read_plan = inputs()
        pack = build_tool_pack(
            canonical_map,
            read_plan,
            targets=("node-red", "modpoll", "modscan"),
        )
        self.assertEqual("generated", pack.status)
        flow = json.loads(pack.files()["node-red/flow.json"])
        reads = [node for node in flow if node["type"] == "modbus-read"]
        self.assertEqual(len(read_plan.requests), len(reads))
        self.assertEqual(str(read_plan.requests[0].start_offset), reads[0]["adr"])

    def test_checksums_cover_every_file_except_the_checksum_file(self) -> None:
        canonical_map, read_plan = inputs()
        pack = build_tool_pack(canonical_map, read_plan, targets=SUPPORTED_TARGETS)
        files = pack.files()
        rows = files["checksums.sha256"].decode("utf-8").splitlines()
        recorded = {}
        for row in rows:
            digest, path = row.split("  ", 1)
            recorded[path] = digest
        self.assertEqual(set(files) - {"checksums.sha256"}, set(recorded))
        for path, digest in recorded.items():
            self.assertEqual(hashlib.sha256(files[path]).hexdigest(), digest)

    def test_pack_and_zip_are_deterministic_and_target_order_independent(self) -> None:
        canonical_map, read_plan = inputs()
        left = build_tool_pack(canonical_map, read_plan, targets=SUPPORTED_TARGETS)
        right = build_tool_pack(canonical_map, read_plan, targets=tuple(reversed(SUPPORTED_TARGETS)))
        self.assertEqual(left.files(), right.files())
        self.assertEqual(left.to_zip_bytes(), right.to_zip_bytes())

    def test_no_timestamp_fields_are_added(self) -> None:
        canonical_map, read_plan = inputs()
        pack = build_tool_pack(canonical_map, read_plan, targets=SUPPORTED_TARGETS)
        manifest = pack.files()["manifest.json"].decode("utf-8")
        for key in ("created_at", "generated_at", "timestamp"):
            self.assertNotIn(f'"{key}"', manifest)

    def test_pack_manifest_and_nested_target_results_use_common_envelopes(self) -> None:
        canonical_map, read_plan = inputs()
        pack = build_tool_pack(
            canonical_map, read_plan, targets=("node-red", "modscan")
        )
        manifest = json.loads(pack.files()["manifest.json"])
        assert_artifact_envelope(manifest)
        self.assertEqual(
            "modbus-tool-pack-manifest/v1", manifest["schema_version"]
        )
        self.assertEqual("modbus-tool-pack-manifest", manifest["artifact_type"])
        for target in manifest["targets"]:
            assert_artifact_envelope(target)
            self.assertEqual("modbus-target-result/v1", target["schema_version"])

        target_manifest = json.loads(pack.files()["node-red/manifest.json"])
        assert_artifact_envelope(target_manifest)
        self.assertEqual(
            "modbus-target-manifest/v1", target_manifest["schema_version"]
        )

    def test_additional_zip_artifact_is_deterministic_and_not_in_core_files(self) -> None:
        canonical_map, read_plan = inputs()
        pack = build_tool_pack(canonical_map, read_plan, targets=("modscan",))
        result = Artifact.text(
            "tool-pack-result.json",
            "application/json",
            "{}\n",
            "cli-result",
        )
        self.assertNotIn(result.path, pack.files())
        self.assertEqual(
            pack.to_zip_bytes((result,)), pack.to_zip_bytes((result,))
        )
        with self.assertRaises(ExporterInputError):
            pack.to_zip_bytes(
                (
                    Artifact.text(
                        "manifest.json", "application/json", "{}\n", "duplicate"
                    ),
                )
            )

    def test_empty_duplicate_and_unknown_target_selections_fail(self) -> None:
        canonical_map, read_plan = inputs()
        cases = ((), ("node-red", "node-red"), ("unknown",))
        for selection in cases:
            with self.subTest(targets=selection), self.assertRaises(ExporterInputError):
                build_tool_pack(canonical_map, read_plan, targets=selection)

    def test_sensitive_map_and_plan_fields_fail_closed(self) -> None:
        canonical_map, read_plan_object = inputs()
        base_plan = read_plan_object.to_dict()
        sensitive_keys = (
            "password",
            "api_key",
            "accessToken",
            "client_secret",
            "private-key",
            "credentials",
        )
        for key in sensitive_keys:
            with self.subTest(source="map", key=key):
                unsafe_map = json.loads(json.dumps(canonical_map))
                unsafe_map["metadata"] = {key: "do-not-package-this"}
                with self.assertRaises(ExporterInputError) as caught:
                    build_tool_pack(
                        unsafe_map, base_plan, targets=("modscan",)
                    )
                self.assertNotIn("do-not-package-this", str(caught.exception))
            with self.subTest(source="plan", key=key):
                unsafe_plan = json.loads(json.dumps(base_plan))
                unsafe_plan["metadata"] = {key: "do-not-package-this"}
                with self.assertRaises(ExporterInputError):
                    build_tool_pack(
                        canonical_map, unsafe_plan, targets=("node-red",)
                    )

    def test_pem_like_key_material_fails_closed_even_under_a_generic_field(self) -> None:
        canonical_map, read_plan = inputs()
        unsafe_map = json.loads(json.dumps(canonical_map))
        unsafe_map["notes"] = "-----SENSITIVE KEY-----"
        with self.assertRaises(ExporterInputError):
            build_tool_pack(unsafe_map, read_plan, targets=("modscan",))

    def test_absolute_local_path_values_are_not_packaged(self) -> None:
        canonical_map, read_plan_object = inputs()
        base_plan = read_plan_object.to_dict()
        cases = (
            ("map", "/var/tmp/private-register-map.csv"),
            ("plan", r"D:\\Engineering\\private-register-map.csv"),
            ("options", r"\\\\workstation\\share\\private-map.csv"),
            ("map", "file:///var/tmp/private-register-map.csv"),
        )
        for source, unsafe_value in cases:
            with self.subTest(source=source, value=unsafe_value):
                candidate_map = json.loads(json.dumps(canonical_map))
                candidate_plan = json.loads(json.dumps(base_plan))
                target_options: dict[str, dict[str, object]] = {}
                if source == "map":
                    candidate_map["metadata"] = {"source_file": unsafe_value}
                elif source == "plan":
                    candidate_plan["metadata"] = {"source_file": unsafe_value}
                else:
                    target_options = {
                        "node-red": {"source_file": unsafe_value}
                    }
                with self.assertRaises(ExporterInputError) as caught:
                    build_tool_pack(
                        candidate_map,
                        candidate_plan,
                        targets=("node-red",),
                        target_options=target_options,
                    )
                self.assertIn("absolute local path", str(caught.exception))
                self.assertNotIn(unsafe_value, str(caught.exception))

    def test_final_unresolved_value_holds_every_selected_target(self) -> None:
        unresolved = point(byte_order=None, byte_order_confirmed=False)
        canonical_map, read_plan = inputs(unresolved)
        pack = build_tool_pack(canonical_map, read_plan, targets=SUPPORTED_TARGETS)
        self.assertEqual("held", pack.status)
        self.assertTrue(all(result.status == "held" for result in pack.target_results))
        self.assertFalse(any(path.startswith(("node-red/", "modpoll/", "modscan/")) for path in pack.files()))

    def test_each_modbus_write_function_is_rejected(self) -> None:
        canonical_map, _ = inputs()
        for function_code in (5, 6, 15, 16):
            with self.subTest(function_code=function_code):
                read_plan = {
                    "requests": [
                        {
                            "request_id": "unsafe",
                            "route_id": "default",
                            "unit_id": 1,
                            "area": "holding-register",
                            "function_code": function_code,
                            "start_offset": 100,
                            "quantity": 2,
                            "points": [{"logical_point_id": "pressure"}],
                        }
                    ]
                }
                pack = build_tool_pack(canonical_map, read_plan, targets=SUPPORTED_TARGETS)
                self.assertEqual("held", pack.status)
                self.assertTrue(all(result.status == "held" for result in pack.target_results))

    def test_unit_zero_and_unknown_route_are_held(self) -> None:
        invalid = point(unit_id=0, route_id=None)
        canonical_map = {"points": [invalid]}
        read_plan = {"requests": [{"request_id": "raw", "route_id": None, "unit_id": 0, "area": "holding-register", "function_code": 3, "start_offset": 100, "quantity": 2, "points": [{"logical_point_id": "pressure"}]}]}
        pack = build_tool_pack(canonical_map, read_plan, targets=SUPPORTED_TARGETS, mode="probe")
        self.assertEqual("held", pack.status)

    def test_write_to_preserves_existing_files_by_default(self) -> None:
        canonical_map, read_plan = inputs()
        pack = build_tool_pack(canonical_map, read_plan, targets=("modscan",))
        with tempfile.TemporaryDirectory() as directory:
            pack.write_to(directory)
            with self.assertRaises(FileExistsError):
                pack.write_to(directory)


if __name__ == "__main__":
    unittest.main()
