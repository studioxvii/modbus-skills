"""A later independent parser cannot clear a source access conflict."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/modbus-skills/runtime'))
import unittest
from modbus_skills.pdf_extraction import _envelope, parse_layout_rows

TABLE=f"{'Name':22}{'Description':36}{'Register':14}Access\n{'Reset state':22}{'Synthetic state (W)':36}{'0x0100':14}R"
LEGEND='* R-read only, W-write only, R/W-read and write.'

class AccessLegendEnvelopeTests(unittest.TestCase):
    def test_later_parser_row_remains_quarantined(self):
        rows,rejected=parse_layout_rows(TABLE+'\n'+LEGEND)
        self.assertEqual([],rows)
        later,_=parse_layout_rows(TABLE)
        envelope=_envelope(Path('synthetic.pdf'),b'%PDF synthetic',later,rejected,[],[],(1,1),discovered_pages=[1])
        self.assertEqual([],envelope['records'])
        self.assertTrue(envelope['quarantined_records'])
        self.assertIn('pdf-access-annotation-conflict',[hold['code'] for hold in envelope['holds']])

    def test_other_page_row_does_not_inherit_the_conflict(self):
        _,rejected=parse_layout_rows(TABLE+'\n'+LEGEND)
        later,_=parse_layout_rows(TABLE,first_page=2)
        envelope=_envelope(Path('synthetic.pdf'),b'%PDF synthetic',later,rejected,[],[],(1,2),discovered_pages=[1,2])
        self.assertEqual(1,len(envelope['records']))
        self.assertEqual(2,envelope['records'][0]['_source']['page'])

if __name__=='__main__': unittest.main()
