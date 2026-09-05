"""Independent protocol/UI expectations from the bounded native entry checks."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.artifacts import artifact_envelope  # noqa: E402
from modbus_skills.modscan import export_modscan  # noqa: E402
from modbus_skills.read_plan import compile_read_plan  # noqa: E402


class ModscanAddressEntryTests(unittest.TestCase):
    def export(self, offset, mode="final"):
        point = {"logical_point_id": "synthetic", "name": "Synthetic",
                 "route_id": "loop", "unit_id": 7, "area": "holding-register",
                 "protocol_offset": offset, "datatype": "uint16", "word_span": 1,
                 "access": "read-only", "normalization_status": "confirmed"}
        canonical = {"schema_version": "modbus-map/v1", "points": [point]}
        plan = artifact_envelope(compile_read_plan([point]).to_dict(), schema_version="modbus-read-plan/v1",
                                 inputs={"canonical_map": canonical})
        return export_modscan(canonical, plan, mode=mode)

    def test_ui_address_does_not_change_protocol_messages_or_canonical_offset(self):
        for offset, ui, pdu in ((0, 1, "03 00 00 00 01"), (65534, 65535, "03 FF FE 00 01")):
            with self.subTest(offset=offset):
                result = self.export(offset)
                self.assertEqual("generated", result.status)
                files = {item.path: item.as_text() for item in result.artifacts}
                row, = csv.DictReader(io.StringIO(files["modscan/read-plan.csv"]))
                self.assertEqual(str(offset), row["protocol_offset_base_0"])
                self.assertEqual(str(ui), row["modscan_point_address_base_1"])
                self.assertEqual(str(40001 + offset), row["common_reference_base_1"])
                message, = csv.DictReader(io.StringIO(files["modscan/test-message-plan.csv"]))
                self.assertEqual(pdu, message["request_pdu_hex"])
                setup = json.loads(files["modscan/setup-manifest.json"])
                self.assertEqual(0, setup["protocol_address_base"])
                self.assertEqual(1, setup["point_address_base"])
                self.assertEqual("not-run", setup["native_verification"]["status"])

    def test_unrepresentable_ui_start_is_held_in_both_modes_without_runnable_files(self):
        for mode in ("probe", "final"):
            with self.subTest(mode=mode):
                result = self.export(65535, mode)
                self.assertEqual("held", result.status)
                self.assertIn("MODSCAN_POINT_ADDRESS_UNSUPPORTED", {finding.code for finding in result.findings})
                self.assertFalse(result.artifacts)


if __name__ == "__main__":
    unittest.main()
