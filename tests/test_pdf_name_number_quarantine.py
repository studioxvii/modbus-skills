"""A numeric name fragment is not an independently addressed register."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugins/modbus-skills/runtime'))
from modbus_skills import pdf_extraction as pdf
from modbus_skills import pdf_table_extraction as grid
from test_pdf_explicit_column_roles import drawn_projection_input


class NameNumberQuarantineTests(unittest.TestCase):
    def example(self):
        row,words,proof=drawn_projection_input()
        name=next(c for c in proof['cells'] if c['field']=='name')
        glyph=name['glyphs'][0]
        glyph['text']='1'
        name['raw_value']='1'+name['raw_value'][1:]
        box=glyph['bbox']
        words[1]=[(x0,x1,top,bottom,'1' if [x0,top,x1,bottom]==box else text)
                  for x0,x1,top,bottom,text in words[1]]
        row['source_register']='1'
        row['_claims'][0].update(value='1')
        row['_claims'][0]['source_locator']['bbox']=list(box)
        return row,words,proof

    def test_proved_numeric_name_fragment_is_preserved_only_in_quarantine(self):
        row,words,proof=self.example();held=[]
        self.assertEqual([],pdf._apply_drawn_name_partitions([row],words,[proof],'a'*64,quarantined=held))
        self.assertEqual(1,len(held))
        self.assertEqual(row['_claims'],held[0]['_claims'])
        self.assertEqual(row['_source'],held[0]['_source'])
        self.assertEqual('1',held[0]['source_register'])
        self.assertEqual('pdf-address-from-nonaddress-cell',held[0]['code'])

    def test_unproved_wrong_source_or_ambiguous_cells_cannot_quarantine(self):
        row,words,proof=self.example()
        for change in ('source','glyph','bbox','duplicate','word'):
            p=deepcopy(proof);w=deepcopy(words);r=deepcopy(row);proofs=[p]
            if change=='source':p['source_sha256']='b'*64
            elif change=='glyph':next(c for c in p['cells'] if c['field']=='name')['glyphs'][0]['text']='2'
            elif change=='bbox':r['_claims'][0]['source_locator']['bbox']=[0,0,1,1]
            elif change=='duplicate':proofs.append(deepcopy(p))
            else:w[1]=[]
            with self.subTest(change=change):
                held=[]
                self.assertEqual([r],pdf._apply_drawn_name_partitions([r],w,proofs,'a'*64,quarantined=held))
                self.assertEqual([],held)

    def test_imported_parser_does_not_acquire_source_owned_conflict(self):
        row,words,proof=self.example();row['_source']['parser_id']='imported';held=[]
        self.assertEqual([row],pdf._apply_drawn_name_partitions([row],words,[proof],'a'*64,quarantined=held))
        self.assertEqual([],held)

    def test_duplicate_raw_header_claim_must_bind_the_same_box(self):
        row,words,proof=self.example()
        row['_claims'].append({**deepcopy(row['_claims'][0]),'raw_header':'Addr.','raw_value':'1'})
        held=[]
        self.assertEqual([],pdf._apply_drawn_name_partitions([row],words,[proof],'a'*64,quarantined=held))
        self.assertEqual(row['_claims'],held[0]['_claims'])
        row['_claims'][1]['source_locator']['bbox']=[0,0,1,1]
        held=[]
        self.assertEqual([row],pdf._apply_drawn_name_partitions([row],words,[proof],'a'*64,quarantined=held))
        self.assertEqual([],held)

    def test_distinct_reader_font_metrics_still_require_complete_cell_text(self):
        row,words,proof=self.example()
        name=next(c for c in proof['cells'] if c['field']=='name')
        for glyph in name['glyphs']:
            glyph['bbox'][1]+=0.5
            glyph['bbox'][3]+=0.5
        held=[]
        self.assertEqual([],pdf._apply_drawn_name_partitions([row],words,[proof],'a'*64,quarantined=held))
        self.assertEqual(1,len(held))
        words[1].append((200,203,35,45,'extra'))
        held=[]
        self.assertEqual([row],pdf._apply_drawn_name_partitions([row],words,[proof],'a'*64,quarantined=held))
        self.assertEqual([],held)
