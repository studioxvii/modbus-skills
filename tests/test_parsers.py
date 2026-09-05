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

    def test_modbus_address_read_header_is_recognized_as_address(self) -> None:
        # ComAp/Entergy-style register maps label their address column
        # "Modbus Address Read" rather than a plain "Address"/"Register".
        result = parse_csv(
            "Parameter Name,Modbus Address Read\nCharging status,40005\n",
            delimiter=",",
        )
        self.assertEqual(1, len(result["records"]))
        self.assertEqual(40005, int(result["records"][0]["address"]))

    def test_modbus_data_type_and_function_code_headers_are_recognized(self) -> None:
        # ASCO-style register maps label their columns "Modbus Data Type"
        # and "Modbus Function Code" rather than "Data Type"/"Function Code".
        result = parse_csv(
            "Name,Modbus Address Read,Modbus Data Type,Modbus Function Code\n"
            "Year,1836,UINT16,3\n",
            delimiter=",",
        )
        self.assertEqual(1, len(result["records"]))
        record = result["records"][0]
        self.assertEqual("UINT16", record["datatype"])
        self.assertEqual(3, int(record["function_code"]))

    def test_common_underscore_enum_names_do_not_create_false_warnings(self) -> None:
        result = parse_csv(
            "Address,Area,Data Type,Byte Order\n"
            "1,input_register,FLOAT32,BIG_ENDIAN\n"
            "3,holding_register,INT32,LITTLE_ENDIAN_BYTE_SWAP\n",
            delimiter=",",
        )

        self.assertEqual([], result["warnings"])

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


def make_xlsx_with_title_row() -> bytes:
    """Build an XLSX where a merged, single-cell title sits above the real header.

    Mirrors vendor workbooks (e.g. OEM register lists) that put a worksheet
    title in row 1 and the actual column headers in row 2.
    """

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
              <si><t>Vendor Register List - Title</t></si>
              <si><t>Parameter Name</t></si><si><t>Holding Register</t></si>
              <si><t>Voltage</t></si>
            </sst>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0"?>
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1"><c r="A1" t="s"><v>0</v></c></row>
                <row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2" t="s"><v>2</v></c></row>
                <row r="3"><c r="A3" t="s"><v>3</v></c><c r="B3"><v>100</v></c></row>
              </sheetData>
            </worksheet>""",
        )
    return stream.getvalue()


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

    def test_single_cell_title_row_above_header_is_skipped(self) -> None:
        result = parse_xlsx(make_xlsx_with_title_row())
        self.assertEqual(1, len(result["records"]))
        record = result["records"][0]
        self.assertEqual("Voltage", record["name"])
        self.assertEqual(100, record["address"])
        skipped = [a for a in result["assumptions"] if a["code"] == "skipped_title_row"]
        self.assertEqual(1, len(skipped))
        self.assertEqual([1], skipped[0]["rows"])
        header = [a for a in result["assumptions"] if a["code"] == "xlsx_header_row"][0]
        self.assertEqual(2, header["row"])

    def test_holding_register_header_alias_maps_to_address(self) -> None:
        result = parse_source(
            "Parameter Name,Holding Register\nGenerator Voltage,100\n",
            filename="map.csv",
        )
        self.assertEqual(100, int(result["records"][0]["address"]))

    def test_dispatch_uses_filename_extension(self) -> None:
        result = parse_source("Address\tArea\n0\tholding-register\n", filename="map.tsv")
        self.assertEqual("holding-register", result["records"][0]["area"])

    def test_unit_header_maps_to_engineering_unit_not_slave_id(self) -> None:
        result = parse_csv(
            "Address,Area,Type,Unit\n256,holding-register,uint16,V\n",
            delimiter=",",
        )
        record = result["records"][0]
        self.assertEqual("V", record["engineering_unit"])
        self.assertNotIn("unit_id", record)

    def test_gotion_integrator_xlsx_skips_cover_and_alarm_sheets(self) -> None:
        path = ROOT / "tests" / "fixtures" / "oem-corpus" / "synthetic" / "gotion-bess-integrator-messy.xlsx"
        result = parse_xlsx(path.read_bytes())
        self.assertEqual(9, len(result["records"]))
        self.assertEqual(0, len(result["rejected_rows"]))
        self.assertTrue(
            any(warning["code"] == "skipped_non_register_worksheet" for warning in result["warnings"])
        )
        self.assertEqual("V", result["records"][0]["engineering_unit"])
        self.assertEqual("4x Holding", result["records"][0]["area"])

    def test_side_by_side_duplicate_title_row_is_skipped(self) -> None:
        # Some vendor sheets (e.g. ASCO PM8000 register lists) lay out two
        # side-by-side table blocks that repeat the worksheet title in more
        # than one cell of row 1 ("PowerLogic PM8000 Power Quality Meter" in
        # both column A and column I). That row must still be treated as a
        # title, not a multi-column header, or every data row is rejected for
        # missing an address.
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "xl/workbook.xml",
                """<?xml version="1.0"?>
                <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
                  <sheets><sheet name="PM8000" sheetId="1" r:id="rId1"/></sheets>
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
                "xl/worksheets/sheet1.xml",
                """<?xml version="1.0"?>
                <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                  <sheetData>
                    <row r="1">
                      <c r="A1" t="inlineStr"><is><t>PowerLogic PM8000 Power Quality Meter</t></is></c>
                      <c r="I1" t="inlineStr"><is><t>PowerLogic PM8000 Power Quality Meter</t></is></c>
                    </row>
                    <row r="2">
                      <c r="A2" t="inlineStr"><is><t>Parameter Name</t></is></c>
                      <c r="B2" t="inlineStr"><is><t>Modbus Address Read</t></is></c>
                      <c r="C2" t="inlineStr"><is><t>Modbus Data Type</t></is></c>
                    </row>
                    <row r="3">
                      <c r="A3" t="inlineStr"><is><t>Year</t></is></c>
                      <c r="B3"><v>1836</v></c>
                      <c r="C3" t="inlineStr"><is><t>UINT16</t></is></c>
                    </row>
                  </sheetData>
                </worksheet>""",
            )
        result = parse_xlsx(stream.getvalue())
        self.assertEqual(1, len(result["records"]))
        record = result["records"][0]
        self.assertEqual("Year", record["name"])
        self.assertEqual(1836, record["address"])
        skipped = [a for a in result["assumptions"] if a["code"] == "skipped_title_row"]
        self.assertEqual([1], skipped[0]["rows"])

    def test_modbus_address_read_header_alias_maps_to_address(self) -> None:
        result = parse_source(
            "Parameter Name,Modbus Address Read\nYear,1836\n",
            filename="map.csv",
        )
        self.assertEqual(1836, int(result["records"][0]["address"]))
        self.assertEqual("Year", result["records"][0]["name"])

    def test_non_worksheet_relationships_may_target_outside_xl_directory(self) -> None:
        # Excel routinely emits workbook-level relationships (customXml, calcChain, ...)
        # whose Target points above xl/, e.g. "../customXml/item1.xml". Those parts are
        # never loaded as worksheets, so they must not trip the anti-traversal check.
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
                  <Relationship Id="rId2" Target="../customXml/item1.xml"
                    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml"/>
                </Relationships>""",
            )
            archive.writestr(
                "xl/worksheets/sheet1.xml",
                """<?xml version="1.0"?>
                <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                  <sheetData>
                    <row r="1"><c r="A1" t="inlineStr"><is><t>Address</t></is></c></row>
                    <row r="2"><c r="A2"><v>1</v></c></row>
                  </sheetData>
                </worksheet>""",
            )
        result = parse_xlsx(stream.getvalue())
        self.assertEqual(1, len(result["records"]))

    def test_worksheet_relationship_escaping_workbook_directory_is_rejected(self) -> None:
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
                  <Relationship Id="rId1" Target="../../etc/passwd"
                    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>
                </Relationships>""",
            )
        with self.assertRaises(ParseError):
            parse_xlsx(stream.getvalue())

    def test_merged_title_row_above_header_is_skipped(self) -> None:
        # Mirrors real vendor workbooks (e.g. Entergy/ComAp) that place a single
        # merged title cell in row 1 and the real column headers in row 2.
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
                  <si><t>Battery Charger</t></si><si><t>Address</t></si><si><t>Area</t></si>
                </sst>""",
            )
            archive.writestr(
                "xl/worksheets/sheet1.xml",
                """<?xml version="1.0"?>
                <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                  <sheetData>
                    <row r="1"><c r="A1" t="s"><v>0</v></c></row>
                    <row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2" t="s"><v>2</v></c></row>
                    <row r="3"><c r="A3"><v>40001</v></c><c r="B3" t="s"><v>2</v></c></row>
                  </sheetData>
                </worksheet>""",
            )
        result = parse_xlsx(stream.getvalue())
        self.assertEqual(1, len(result["records"]))
        self.assertEqual(40001, result["records"][0]["address"])
        self.assertTrue(any(a["code"] == "skipped_title_row" for a in result["assumptions"]))

    def test_single_column_header_is_not_mistaken_for_a_title(self) -> None:
        # A genuine one-cell header (single-column worksheet) must not be skipped
        # just because it only has one populated value.
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
                  <si><t>Address</t></si>
                </sst>""",
            )
            archive.writestr(
                "xl/worksheets/sheet1.xml",
                """<?xml version="1.0"?>
                <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                  <sheetData>
                    <row r="1"><c r="A1" t="s"><v>0</v></c></row>
                    <row r="2"><c r="A2"><v>40001</v></c></row>
                  </sheetData>
                </worksheet>""",
            )
        result = parse_xlsx(stream.getvalue())
        self.assertEqual(1, len(result["records"]))
        self.assertEqual(40001, result["records"][0]["address"])
        self.assertFalse(any(a["code"] == "skipped_title_row" for a in result["assumptions"]))

    def test_mb_address_is_explicit_but_generic_offset_is_ambiguous(self) -> None:
        result = parse_source(
            "Description,MB Address,Access\nATS Status,40104,R\n",
            filename="map.csv",
        )
        self.assertEqual(1, len(result["records"]))
        self.assertEqual("40104", str(result["records"][0]["display_address"]))

        offset_result = parse_source(
            "Description,Access,Offset\nReady,R,12\n",
            filename="map.csv",
        )
        self.assertEqual([], offset_result["records"])
        rejected = offset_result["rejected_rows"][0]["record"]
        self.assertNotIn("protocol_offset", rejected)
        self.assertEqual("12", rejected["source_offset"])
        self.assertIn("ambiguous_offset_header", {item["code"] for item in offset_result["warnings"]})
        explicit = parse_source("Name,Zero-based Offset,Engineering Offset\nReady,12,-10\n", filename="map.csv")
        self.assertEqual("12", explicit["records"][0]["protocol_offset"])
        self.assertEqual("-10", explicit["records"][0]["engineering_offset"])

    def test_parenthetical_indexed_headers_alias_to_address(self) -> None:
        result = parse_source(
            "Holding Register # (1 indexed),Address (0 indexed),Description\n40001,0,Voltage\n",
            filename="map.csv",
        )
        record = result["records"][0]
        self.assertEqual(40001, int(record["display_address"]))
        self.assertEqual(0, int(record["protocol_offset"]))

    def test_label_value_preamble_rows_are_skipped_until_register_header(self) -> None:
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
                "xl/worksheets/sheet1.xml",
                """<?xml version="1.0"?>
                <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                  <sheetData>
                    <row r="1">
                      <c r="A1" t="inlineStr"><is><t>Connection:</t></is></c>
                      <c r="B1" t="inlineStr"><is><t>Lab bus</t></is></c>
                    </row>
                    <row r="2">
                      <c r="A2" t="inlineStr"><is><t>Protocol:</t></is></c>
                      <c r="B2" t="inlineStr"><is><t>Modbus TCP</t></is></c>
                    </row>
                    <row r="3">
                      <c r="A3" t="inlineStr"><is><t>Modbus Address</t></is></c>
                      <c r="B3" t="inlineStr"><is><t>Description</t></is></c>
                    </row>
                    <row r="4">
                      <c r="A4"><v>40010</v></c>
                      <c r="B4" t="inlineStr"><is><t>Bus voltage</t></is></c>
                    </row>
                  </sheetData>
                </worksheet>""",
            )
        result = parse_xlsx(stream.getvalue())
        self.assertEqual(1, len(result["records"]))
        self.assertEqual(40010, int(result["records"][0]["address"]))
        self.assertEqual("Bus voltage", result["records"][0]["description"])
        skipped = [a for a in result["assumptions"] if a["code"] == "skipped_title_row"]
        self.assertEqual(1, len(skipped))
        self.assertEqual([1, 2], skipped[0]["rows"])


if __name__ == "__main__":
    unittest.main()
