from __future__ import annotations

import io
import sys
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))

from modbus_skills.parsers import (  # noqa: E402
    ParseError,
    parse_csv,
    parse_json,
    parse_source,
    parse_xlsx,
    parse_xml,
)


FIXTURES = ROOT / "tests" / "fixtures" / "maps"


def make_xlsx() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0"?>
            <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets><sheet name="Map" sheetId="1" r:id="rId1"/></sheets>
            </workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Target="worksheets/sheet1.xml"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>
            </Relationships>""",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            """<?xml version="1.0"?>
            <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <si><t>Address</t></si><si><t>Area</t></si><si><t>Data Type</t></si>
              <si><t>holding-register</t></si><si><t>uint16</t></si>
            </sst>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0"?>
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>
                <row r="2"><c r="A2"><v>0</v></c><c r="B2" t="s"><v>3</v></c><c r="C2" t="s"><v>4</v></c></row>
                <row r="3"><c r="A3"><f>1+1</f><v>2</v></c><c r="B3" t="inlineStr"><is><t>input-register</t></is></c><c r="C3" t="inlineStr"><is><t>uint16</t></is></c></row>
              </sheetData>
            </worksheet>""",
        )
    return stream.getvalue()


class CsvParserTests(unittest.TestCase):
    def test_delimiter_and_multiline_quoted_field(self) -> None:
        result = parse_csv((FIXTURES / "synthetic_registers.csv").read_bytes())
        self.assertEqual(3, len(result["records"]))
        self.assertEqual(";", result["assumptions"][0]["value"])
        self.assertIn("multiline description", result["records"][1]["description"])
        self.assertEqual("protocol-offset", result["records"][0]["address_convention"])

    def test_unknown_engineering_values_are_preserved_not_defaulted(self) -> None:
        result = parse_csv(
            "Address,Area,Data Type,Byte Order\n1,Mystery,VendorFloat,ZYXW\n",
            delimiter=",",
        )
        record = result["records"][0]
        self.assertEqual("Mystery", record["area"])
        self.assertEqual("VendorFloat", record["datatype"])
        self.assertEqual("ZYXW", record["byte_order"])
        self.assertEqual(
            {"unrecognized_area", "unrecognized_datatype", "unrecognized_byte_order"},
            {warning["code"] for warning in result["warnings"]},
        )

    def test_missing_address_is_rejected_with_source_record(self) -> None:
        result = parse_csv("Name,Data Type\nNo Address,uint16\n", delimiter=",")
        self.assertEqual([], result["records"])
        self.assertEqual("missing_address", result["rejected_rows"][0]["code"])

    def test_quoted_delimiters_do_not_split_columns(self) -> None:
        result = parse_csv('Address,Name,Description\n0,"A,B","quoted, value"\n', delimiter=",")
        self.assertEqual("A,B", result["records"][0]["name"])
        self.assertEqual("quoted, value", result["records"][0]["description"])

    def test_function_code_header_alias_is_preserved(self) -> None:
        result = parse_csv("Address,FC\n0,3\n", delimiter=",")
        self.assertEqual("3", result["records"][0]["function_code"])


class JsonAndXmlParserTests(unittest.TestCase):
    def test_json_registers_and_nested_data_collections(self) -> None:
        fixture = parse_json((FIXTURES / "synthetic_registers.json").read_bytes())
        nested = parse_json({"data": {"registers": [{"address": 1}, "bad"]}})
        self.assertEqual(2, len(fixture["records"]))
        self.assertEqual(1, len(nested["records"]))
        self.assertEqual("record_not_object", nested["rejected_rows"][0]["code"])
        self.assertEqual("data.registers", nested["assumptions"][0]["value"])

    def test_json_requires_documented_collection_shape(self) -> None:
        with self.assertRaises(ParseError):
            parse_json('{"items": []}')

    def test_xml_normalizes_camel_case_element_names(self) -> None:
        result = parse_xml((FIXTURES / "synthetic_registers.xml").read_bytes())
        record = result["records"][0]
        self.assertEqual("modicon-reference", record["address_convention"])
        self.assertEqual("line_voltage", record["logical_point_id"])
        self.assertEqual("uint32", record["datatype"])

    def test_xml_rejects_dtd_and_entity_declarations(self) -> None:
        unsafe = """<!DOCTYPE map [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
        <map><register><address>&xxe;</address></register></map>"""
        with self.assertRaises(ParseError):
            parse_xml(unsafe)


class XlsxParserTests(unittest.TestCase):
    def test_basic_xlsx_types_and_formula_cached_values(self) -> None:
        result = parse_xlsx(make_xlsx())
        self.assertEqual(2, len(result["records"]))
        self.assertEqual(0, result["records"][0]["address"])
        self.assertEqual(2, result["records"][1]["address"])
        self.assertTrue(any(warning["code"] == "formula_cached_value" for warning in result["warnings"]))
        self.assertEqual("Map", result["records"][0]["_source"]["sheet"])

    def test_invalid_archive_is_rejected(self) -> None:
        with self.assertRaises(ParseError):
            parse_xlsx(b"not a workbook")

    def test_dispatch_uses_filename_extension(self) -> None:
        result = parse_source("Address\tArea\n0\tholding-register\n", filename="map.tsv")
        self.assertEqual("holding-register", result["records"][0]["area"])


if __name__ == "__main__":
    unittest.main()
