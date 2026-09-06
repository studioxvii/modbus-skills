"""Rejected PDF source evidence must survive the delivered compact map."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'plugins/modbus-skills/runtime'))
from modbus_skills.artifacts import stable_input_hash
from modbus_skills.compiler import compile_user_map
from modbus_skills.pdf_extraction import _envelope, parse_layout_rows
from modbus_skills.source_intake import compile_source_descriptor

TABLE = 'Address       Name          Data type   Note\n4100          Quantity      U16         Visible value\n4101 - 4102   Counter pair  U32         Source definition unresolved'


class PdfRejectedDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'synthetic.pdf'
        self.source.write_bytes(b'%PDF synthetic source')
        rows, rejected = parse_layout_rows(TABLE, first_page=7)
        self.parsed = _envelope(self.source, self.source.read_bytes(), rows, rejected, [], [], (7,7), discovered_pages=[7])
        self.assertEqual(1,len(self.parsed['records']))
        self.assertEqual(1,len(self.parsed['rejected_rows']))

    def request(self):
        return {'schema_version':'modbus-compile-request/v1',
                'source':{'path':str(self.source),'format':'pdf'},
                'selection_template':{'schema_version':'modbus-user-selection-template/v1',
                    'requested_measurements':['All readable source points'],'mode':'all-readable'},
                'targets':[], 'target_options':{}}

    def test_oem_retains_source_bound_rejections_without_new_points(self):
        original = copy.deepcopy(self.parsed)
        with patch('modbus_skills.source_intake.extract_pdf',return_value=self.parsed):
            oem, _ = compile_source_descriptor(self.request()['source'])
        hold = next(h for h in oem['holds'] if h['code']=='source.rejected-rows-unresolved')
        self.assertEqual(self.parsed['rejected_rows'],hold.get('details',{}).get('rejected_rows'))
        self.assertEqual(stable_input_hash(self.source.read_bytes()),hold['details']['source_sha256'])
        self.assertEqual(['Quantity'],[p['name'] for p in oem['points']])
        self.assertEqual(original,self.parsed)

    def test_delivered_json_retains_literals_and_markdown_groups_once(self):
        with patch('modbus_skills.source_intake.extract_pdf',return_value=self.parsed):
            result = compile_user_map(self.request(),self.root/'case')
        user_map = json.loads((self.root/'case/output/user-map.json').read_text())
        evidence = [row for row in user_map['exception_annex'] if row.get('kind')=='source-rejected-evidence']
        self.assertEqual(1,len(evidence))
        self.assertEqual(self.parsed['rejected_rows'],evidence[0]['rejected_rows'])
        self.assertEqual(stable_input_hash(self.source.read_bytes()),evidence[0]['source_sha256'])
        self.assertEqual(['Quantity'],[p['name'] for p in user_map['points']])
        self.assertTrue(any(h['code']=='source.rejected-rows-unresolved' for h in user_map['holds']))
        self.assertNotEqual('complete',result['state'])
        markdown=(self.root/'case/output/user-map.md').read_text()
        self.assertNotIn('4101 - 4102',markdown)
        self.assertNotIn('Source definition unresolved',markdown)
        self.assertIn('1 rejected source row',markdown)
        self.assertIn('user-map.json',markdown)
        csv=(self.root/'case/output/user-map.csv').read_text()
        self.assertNotIn('Counter pair',csv)

    def test_no_rejected_rows_adds_no_source_evidence_payload(self):
        self.parsed['rejected_rows']=[]
        with patch('modbus_skills.source_intake.extract_pdf',return_value=self.parsed):
            oem,_=compile_source_descriptor(self.request()['source'])
        self.assertFalse(any(h['code']=='source.rejected-rows-unresolved' for h in oem['holds']))


if __name__=='__main__':
    unittest.main()
