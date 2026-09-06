"""Explicit layout anchors win over spacing counts and incidental header words."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/modbus-skills/runtime'))
from modbus_skills import pdf_extraction as pdf

HEADERS=('No.','Name','Address','Data type','Data range','Unit','Note')
WIDTHS=(8,48,20,16,30,15,180)
def line(values):return ''.join(str(value).ljust(width) for value,width in zip(values,WIDTHS)).rstrip()
HEADER=line(HEADERS)

class AnchoredBodyRowTests(unittest.TestCase):
    def test_double_space_inside_name_does_not_shift_explicit_address(self):
        body=line(('1','Negative voltage  to ground','5146','S16','-10000 - 10000','0.1V',''))
        rows,rejected=pdf.parse_layout_rows(HEADER+'\n'+body)
        self.assertEqual([],rejected);self.assertEqual(1,len(rows))
        self.assertEqual('5146',rows[0]['source_register'])
        self.assertEqual('Negative voltage to ground',rows[0]['name'])
        self.assertEqual('S16',rows[0]['datatype'])
        self.assertEqual('0.1V',rows[0]['engineering_unit'])
        self.assertIn('voltage  to ground',rows[0]['_source']['excerpt'])

    def test_internal_note_gap_does_not_fill_blank_unit(self):
        body=line(('1','Input status','5146','U16','0-2','','Only if hardware  is present'))
        rows,rejected=pdf.parse_layout_rows(HEADER+'\n'+body)
        self.assertEqual([],rejected);self.assertEqual(1,len(rows))
        self.assertNotIn('engineering_unit',rows[0])
        self.assertEqual('Only if hardware is present',rows[0]['_extra']['note'])

    def test_parameter_address_note_is_body_not_new_header_and_following_rows_survive(self):
        bodies=[line(('1','Input current','7013','U16','','0.01A','If parameter is visible, the corresponding address is readable.'))]
        bodies.extend(line((i,f'Input current {i}',7012+i,'U16','','0.01A','')) for i in range(2,18))
        rows,rejected=pdf.parse_layout_rows(HEADER+'\n'+'\n'.join(bodies),first_page=7)
        self.assertEqual([],rejected);self.assertEqual([str(n) for n in range(7013,7030)],[row['source_register'] for row in rows])
        self.assertEqual(7,rows[-1]['_source']['page'])
        self.assertEqual('If parameter is visible, the corresponding address is readable.',rows[0]['_extra']['note'])

    def test_header_words_in_point_name_do_not_replace_current_columns(self):
        rows,rejected=pdf.parse_layout_rows(HEADER+'\n'+line(('1','Parameter address','7013','U16','','','')))
        self.assertEqual([],rejected);self.assertEqual(['Parameter address'],[r['name'] for r in rows])

    def test_range_with_header_words_remains_literal_rejection_not_expansion(self):
        text=HEADER+'\n'+line(('1','Counter pair','4100 - 4101','U32','','Wh','Check parameter address.'))+'\n'+line(('2','Following','4102','U16','','',''))
        rows,rejected=pdf.parse_layout_rows(text)
        self.assertEqual(['4102'],[r['source_register'] for r in rows]);self.assertEqual(1,len(rejected))
        self.assertEqual('4100 - 4101',next(c['raw_value'] for c in rejected[0]['_claims'] if c['raw_header']=='Address'))
        self.assertNotIn('word_count',rejected[0])

    def test_real_new_header_is_still_recognized(self):
        text=HEADER+'\n'+line(('1','First','4100','U16','','',''))+'\nProtocol Offset   Name     Data Type\n17                Second   U16'
        rows,rejected=pdf.parse_layout_rows(text)
        self.assertEqual([],rejected);self.assertEqual(['First','Second'],[r['name'] for r in rows])
        self.assertEqual('protocol-offset',rows[1]['address_convention'])

    def test_ragged_legacy_layout_keeps_existing_parse(self):
        rows,rejected=pdf.parse_layout_rows('Address  Name  Data Type\n4100  Counter value  U16')
        self.assertEqual([],rejected);self.assertEqual(['Counter value'],[r['name'] for r in rows]);self.assertEqual('4100',rows[0]['source_register'])

    def test_ragged_access_does_not_append_to_scalar_bias(self):
        # Native PDF layout can place the Access heading two columns to the
        # right of its body value. It is not a prose continuation of bias.
        text='Protocol Offset   Name          Datatype   Byte Order   Scale   Engineering Offset\n                                                                               Access\n\n96                Sample Flow   uint16     AB           .25     -3            R'
        rows,rejected=pdf.parse_layout_rows(text)
        self.assertEqual([],rejected);self.assertEqual(1,len(rows))
        self.assertEqual('-3',rows[0]['engineering_offset'])
        self.assertEqual('R',rows[0]['access'])

if __name__=='__main__':unittest.main()
