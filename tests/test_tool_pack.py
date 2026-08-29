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

from modbus_skills.artifacts import (  # noqa: E402
    artifact_envelope,
    assert_artifact_envelope,
    stable_input_hash,
)
from modbus_skills.exporters import (  # noqa: E402
    Artifact,
    ExporterInputError,
    preflight_common,
)
from modbus_skills.read_plan import compile_read_plan  # noqa: E402
from modbus_skills.tool_pack import (  # noqa: E402
    SUPPORTED_TARGETS,
    _find_unsafe_artifact_paths,
    build_tool_pack,
)


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
    canonical_map = {"schema_version": "modbus-map/v1", "points": [selected]}
    read_plan = compile_read_plan([selected])
    return canonical_map, artifact_envelope(
        read_plan.to_dict(),
        schema_version="modbus-read-plan/v1",
        inputs={"canonical_map": canonical_map},
    )


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

    def test_actual_compile_read_plan_object_is_accepted_in_probe_mode(self) -> None:
        canonical_map, _ = inputs()
        read_plan = compile_read_plan(canonical_map["points"])
        pack = build_tool_pack(
            canonical_map,
            read_plan,
            targets=("node-red", "modpoll", "modscan"),
            mode="probe",
        )
        self.assertEqual("generated", pack.status)
        flow = json.loads(pack.files()["node-red/flow.json"])
        reads = [node for node in flow if node["type"] == "modbus-flex-getter"]
        injects = [node for node in flow if node["type"] == "inject"]
        sequencer = next(node for node in flow if node.get("modbusSkillsRole") == "sequencer" and node["type"] == "function")
        self.assertEqual(1, len(reads))
        self.assertEqual(1, len(injects))
        self.assertIn(f'"start_offset":{read_plan.requests[0].start_offset}', sequencer["func"])

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

    def test_additional_zip_artifact_cannot_bypass_export_scan(self) -> None:
        canonical_map, read_plan = inputs()
        pack = build_tool_pack(canonical_map, read_plan, targets=("modscan",))
        unsafe = Artifact.text(
            "result.json",
            "application/json",
            '{"source":"/etc/private/map.csv"}',
            "workflow-result",
        )

        with self.assertRaises(ExporterInputError):
            pack.to_zip_bytes((unsafe,))

    def test_empty_duplicate_and_unknown_target_selections_fail(self) -> None:
        canonical_map, read_plan = inputs()
        cases = ((), ("node-red", "node-red"), ("unknown",))
        for selection in cases:
            with self.subTest(targets=selection), self.assertRaises(ExporterInputError):
                build_tool_pack(canonical_map, read_plan, targets=selection)

    def test_sensitive_map_and_plan_fields_fail_closed(self) -> None:
        canonical_map, read_plan = inputs()
        base_plan = json.loads(json.dumps(read_plan))
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
        canonical_map, read_plan = inputs()
        base_plan = json.loads(json.dumps(read_plan))
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

    def test_pdf_evidence_excerpts_are_not_treated_as_path_fields(self) -> None:
        canonical_map, read_plan = inputs()
        candidate_plan = json.loads(json.dumps(read_plan))
        candidate_plan["holds"] = [
            {
                "code": "pdf-field-evidence-unconfirmed",
                "source": {"excerpt": "/Status word | 40001 | UINT16"},
            }
        ]
        pack = build_tool_pack(canonical_map, candidate_plan, targets=("modscan",))
        self.assertTrue(pack.artifacts)

    def test_oem_slash_names_are_not_treated_as_unix_paths(self) -> None:
        canonical_map, read_plan = inputs()
        candidate_map = json.loads(json.dumps(canonical_map))
        candidate_map["points"][0]["name"] = "Gen L1 lead /lag"
        candidate_map["points"][0]["description"] = "Gen L1 lead /lag"
        pack = build_tool_pack(candidate_map, read_plan, targets=("modpoll",))
        self.assertTrue(pack.artifacts)

    def test_portable_map_excludes_review_audit_and_source_evidence(self) -> None:
        secret = "bearer-eyJhbGciOiJIUzI1NiJ9-private"
        local_path = "/" + "Users/operator/Private/customer-map.csv"
        selected = point(
            review_decisions=[
                {"reason": f"Use {secret} from local file {local_path}"}
            ],
            source_evidence=[{"source_value": f"Local source is {local_path}"}],
            _source={"note": f"Imported from {local_path}"},
        )
        canonical_map = {
            "schema_version": "modbus-map/v1",
            "points": [selected],
            "review_decisions": [
                {"reviewer": secret, "reason": f"Reviewed file {local_path}"}
            ],
            "approval": {"reviewer": secret},
            "source_evidence": [{"note": f"Evidence file is {local_path}"}],
            "holds": [],
        }
        read_plan = artifact_envelope(
            compile_read_plan([selected]).to_dict(),
            schema_version="modbus-read-plan/v1",
            inputs={"canonical_map": canonical_map},
        )

        pack = build_tool_pack(
            canonical_map,
            read_plan,
            targets=("node-red", "modpoll", "modscan"),
        )

        self.assertEqual("generated", pack.status)
        for path, content in pack.files().items():
            with self.subTest(path=path):
                text = content.decode("utf-8", errors="ignore")
                self.assertNotIn(secret, text)
                self.assertNotIn(local_path, text)
        portable_map = json.loads(pack.files()["canonical-map.json"])
        self.assertEqual("modbus-runtime-map/v1", portable_map["schema_version"])
        self.assertEqual("modbus-runtime-map", portable_map["artifact_type"])
        self.assertNotIn("review_decisions", portable_map)
        self.assertNotIn("approval", portable_map)
        self.assertNotIn("source_evidence", portable_map)
        self.assertNotIn("review_decisions", portable_map["points"][0])
        self.assertNotIn("source_evidence", portable_map["points"][0])
        self.assertNotIn("_source", portable_map["points"][0])
        self.assertEqual("pressure", portable_map["points"][0]["logical_point_id"])
        self.assertEqual(
            "Discharge Pressure", portable_map["points"][0]["name"]
        )
        manifest = json.loads(pack.files()["manifest.json"])
        self.assertRegex(manifest["portable_map_hash"], r"^[0-9a-f]{64}$")
        self.assertRegex(
            manifest["portable_read_plan_hash"], r"^[0-9a-f]{64}$"
        )

    def test_portable_read_plan_excludes_unapproved_metadata(self) -> None:
        canonical_map, read_plan = inputs()
        private_values = (
            "Example customer",
            "reviewer@example.invalid",
            "Private register description",
        )
        read_plan["source_evidence"] = [
            {
                "customer": private_values[0],
                "reviewer_email": private_values[1],
                "source_excerpt": private_values[2],
            }
        ]

        pack = build_tool_pack(
            canonical_map,
            read_plan,
            targets=("node-red", "modpoll", "modscan"),
        )

        portable_plan = json.loads(pack.files()["read-plan.json"])
        self.assertEqual("modbus-read-plan/v1", portable_plan["schema_version"])
        self.assertNotIn("source_evidence", portable_plan)
        self.assertEqual(
            pack.read_plan_hash,
            portable_plan["source_read_plan_hash"],
        )
        self.assertEqual(
            pack.map_hash,
            portable_plan["input_hashes"]["canonical_map"],
        )
        for path, content in pack.files().items():
            with self.subTest(path=path):
                text = content.decode("utf-8", errors="ignore")
                for private_value in private_values:
                    self.assertNotIn(private_value, text)

    def test_portable_read_plan_preserves_stale_map_provenance(self) -> None:
        original_map, read_plan = inputs()
        changed_map = json.loads(json.dumps(original_map))
        changed_map["points"][0]["name"] = "Changed display name"

        pack = build_tool_pack(
            changed_map,
            read_plan,
            targets=("modscan",),
            mode="final",
        )
        portable_plan = json.loads(pack.files()["read-plan.json"])
        codes = {
            finding.code
            for finding in preflight_common(
                changed_map,
                portable_plan,
                mode="final",
            )
        }

        self.assertEqual("held", pack.status)
        self.assertEqual(
            read_plan["input_hashes"]["canonical_map"],
            portable_plan["input_hashes"]["canonical_map"],
        )
        self.assertIn("PLAN_MAP_HASH_MISMATCH", codes)

    def test_portable_read_plan_preserves_missing_options_failure(self) -> None:
        canonical_map, read_plan = inputs()
        options = {"max_gap": 0, "max_quantities": {}}
        read_plan["input_hashes"]["planning_options"] = stable_input_hash(
            options
        )

        pack = build_tool_pack(
            canonical_map,
            read_plan,
            targets=("modscan",),
            mode="final",
        )
        portable_plan = json.loads(pack.files()["read-plan.json"])
        codes = {
            finding.code
            for finding in preflight_common(
                canonical_map,
                portable_plan,
                mode="final",
            )
        }

        self.assertEqual("held", pack.status)
        self.assertEqual(
            read_plan["input_hashes"]["planning_options"],
            portable_plan["input_hashes"]["planning_options"],
        )
        self.assertIn("PLAN_OPTIONS_MISSING", codes)

    def test_portable_read_plan_preserves_supported_request_aliases(self) -> None:
        canonical_map, _ = inputs()
        requests = (
            (
                {
                    "block_id": "alias-unit-id",
                    "route": "default",
                    "unitId": 1,
                    "object_type": "holding-register",
                    "function": 3,
                    "start_address": 100,
                    "count": 2,
                    "poll_interval_ms": 2500,
                    "points": ["pressure"],
                },
                {
                    "request_id": "alias-unit-id",
                    "route_id": "default",
                    "unit_id": 1,
                    "area": "holding-register",
                    "function_code": 3,
                    "start_offset": 100,
                    "quantity": 2,
                    "poll_interval_ms": 2500,
                    "points": ["pressure"],
                },
            ),
            (
                {
                    "id": "alias-slave-id",
                    "route": "default",
                    "slave_id": 1,
                    "object_type": "holding-register",
                    "function": 3,
                    "start": 100,
                    "size": 2,
                    "interval_ms": 3000,
                    "point_ids": ["pressure"],
                },
                {
                    "request_id": "alias-slave-id",
                    "route_id": "default",
                    "unit_id": 1,
                    "area": "holding-register",
                    "function_code": 3,
                    "start_offset": 100,
                    "quantity": 2,
                    "poll_interval_ms": 3000,
                    "point_ids": ["pressure"],
                },
            ),
        )
        for request, expected in requests:
            with self.subTest(request=request.get("block_id", request.get("id"))):
                read_plan = {"requests": [request]}
                pack = build_tool_pack(
                    canonical_map,
                    read_plan,
                    targets=("modscan",),
                    mode="probe",
                )
                projected = json.loads(pack.files()["read-plan.json"])[
                    "requests"
                ][0]

                self.assertEqual("generated", pack.status)
                self.assertEqual(expected, projected)

    def test_portable_point_ids_use_adapter_id_resolution(self) -> None:
        canonical_map, _ = inputs()
        request = {
            "request_id": "structured-point-ids",
            "route_id": "default",
            "unit_id": 1,
            "area": "holding-register",
            "function_code": 3,
            "start_offset": 100,
            "quantity": 2,
            "point_ids": [
                {
                    "logical_point_id": "pressure",
                    "protocol_offset": 100,
                    "span": 2,
                },
                None,
                "unknown",
                "",
            ],
        }

        pack = build_tool_pack(
            canonical_map,
            {"requests": [request]},
            targets=("modscan",),
            mode="probe",
        )
        portable_plan = json.loads(pack.files()["read-plan.json"])
        projected = portable_plan["requests"][0]

        self.assertEqual("generated", pack.status)
        self.assertEqual(
            [
                {
                    "logical_point_id": "pressure",
                    "protocol_offset": 100,
                    "span": 2,
                }
            ],
            projected["point_ids"],
        )
        self.assertFalse(preflight_common(canonical_map, portable_plan, mode="probe"))

    def test_portable_point_ids_preserve_trace_validation_failures(self) -> None:
        canonical_map, read_plan = inputs()
        request = read_plan["requests"][0]
        request.pop("points")
        request["point_ids"] = [
            {
                "logical_point_id": "pressure",
                "protocol_offset": 99,
                "span": 2,
                "relative_offset": 99,
            }
        ]
        source_codes = {
            finding.code
            for finding in preflight_common(canonical_map, read_plan, mode="final")
        }

        pack = build_tool_pack(
            canonical_map,
            read_plan,
            targets=("modscan",),
            mode="final",
        )
        portable_plan = json.loads(pack.files()["read-plan.json"])
        portable_codes = {
            finding.code
            for finding in preflight_common(
                canonical_map,
                portable_plan,
                mode="final",
            )
        }

        self.assertEqual("held", pack.status)
        self.assertIn("BLOCK_POINT_TRACE_MISMATCH", source_codes)
        self.assertIn("BLOCK_POINT_TRACE_MISMATCH", portable_codes)

    def test_portable_read_plan_drops_shadowed_alias_values(self) -> None:
        canonical_map, _ = inputs()
        private_text = "ACME Confidential Program Falcon"
        read_plan = {
            "requests": [
                {
                    "request_id": "read-0001",
                    "id": private_text,
                    "route_id": "default",
                    "route": private_text,
                    "unit_id": 1,
                    "slave_id": 247,
                    "area": "holding-register",
                    "object_type": private_text,
                    "function_code": 3,
                    "function": 4,
                    "start_offset": 100,
                    "start": 60000,
                    "quantity": 2,
                    "size": 125,
                    "points": [
                        {
                            "point_id": "pressure",
                            "id": private_text,
                        }
                    ],
                }
            ]
        }

        pack = build_tool_pack(
            canonical_map,
            read_plan,
            targets=("modscan",),
            mode="probe",
        )

        self.assertEqual("generated", pack.status)
        for path, content in pack.files().items():
            with self.subTest(path=path):
                self.assertNotIn(
                    private_text,
                    content.decode("utf-8", errors="ignore"),
                )

    def test_target_visible_secret_and_embedded_path_values_fail_closed(self) -> None:
        cases = (
            "Pressure from /" + "Users/operator/Private/customer-map.csv",
            "Pressure from /var/tmp/customer-map.csv",
            "Pressure from /etc/scada/customer-map.csv",
            "Pressure from /opt/company/customer-map.csv",
            "Pressure from /srv/maps/customer-map.csv",
            "Pressure from /mnt/share/customer-map.csv",
            "Pressure from //workstation/share/customer-map.csv",
            "Pressure source `/etc/scada/customer-map.csv`",
            "postgresql://engineer:correct-horse@db.invalid/scada",
            "Bearer eyJhbGciOiJIUzI1NiJ9.customer.signature",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJlbmdpbmVlciJ9.ZmFrZXNpZ25hdHVyZQ",
            "AKIAIOSFODNN7EXAMPLE",
            "github" + "_pat_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
            "s" + "k-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
        )
        for unsafe_name in cases:
            with self.subTest(value=unsafe_name):
                canonical_map, read_plan = inputs(point(name=unsafe_name))
                with self.assertRaises(ExporterInputError) as caught:
                    build_tool_pack(
                        canonical_map,
                        read_plan,
                        targets=("node-red", "modpoll", "modscan"),
                    )
                self.assertNotIn(unsafe_name, str(caught.exception))

    def test_final_artifact_scan_detects_generated_unsafe_text(self) -> None:
        unsafe = Artifact.text(
            "target/generated.txt",
            "text/plain",
            "Connect with redis://operator:private-value@host.invalid/0",
            "generated-target-output",
        )

        self.assertEqual(
            ["target/generated.txt"],
            _find_unsafe_artifact_paths((unsafe,)),
        )

    def test_dashboard_route_exception_does_not_hide_other_absolute_paths(self) -> None:
        dashboard = Artifact.text(
            "target/dashboard.json",
            "application/json",
            '{"url":"/modbus-dashboard"}',
            "generated-target-output",
        )
        unsafe_path = Artifact.text(
            "target/unsafe.json",
            "application/json",
            '{"path":"/etc/private-map.json"}',
            "generated-target-output",
        )

        self.assertEqual([], _find_unsafe_artifact_paths((dashboard,)))
        self.assertEqual(
            ["target/unsafe.json"],
            _find_unsafe_artifact_paths((unsafe_path,)),
        )

    def test_final_unresolved_value_holds_every_selected_target(self) -> None:
        unresolved = point(byte_order=None, byte_order_confirmed=False)
        canonical_map, read_plan = inputs(unresolved)
        pack = build_tool_pack(canonical_map, read_plan, targets=SUPPORTED_TARGETS)
        self.assertEqual("held", pack.status)
        self.assertTrue(all(result.status == "held" for result in pack.target_results))
        self.assertFalse(any(path.startswith(("node-red/", "modpoll/", "modscan/")) for path in pack.files()))
        manifest = json.loads(pack.files()["manifest.json"])
        self.assertEqual(1, len(manifest["holds"]))
        self.assertEqual(list(SUPPORTED_TARGETS), manifest["holds"][0]["targets"])

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
