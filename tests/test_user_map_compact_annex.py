from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))
from modbus_skills.user_map import render_human_summary


def issue(row, **overrides):
    return {'kind': 'unselected-hold', 'code': 'address.area-unresolved', 'field': 'area',
            'severity': 'hold', 'blocking': True, 'message': 'Declare the Modbus area.',
            'details': {'source_field': 'address'}, 'point_ids': [f'p{row}'],
            'source': {'sheet': 'Table', 'row': row}, **overrides}


def render(annex, holds=None):
    artifact = {'points': [], 'holds': holds or [], 'exception_annex': annex}
    original = deepcopy(artifact)
    output = render_human_summary(artifact, {'suggested': []})
    assert artifact == original
    return output


class CompactAnnexTests(unittest.TestCase):
    def test_identical_unselected_issues_are_one_counted_human_line(self):
        text = render([issue(r) for r in range(1, 35)])
        self.assertEqual(1, text.count('address.area-unresolved:'))
        self.assertIn('34 unselected source records', text)
        self.assertIn('user-map.json', text)

    def test_distinct_semantic_details_reasons_and_severity_are_not_merged(self):
        changes = ({'details': {'source_field': 'other'}}, {'message': 'Confirm the area.'},
                   {'severity': 'error'}, {'field': 'other'}, {'blocking': False})
        for change in changes:
            with self.subTest(change=change):
                text = render([issue(1), issue(2, **change)])
                self.assertEqual(2, text.count('address.area-unresolved:'))
                self.assertNotIn('2 unselected source records', text)

    def test_explicit_exclusions_remain_individual_and_in_order(self):
        entries = [{'kind': 'excluded', 'oem_point_id': f'p{i}', 'reason': 'Outside requested scope.'} for i in (2, 1)]
        text = render(entries)
        self.assertIn('p2: Outside requested scope.', text)
        self.assertIn('p1: Outside requested scope.', text)
        self.assertLess(text.index('p2:'), text.index('p1:'))
        self.assertNotIn('unselected source records', text)

    def test_selected_blocking_holds_are_not_hidden_or_folded_into_annex(self):
        selected = {'code': 'point.area-unresolved', 'message': 'Selected point needs an area.', 'affected_count': 1}
        text = render([issue(1), issue(2)], [selected])
        self.assertIn('point.area-unresolved: Selected point needs an area.', text)
        self.assertIn('2 unselected source records', text)
        self.assertLess(text.index('Selected point needs an area.'), text.index('2 unselected source records'))

    def test_single_issue_and_empty_annex_keep_existing_output(self):
        text = render([issue(1)])
        self.assertIn('- address.area-unresolved: Declare the Modbus area.\n', text)
        self.assertNotIn('unselected source records', text)
        self.assertNotIn('user-map.json', text)
        self.assertIn('## Exclusions and evidence annex\n- None\n', render([]))

    def test_group_order_is_stable_and_records_are_not_called_distinct_points(self):
        first = issue(1, point_ids=['same'])
        second = issue(2, point_ids=['same'])
        unrelated = {'kind': 'excluded', 'oem_point_id': 'explicit', 'reason': 'User choice.'}
        text = render([first, unrelated, second])
        self.assertEqual(text, render([first, unrelated, second]))
        self.assertIn('2 unselected source records', text)
        self.assertNotIn('2 points', text)
        self.assertLess(text.index('address.area-unresolved:'), text.index('explicit:'))


if __name__ == '__main__':
    unittest.main()
