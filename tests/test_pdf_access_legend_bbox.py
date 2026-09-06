"""Independent coordinate parser must not undo access conflicts."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/modbus-skills/runtime'))
import re
import unittest
from xml.etree.ElementTree import Element, SubElement, tostring
from modbus_skills.pdf_extraction import parse_bbox_rows

def bbox(text):
    root=Element('doc'); page=SubElement(root,'page')
    for i,line in enumerate(text.splitlines()):
        for token in re.finditer(r'\S+',line):
            node=SubElement(page,'word',{'xMin':str(token.start()*5),'xMax':str(token.end()*5),'yMin':str(i*15),'yMax':str(i*15+10)})
            node.text=token.group()
    return tostring(root,encoding='unicode')

LEGEND='* R-read only, W-write only, R/W-read and write.'
TABLE=f"{'Name':22}{'Description':36}Register\n{'Reset state':22}{'Synthetic state (W)':36}0x0100"

class AccessLegendBboxTests(unittest.TestCase):
    def test_coordinate_access_is_preserved_with_own_locators(self):
        rows=parse_bbox_rows(bbox(TABLE+'\n'+LEGEND))
        self.assertEqual(1,len(rows))
        self.assertEqual('W',rows[0].get('access'))
        claim=next(c for c in rows[0]['_claims'] if c['field']=='access')
        self.assertIn('bbox',claim['source_locator'])
        self.assertEqual('p1:y30',claim['legend_source_locator']['region'])

    def test_coordinate_conflict_cannot_reintroduce_readable_row(self):
        text=f"{'Name':22}{'Description':36}{'Register':14}Access\n{'Reset state':22}{'Synthetic state (W)':36}{'0x0100':14}R\n"+LEGEND
        self.assertEqual([],parse_bbox_rows(bbox(text)))

    def test_coordinate_no_legend_keeps_existing_unknown_access(self):
        rows=parse_bbox_rows(bbox(TABLE))
        self.assertEqual(1,len(rows)); self.assertNotIn('access',rows[0])

if __name__=='__main__': unittest.main()
