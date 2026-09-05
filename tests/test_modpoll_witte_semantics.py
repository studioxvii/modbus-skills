from __future__ import annotations

from pathlib import Path
import json
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.artifacts import artifact_envelope  # noqa: E402
from modbus_skills.modpoll import export_modpoll, validate_witte_v12_xml  # noqa: E402
from modbus_skills.read_plan import compile_read_plan  # noqa: E402


def inputs(function=3, quantity=1, **updates):
    areas = {1: "coil", 2: "discrete-input", 3: "holding-register", 4: "input-register"}
    points = [{"logical_point_id": f"sample-{index}", "name": f"Sample {index}",
               "route_id": "synthetic", "unit_id": 7, "area": areas[function],
               "function_code": function, "protocol_offset": 16+index,
               "datatype": "bool" if function < 3 else "uint16", "word_span": 1,
               "access": "read-only", "normalization_status": "confirmed", **updates}
              for index in range(quantity)]
    canonical = {"schema_version": "modbus-map/v1", "points": points}
    plan = artifact_envelope(compile_read_plan(points).to_dict(), schema_version="modbus-read-plan/v1", inputs={"canonical_map": canonical})
    return canonical, plan


def xml_document(function, quantity):
    canonical, plan = inputs(function, quantity)
    result = export_modpoll(canonical, plan, profile="witte-v12-xml", mode="probe")
    assert result.status == "generated", result.findings
    return next(artifact.as_text() for artifact in result.artifacts if artifact.path.endswith(".mbp"))


class WitteXmlStorageTests(unittest.TestCase):
    def test_stored_byte_count_depends_on_area_not_wire_packing(self):
        for function in (1, 2, 3, 4):
            for quantity in (1, 9):
                with self.subTest(function=function, quantity=quantity):
                    text = xml_document(function, quantity)
                    root = ET.fromstring(text)
                    expected = quantity if function < 3 else quantity*2
                    self.assertEqual(expected, len(root.findall("Data/Bytes/B")))
                    self.assertEqual(quantity, len(root.findall("Data/Formats/F")))
                    self.assertEqual("0", root.findtext("Enable"))
                    self.assertEqual("0", root.findtext("OneBased"))
                    self.assertEqual((), validate_witte_v12_xml(text))

    def test_bit_storage_rejects_register_sized_and_wire_packed_counts(self):
        for function in (1, 2):
            for count in (2, 18):
                with self.subTest(function=function, count=count):
                    root = ET.fromstring(xml_document(function, 9))
                    stored = root.find("Data/Bytes")
                    stored.clear()
                    for _ in range(count):
                        ET.SubElement(stored, "B").text = "0"
                    codes = {finding.code for finding in validate_witte_v12_xml(ET.tostring(root, encoding="unicode"))}
                    self.assertIn("WITTE_XML_BYTES_COUNT_MISMATCH", codes)

    def test_register_storage_rejects_one_byte_per_register(self):
        for function in (3, 4):
            root = ET.fromstring(xml_document(function, 9))
            stored = root.find("Data/Bytes")
            for child in list(stored)[9:]:
                stored.remove(child)
            codes = {finding.code for finding in validate_witte_v12_xml(ET.tostring(root, encoding="unicode"))}
            self.assertIn("WITTE_XML_BYTES_COUNT_MISMATCH", codes)


class WitteNumericSemanticsTests(unittest.TestCase):
    profiles = ("witte-desktop", "witte-v12-xml")

    def test_scaled_offset_float_all_layouts_are_held_not_silently_raw(self):
        for profile in self.profiles:
            for layout in ("ABCD", "BADC", "CDAB", "DCBA"):
                with self.subTest(profile=profile, layout=layout):
                    canonical, plan = inputs(datatype="float32", word_span=2,
                                             byte_order=layout, byte_order_confirmed=True,
                                             scale=2.5, engineering_offset=-3)
                    result = export_modpoll(canonical, plan, profile=profile, mode="final")
                    self.assertEqual("held", result.status)
                    self.assertFalse(result.artifacts)
                    codes = {f.code for f in result.findings}
                    self.assertTrue({"WITTE_FINAL_DISPLAY_UNCONFIGURED", "WITTE_SCALE_UNSUPPORTED", "WITTE_OFFSET_UNSUPPORTED"} <= codes)
                    self.assertEqual(layout != "ABCD", "WITTE_BYTE_ORDER_UNCONFIGURED" in codes)

    def test_other_unconfigured_final_datatypes_remain_held(self):
        for profile in self.profiles:
            for function, datatype, width in ((1, "bool", 1), (2, "bool", 1),
                    (3, "int16", 1), (4, "int32", 2), (3, "uint32", 2),
                    (3, "float64", 4), (3, "string", 2), (3, "bitfield", 1), (3, "bool", 1)):
                with self.subTest(profile=profile, datatype=datatype, function=function):
                    canonical, plan = inputs(function, datatype=datatype, word_span=width,
                                             byte_order="ABCD", byte_order_confirmed=True)
                    result = export_modpoll(canonical, plan, profile=profile, mode="final")
                    self.assertEqual("held", result.status)
                    self.assertIn("WITTE_FINAL_DISPLAY_UNCONFIGURED", {f.code for f in result.findings})

    def test_uint16_transforms_and_swapped_bytes_stay_held(self):
        for updates, code in (({"scale": 0}, "WITTE_SCALE_UNSUPPORTED"),
                ({"scale": "2.5"}, "WITTE_SCALE_UNSUPPORTED"),
                ({"engineering_offset": .5}, "WITTE_OFFSET_UNSUPPORTED"),
                ({"offset": -1}, "WITTE_OFFSET_UNSUPPORTED"),
                ({"byte_order": "BA"}, "WITTE_BYTE_ORDER_UNCONFIGURED")):
            with self.subTest(updates=updates):
                canonical, plan = inputs(**updates)
                result = export_modpoll(canonical, plan, profile="witte-desktop", mode="final")
                self.assertEqual("held", result.status)
                self.assertIn(code, {f.code for f in result.findings})

    def test_desktop_identity_uint16_sets_document_relative_unsigned_before_connect(self):
        for function in (3, 4):
            canonical, plan = inputs(function, quantity=3, scale="1.00", engineering_offset="0e0")
            result = export_modpoll(canonical, plan, profile="witte-desktop", mode="final")
            self.assertEqual("generated", result.status, result.findings)
            script = next(a.as_text() for a in result.artifacts if a.path.endswith(".ps1"))
            self.assertEqual(3, script.count(".SetFormat("))
            for index in range(3):
                call = f"$document1.SetFormat({index}, 1)"
                self.assertIn(call, script)
                self.assertLess(script.index("$document1.ReadWriteDisabled = $true"), script.index(call))
                self.assertLess(script.index(call), script.index("$document1.Save("))
                self.assertLess(script.index(call), script.index("OpenConnection()"))
            self.assertNotIn("SetFormat(16,", script)
            setup = json.loads(next(a.as_text() for a in result.artifacts if a.path.endswith("setup-manifest.json")))
            self.assertEqual("identity-uint16", setup["numeric_representation"]["kind"])
            self.assertEqual("not-run", setup["native_verification"]["status"])
            xml = export_modpoll(canonical, plan, profile="witte-v12-xml", mode="final")
            self.assertEqual("held", xml.status)
            self.assertNotIn("WITTE_SCALE_UNSUPPORTED", {f.code for f in xml.findings})
            self.assertNotIn("WITTE_OFFSET_UNSUPPORTED", {f.code for f in xml.findings})

    def test_probe_ignores_engineering_decode_but_labels_artifacts_explicitly_raw(self):
        for profile in self.profiles:
            canonical, plan = inputs(datatype="float32", word_span=2, byte_order="DCBA",
                                     byte_order_confirmed=True, scale=2.5, engineering_offset=-3)
            result = export_modpoll(canonical, plan, profile=profile, mode="probe")
            self.assertEqual("generated", result.status, result.findings)
            for artifact in result.artifacts:
                if artifact.path.endswith("manifest.json"):
                    representation = json.loads(artifact.as_text())["numeric_representation"]
                    self.assertEqual("raw-bits-or-register-words", representation["kind"])
                    self.assertFalse(representation["datatype_decoding_configured"])
                if artifact.path.endswith(".ps1"):
                    self.assertNotIn("SetFormat", artifact.as_text())
            readme = next(a.as_text() for a in result.artifacts if a.path.endswith("README.md"))
            self.assertIn("Raw Probe", readme)
            self.assertIn("raw bits/register words only", readme)
            self.assertIn("engineering", readme)


if __name__ == "__main__":
    unittest.main()
