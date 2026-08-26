#!/usr/bin/env python3
"""Build deliberately messy OEM-style synthetic fixtures for corpus testing."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "oem-corpus" / "synthetic"


def _xlsx_bytes(sheets: dict[str, list[list]]) -> bytes:
    shared: list[str] = []
    index: dict[str, int] = {}

    def si(text: str) -> int:
        if text not in index:
            index[text] = len(shared)
            shared.append(text)
        return index[text]

    sheet_xml_parts: list[str] = []
    sheet_entries: list[str] = []
    rel_entries: list[str] = []

    for sheet_id, (name, rows) in enumerate(sheets.items(), start=1):
        rid = f"rId{sheet_id}"
        sheet_entries.append(
            f'<sheet name="{name}" sheetId="{sheet_id}" r:id="{rid}"/>'
        )
        rel_entries.append(
            f'<Relationship Id="{rid}" Target="worksheets/sheet{sheet_id}.xml" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
        )
        row_xml: list[str] = []
        for row_idx, row in enumerate(rows, start=1):
            cells: list[str] = []
            for col_idx, value in enumerate(row):
                col = chr(ord("A") + col_idx)
                ref = f"{col}{row_idx}"
                if value is None:
                    continue
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                else:
                    cells.append(f'<c r="{ref}" t="s"><v>{si(str(value))}</v></c>')
            row_xml.append(f'<row r="{row_idx}">{"".join(cells)}</row>')
        sheet_xml_parts.append(
            (
                f'<?xml version="1.0"?>'
                f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"<sheetData>{''.join(row_xml)}</sheetData></worksheet>"
            )
        )

    sst_items = "".join(f"<si><t>{text}</t></si>" for text in shared)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            (
                '<?xml version="1.0"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f"<sheets>{''.join(sheet_entries)}</sheets></workbook>"
            ),
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            (
                '<?xml version="1.0"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f"{''.join(rel_entries)}</Relationships>"
            ),
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            (
                '<?xml version="1.0"?>'
                '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"{sst_items}</sst>"
            ),
        )
        for sheet_id, xml in enumerate(sheet_xml_parts, start=1):
            archive.writestr(f"xl/worksheets/sheet{sheet_id}.xml", xml)
    return stream.getvalue()


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_gotion_xlsx() -> None:
    sheets = {
        "Cover": [
            ["Gotion ESS — Modbus point export"],
            ["Project", "Site-17 Lab BESS"],
            ["PCS", "Sinexcel PWS1725K"],
            ["BMS firmware", "v3.2.1"],
            ["NOTE", "Map is on sheet BMS_Map"],
        ],
        "BMS_Map": [
            ["Protocol Offset", "Reg Type", "Tag Name", "R/W", "Type", "Gain", "Unit", "Comment"],
            [256, "4x Holding", "Stack_Voltage", "R", "U16", 0.1, "V", "电池总电压"],
            [257, "4x Holding", "Stack_Current", "R", "S16", 0.1, "A", "signed"],
            [258, "4x Holding", "Work_State", "R", "U16", 1, "", "0=standby 1=dischg 2=chg"],
            [259, "4x Holding", "SOC", "R", "U16", 1, "%", ""],
            [260, "4x Holding", "SOH", "R", "U16", 1, "%", ""],
            [261, "4x Holding", "Max_Cell_mV", "R", "U16", 1, "mV", ""],
            [262, "4x Holding", "Min_Cell_mV", "R", "U16", 1, "mV", ""],
            [270, "4x Holding", "Insulation_kOhm", "R", "U16", 1, "kOhm", ""],
            [285, "4x Holding", "Charge_Request", "R", "U16", 1, "", "1=need charge"],
        ],
        "Alarms": [
            ["Bit", "Alarm text"],
            [0, "BMS comm fault"],
            [1, "Cell OV"],
        ],
    }
    (OUT / "gotion-bess-integrator-messy.xlsx").write_bytes(_xlsx_bytes(sheets))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    intake = OUT / "intake-junk"
    parseable = OUT / "parseable-messy"
    intake.mkdir(exist_ok=True)
    parseable.mkdir(exist_ok=True)
    build_gotion_xlsx()

    write_text(
        intake / "veris-meter-export-junk.csv",
        """# Veris E51C2 — integrator export 2024-11-02
# PLC addressing: subtract 1 for RS-485 offset
Modbus Addr,PLC Addr,Parameter,FC,Format,Scale,Units,R/W
40001,0,Phase A Voltage,03,Float32,1,Volts,R
40003,2,Phase B Voltage,03,Float32,1,Volts,R
40005,4,Phase C Voltage,03,Float32,1,Volts,R
""",
    )

    write_text(
        parseable / "veris-meter-messy.csv",
        """display_address,Parameter,FC,Format,Gain,Units,Access
40001,Phase A Voltage,03,Float32,1,Volts,R
40003,Phase B Voltage,03,Float32,1,Volts,R
40005,Phase C Voltage,03,Float32,1,Volts,R
40007,Phase A Current,03,Float32,1,Amps,R
40009,Total kW,03,Float32,1,kW,R
40011,Import kWh,03,Float32,1,kWh,R
40013,Export kWh,03,Float32,1,kWh,R
130,System Type,03,UInt16,1,,RW
""",
    )

    write_text(
        intake / "narada-bess-junk.csv",
        """Narada NPFC cluster Modbus map (BMS V1.6 excerpt)
Hangzhou Narada — FOR COMMISSIONING USE ONLY
Hex Addr,Decimal,名称 / Name,DataType,Resolution,Offset,Notes
0x0100,256,Battery stack voltage,uint16,0.1 V,,
0x0101,257,Battery circuit current,int16,0.1 A,0,signed
0x0103,259,Battery stack SOC,uint16,1 %,,
""",
    )

    write_text(
        parseable / "narada-bess-messy.csv",
        """protocol_offset,Tag,Type,Gain,Unit,Notes
256,Stack_Voltage,uint16,0.1,V,BMS cluster 1
257,Stack_Current,int16,0.1,A,signed discharge +
258,Work_State,uint16,1,,0 standby 1 dischg 2 chg
259,SOC,uint16,1,%
260,SOH,uint16,1,%
262,Max_Cell_mV,uint16,1,mV
264,Min_Cell_mV,uint16,1,mV
278,Insulation,uint16,1,kOhm
""",
    )

    write_text(
        parseable / "generac-dg-messy.csv",
        """protocol_offset,Tag,Type,Access,Description
15,oil_press,unsigned int,R,Oil pressure
16,rpm,unsigned int,R,Engine RPM
17,freq,signed int,R,Output frequency
18,oil_temp,unsigned int,R,Oil temperature
19,fuel_level,unsigned int,R,Fuel level pct
22,gen_voltsA,unsigned int,R,Gen L-N volts A
26,load_amps,unsigned int,R,Load amps
30,Dispctrl,bitfield,R/W,Display control bits
""",
    )

    write_text(
        parseable / "cummins-pc-messy.tsv",
        """Register\tParameter Name\tHigh Reg\tLow Reg\tMultiplier\tUnits\tAccess
40070\tEngine Running Time\t40070\t40071\t0.1\tseconds\tR
40002\tEngine Speed (RPM)\t40002\t\t1\tRPM\tR
40011\tCoolant Temperature\t40011\t\t1\tdegF\tR
40020\tPercent Load\t40020\t\t1\t%\tR
40025\tkW Total\t40025\t40026\t0.1\tkW\tR
40100\tOil Pressure\t40100\t\t1\tpsi\tR
""",
    )

    write_text(
        intake / "sungrow-inverter-junk.csv",
        """# Sungrow string inverter — protocol v1.1.37 style excerpt
# IMPORTANT: poll address = document register - 1
Doc Register,Poll Address,Point,Type,Gain,Unit,Area
5000,4999,Device type code,U16,1,,Input
5019,5018,Phase A voltage,U16,0.1,V,Input
5033,5032,Total active power,S32,1,W,Input
""",
    )

    write_text(
        parseable / "sungrow-inverter-messy.csv",
        """display_address,Point,datatype,Gain,Unit,register_area
5000,Device type code,uint16,1,,input register
5019,Phase A voltage,uint16,0.1,V,input register
5020,Phase B voltage,uint16,0.1,V,input register
5021,Phase C voltage,uint16,0.1,V,input register
5033,Total active power,int32,1,W,input register
5036,Reactive power,int32,1,var,input register
5003,Daily yield,uint32,0.1,kWh,input register
""",
    )

    write_text(
        parseable / "cat-emcp-messy.csv",
        """Register,Description,datatype,Gain,Units,Access
40001,Engine Speed,uint16,1,RPM,Read
40002,Oil Pressure,uint16,1,psi,Read
40003,Coolant Temp,int16,1,degF,Read
40010,Run Hours High,uint16,1,hrs,Read
40011,Run Hours Low,uint16,1,hrs,Read
40020,Generator kW,uint16,0.1,kW,Read
40025,Line Voltage L1,uint16,0.1,VAC,Read
40100,Operating State,uint16,1,,Read
""",
    )

    write_text(
        parseable / "schneider-pm5560-messy.csv",
        """Register,Parameter,Size,datatype,Units,Gain,Access
3027,Voltage L1-N,2,float32,Volts,1,R
3009,Current L1,2,float32,Amps,1,R
3059,Active Power Total,2,float32,Watts,1,R
3067,Reactive Power Total,2,float32,VAR,1,R
3083,Power Factor Total,2,float32,,1,R
3109,Frequency,2,float32,Hz,1,R
3203,Active Energy Delivered,4,int64,Wh,1,R
3207,Active Energy Received,4,int64,Wh,1,R
""",
    )

    write_text(
        parseable / "tesla-bess-messy.csv",
        """Register,Name,datatype,Range,Unit,Access
1000,Real Power Command Mode,enum,1,,RW
1001,Real Power Setpoint,int32,-1e9..1e9,W,RW
1022,Heartbeat Toggle,uint16,55AA or AA55,,RW
1023,Command Timeout,uint16,0..600,s,RW
2000,Site Real Power,int32,,W,R
2002,Site Reactive Power,int32,,var,R
2010,State of Charge,uint16,0..100,%,R
""",
    )

    write_text(
        intake / "cummins-title-junk.csv",
        """Cummins PowerCommand 3.3 — raw parametrics excerpt
Document A029X159 (integrator paste)
Modbus Ref,Parameter Name,High Reg,Low Reg,Multiplier,Units,Access
40070,Engine Running Time,40070,40071,0.1,seconds,R
40002,Engine Speed (RPM),40002,,1,RPM,R
""",
    )

    print(f"Wrote synthetic fixtures under {OUT}")


if __name__ == "__main__":
    main()
