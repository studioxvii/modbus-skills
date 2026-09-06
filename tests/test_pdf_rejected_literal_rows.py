"""Prospective source evidence retention, never executable range expansion."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/modbus-skills/runtime'))
from modbus_skills import pdf_extraction as pdf

class RejectedLiteralRowTests(unittest.TestCase):
    def test_explicit_range_retains_all_literal_fields_without_becoming_point(self):
        text='No.   Name          Address      Data type   Data range   Unit   Note\n1     Counter pair  4100 - 4101  U32                      Wh     Model option'
        rows,rejected=pdf.parse_layout_rows(text,first_page=12,parser_id='external-ocr-layout/v1')
        self.assertEqual([],rows);self.assertEqual(1,len(rejected))
        row=rejected[0];self.assertEqual('pdf-row-address-invalid',row['code'])
        claims={claim['raw_header']:claim for claim in row['_claims']}
        for header,literal in {'No.':'1','Name':'Counter pair','Address':'4100 - 4101','Data type':'U32','Data range':'','Unit':'Wh','Note':'Model option'}.items():
            self.assertEqual(literal,claims[header]['raw_value'])
            self.assertEqual({'page':12,'line':2,'region':'p12:l2'},claims[header]['source_locator'])
        self.assertEqual('ocr-derived',row['_source']['method'])
        for field in ('protocol_offset','word_count','datatype','area','function_code'):self.assertNotIn(field,row)

    def test_malformed_literal_and_reserved_range_are_retained_not_repaired(self):
        for address in ('??','4100 - 4109'):
            text='Name       Address       Data type   Note\nReserved   '+address.ljust(14)+'U16*10      Source ambiguity'
            rows,rejected=pdf.parse_layout_rows(text)
            self.assertEqual([],rows);self.assertEqual(1,len(rejected))
            claims={c['raw_header']:c['raw_value'] for c in rejected[0]['_claims']}
            self.assertEqual(address,claims['Address']);self.assertEqual('Reserved',claims['Name'])

    def test_valid_scalar_retains_existing_record_semantics(self):
        rows,rejected=pdf.parse_layout_rows('Address   Name     Data type\n4100      Counter  U16')
        self.assertEqual([],rejected);self.assertEqual(1,len(rows));self.assertEqual('4100',rows[0]['source_register'])

if __name__=='__main__':unittest.main()
