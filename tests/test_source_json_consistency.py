from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))

from modbus_skills.compiler import compile_user_map  # noqa: E402
from modbus_skills.map_workflows import normalize_map  # noqa: E402
from modbus_skills.parsers import parse_source  # noqa: E402
from modbus_skills.source_intake import (  # noqa: E402
    SourceIntakeError, _parse_structured_source, compile_source_descriptor,
)


class SourceJsonConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def source(self, document):
        path = self.root / "synthetic.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def row(self, **overrides):
        return {"point_id": "synthetic-point", "name": "Synthetic Point",
                "protocol_offset": 10, "area": "holding-register", "datatype": "uint16", **overrides}

    def coverage(self):
        return {"status": "unknown", "accepted_row_count": 1, "rejected_row_count": 0,
                "quarantined_row_count": 0, "detected_pages": [], "detected_regions": [], "discovery_complete": False}

    def compile(self, document):
        source = self.source(document)
        request = {"schema_version": "modbus-compile-request/v1", "source": {"path": str(source)},
                   "selection_template": {"schema_version": "modbus-user-selection-template/v1",
                                          "requested_measurements": ["all documented Modbus read points"], "mode": "all-readable"},
                   "targets": [], "target_options": {}}
        return compile_user_map(request, self.root / "case")

    def test_raw_collections_match_parse_map_aliases_and_preserve_ids(self):
        for wrapper in (None, "records", "points", "registers", "data"):
            with self.subTest(wrapper=wrapper):
                row = {"oem_point_id": "explicit-source-id", "Name": "Synthetic Temperature",
                       "protocol_offset": 12, "area": "holding-register", "Datatype": "uint16", "R/W": "R",
                       "Engineering Offset": -2.5}
                document = {wrapper: [row]} if wrapper else [row]
                source = self.source(document)
                parsed = parse_source(source.read_bytes(), source_format="json", filename=source.name)
                expected = normalize_map(parsed)["points"][0]
                oem, _ = compile_source_descriptor({"path": str(source)})
                actual = oem["points"][0]
                for field in ("name", "datatype", "access", "protocol_offset", "engineering_offset", "function_code"):
                    self.assertEqual(expected[field], actual[field], field)
                self.assertEqual("explicit-source-id", actual["oem_point_id"])
                self.assertEqual([{ "record_id": "json:0"}], actual["source_refs"])

    def test_mixed_aliases_compile_only_the_readable_member(self):
        readable = {"point_id": "readable", "Name": "Synthetic Readback", "protocol_offset": 0,
                    "area": "holding-register", "Datatype": "uint16", "R/W": "R"}
        write = self.row(point_id="command", protocol_offset=1, **{"R/W": "W"})
        result = self.compile({"records": [readable, write]})
        self.assertEqual("offline-complete", result["state"])
        output = json.loads((self.root / "case/output/user-map.json").read_text())
        self.assertEqual(["readable"], [point["oem_point_id"] for point in output["points"]])
        selection = json.loads((self.root / "case/artifacts/selection.json").read_text())
        self.assertEqual(["command"], [point["oem_point_id"] for point in selection["excluded"]])

    def test_write_only_alias_never_becomes_an_offline_complete_read_map(self):
        result = self.compile({"records": [self.row(**{"R/W": "W"})]})
        self.assertNotEqual("offline-complete", result["state"])
        self.assertFalse((self.root / "case/output/user-map.json").exists())
        oem = json.loads((self.root / "case/artifacts/oem-map.json").read_text())
        self.assertEqual("write-only", oem["points"][0]["access"])
        self.assertTrue(any(item["code"] == "point.write-only-not-readable" for item in oem["holds"]))

    def test_fc06_alias_is_preserved_and_held_not_replaced_with_fc03(self):
        result = self.compile({"points": [self.row(FC="06")]})
        self.assertEqual("partial", result["state"])
        output = json.loads((self.root / "case/output/user-map.json").read_text())
        self.assertEqual(6, output["points"][0]["function_code"])
        self.assertTrue(output["holds"])

    def test_typed_candidate_preserves_metadata_claims_and_engineering_offset(self):
        location = {"format": "csv", "row": 9}
        row = self.row(access="read-only", offset=-2.5, _source=location,
                       _claims=[{"field": "address", "raw_header": "PDU Offset", "raw_value": "10", "source_locator": location}])
        document = {"schema_version": "candidate-map/v1", "format": "csv", "records": [row],
                    "source_coverage": self.coverage(), "assumptions": [{"code": "synthetic-source-assumption"}],
                    "source_findings": [{"code": "synthetic-source-finding"}], "warnings": [], "rejected_rows": [],
                    "input_hashes": {"source": "a" * 64}}
        parsed = _parse_structured_source(json.dumps(document).encode(), source_format="json", filename="candidate.json", delimiter=None)
        self.assertEqual(document, parsed)
        oem, _ = compile_source_descriptor({"path": str(self.source(document))})
        self.assertEqual(-2.5, oem["points"][0]["engineering_offset"])
        self.assertEqual([{"record_id": "csv:9"}], oem["points"][0]["source_refs"])
        self.assertEqual(document["source_coverage"], oem["source_coverage"])
        self.assertIn(document["assumptions"][0], oem["assumptions"])

    def test_typed_canonical_restores_original_row_and_field_provenance(self):
        location = {"format": "csv", "row": 9}
        claims = [{"field": "protocol_offset", "raw_header": "PDU Offset", "raw_value": "10", "source_locator": location}]
        row = self.row(access="read-only", engineering_offset=-2.5, offset=-2.5,
                       source_location=location, source_claims=claims)
        document = {"schema_version": "modbus-map/v1", "points": [row], "source_coverage": self.coverage()}
        original = copy.deepcopy(document)
        parsed = _parse_structured_source(json.dumps(document).encode(), source_format="json", filename="canonical.json", delimiter=None)
        self.assertEqual(location, parsed["points"][0]["_source"])
        self.assertEqual(claims, parsed["points"][0]["_claims"])
        self.assertEqual(original, document)
        oem, _ = compile_source_descriptor({"path": str(self.source(document))})
        point = oem["points"][0]
        self.assertEqual("synthetic-point", point["oem_point_id"])
        self.assertEqual(10, point["protocol_offset"])
        self.assertEqual(-2.5, point["engineering_offset"])
        self.assertEqual([{"record_id": "csv:9"}], point["source_refs"])
        address = next(item for item in point["source_field_evidence"] if item["field"] == "protocol_offset")
        self.assertEqual("10", address["raw_value"])
        self.assertEqual("PDU Offset", address["raw_header"])
        self.assertEqual("csv:9", address["source_ref"])

    def test_malformed_raw_members_are_rejected_with_a_source_hold(self):
        for wrapper in ("records", "points"):
            with self.subTest(wrapper=wrapper):
                source = self.source({wrapper: [self.row(), None, 7, ["not a record"], {"Name": "No Address"}]})
                parsed = parse_source(source.read_bytes(), source_format="json", filename=source.name)
                self.assertEqual(4, len(parsed["rejected_rows"]))
                oem, _ = compile_source_descriptor({"path": str(source)})
                self.assertEqual(1, len(oem["points"]))
                rejection = next(item for item in oem["holds"] if item["code"] == "source.rejected-rows-unresolved")
                self.assertEqual(4, rejection["affected_count"])

    def test_malformed_arrays_and_typed_envelopes_raise_source_errors(self):
        documents = [
            {"records": "not an array"}, {"points": {}},
            {"schema_version": "candidate-map/v1", "records": None},
            {"schema_version": "candidate-map/v1", "records": [None]},
            {"schema_version": "modbus-map/v1", "points": [7]},
            {"schema_version": "modbus-map/v1", "records": [self.row()], "points": [self.row()]},
            {"schema_version": "modbus-map/v1", "points": [self.row()], "holds": {}},
            {"schema_version": "modbus-map/v1", "points": [self.row(source_location="not a locator")]},
            {"schema_version": "modbus-map/v1", "points": [self.row(source_claims=[None])]},
            {"schema_version": "unknown-map/v1", "points": [self.row()]},
        ]
        for document in documents:
            with self.subTest(document=document):
                with self.assertRaises(SourceIntakeError):
                    compile_source_descriptor({"path": str(self.source(document))})


if __name__ == "__main__":
    unittest.main()
