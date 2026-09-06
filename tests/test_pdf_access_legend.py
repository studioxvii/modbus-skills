"""Synthetic controls for source-declared trailing access annotations."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/modbus-skills/runtime'))
import unittest
from modbus_skills.pdf_extraction import parse_layout_rows

HEADER = f"{'Name':22}{'Description':36}Register"
LEGEND = '* R-read only, W-write only, R/W-read and write.'

def table(markers=('R', 'W', 'R/W')):
    return '\n'.join([HEADER] + [f"{'Point'+str(i):22}{('Synthetic state ('+marker+')'):36}0x{256+i:04X}"
                                 for i, marker in enumerate(markers)])

class AccessLegendTests(unittest.TestCase):
    def test_declared_annotations_preserve_text_and_add_located_access(self):
        rows, _ = parse_layout_rows(table() + '\n' + LEGEND)
        self.assertEqual(['R','W','R/W'], [row.get('access') for row in rows])
        for row, marker in zip(rows, ('R','W','R/W')):
            self.assertEqual('Synthetic state ('+marker+')', row['description'])
            claim = next(c for c in row['_claims'] if c['field']=='access')
            self.assertEqual(row['_source']['region'], claim['source_locator']['region'])
            self.assertEqual(5, claim['legend_source_locator']['line'])
            self.assertEqual(LEGEND, claim['legend_literal'])

    def test_missing_or_unrelated_legend_does_not_guess_access(self):
        for note in ('', 'W = watts; R = resistance.', 'R-read only, W-write only.', 'Notes: R-read only, W-write only, R/W-read and write.'):
            rows, _ = parse_layout_rows(table()+'\n'+note)
            self.assertTrue(rows)
            self.assertTrue(all('access' not in row for row in rows))

    def test_legend_on_another_page_does_not_apply(self):
        rows, _ = parse_layout_rows(table()+'\f'+LEGEND)
        self.assertTrue(all('access' not in row for row in rows))

    def test_legend_before_the_table_does_not_apply(self):
        rows, _ = parse_layout_rows(LEGEND+'\n'+table())
        self.assertTrue(all('access' not in row for row in rows))

    def test_new_table_limits_legend_scope(self):
        rows, _ = parse_layout_rows(table(('W',))+'\n'+table(('R',))+'\n'+LEGEND)
        self.assertEqual(2, len(rows))
        self.assertNotIn('access', rows[0])
        self.assertEqual('R', rows[1].get('access'))

    def test_intervening_prose_does_not_bind_a_remote_legend(self):
        rows, _ = parse_layout_rows(table()+'\nUnrelated section\n'+LEGEND)
        self.assertTrue(all('access' not in row for row in rows))

    def test_annotation_must_be_at_end_of_actual_description(self):
        text = table(('W',)).replace('Synthetic state (W)', 'Synthetic (W) state')
        rows, _ = parse_layout_rows(text+'\n'+LEGEND)
        self.assertNotIn('access', rows[0])

    def test_name_only_suffix_is_not_a_description_access_field(self):
        text = 'Name                    Register\nPower (W)               0x0100\n'+LEGEND
        rows, _ = parse_layout_rows(text)
        self.assertTrue(rows)
        self.assertNotIn('access', rows[0])

    def test_conflicting_explicit_access_is_held_not_overwritten(self):
        text = f"{'Name':22}{'Description':36}{'Register':14}Access\n"
        text += f"{'Reset state':22}{'Synthetic state (W)':36}{'0x0100':14}R\n"+LEGEND
        rows, rejected = parse_layout_rows(text)
        self.assertEqual([], rows)
        conflicts = [row for row in rejected if row.get('code')=='pdf-access-annotation-conflict']
        self.assertEqual(1, len(conflicts))
        self.assertEqual('R', conflicts[0]['explicit_access'])
        self.assertEqual('W', conflicts[0]['annotation_access'])

if __name__ == '__main__': unittest.main()
