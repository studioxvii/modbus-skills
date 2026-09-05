"""Public synthetic source annotations remain bounded, literal and selected."""
import copy
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))
from modbus_skills.artifacts import stable_input_hash
from modbus_skills.source_intake import SourceIntakeError, compile_source_descriptor
from modbus_skills.user_map import UserMapError, compile_user_map_bundle
from modbus_skills import user_map

HEADERS = ['Protocol Offset', 'Name', 'Area', 'Datatype', 'Access', 'Scale']

def source(extra_headers=(), extra_rows=((),), *, names=None, defaults=None):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow([*HEADERS, *extra_headers])
    for i, extra in enumerate(extra_rows):
        writer.writerow([i, names[i] if names else f'Reading {i}', 'Input Registers', 'uint16', 'Read', 1, *extra])
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / 'synthetic.csv'
        path.write_text(stream.getvalue())
        return compile_source_descriptor({'path':str(path), **({'defaults':defaults} if defaults else {})})[0]

def bundle(oem, selected=None):
    points = [p for p in oem['points'] if selected is None or p['name'] in selected]
    selection = {'oem_map_hash':stable_input_hash(oem),'requested_measurements':['readings'],
        'included':[{'oem_point_id':p['oem_point_id'],'matched_intent':'readings','match_quality':'exact',
                     'reason':'Explicit synthetic selection','evidence_refs':[r['record_id'] for r in p['source_refs']]} for p in points],
        'suggested':[],'excluded':[]}
    return compile_user_map_bundle(oem,selection,case_id='literal-context')

def contexts(value):
    return [a for a in value['assumptions'] if a.get('code')=='source-literal-context']

class LiteralSourceContextTests(unittest.TestCase):
    def test_literal_units_notes_and_ranges_survive_without_executable_guess(self):
        oem=source(['Units/Notes','Notes','Min','Max'], [('psi_or_bar','Invalid during maintenance.',0,500)])
        original=copy.deepcopy(oem)
        result=bundle(oem)
        notes=contexts(result['user_map'])
        self.assertEqual({'units_notes','notes','minimum','maximum'},{n['field'] for n in notes})
        self.assertEqual({'psi_or_bar','Invalid during maintenance.','0','500'},{n['literal'] for n in notes})
        self.assertTrue(all(n['status']=='source-context-only' for n in notes))
        self.assertTrue(all(n['bindings'][0]['source_ref']=={'record_id':'csv:2'} for n in notes))
        point=result['user_map']['points'][0]
        self.assertIsNone(point['engineering_unit'])
        self.assertNotIn('minimum',point)
        self.assertNotIn('maximum',point)
        self.assertIn('not confirmed engineering',result['human_summary'])
        self.assertEqual(original,oem)
        self.assertEqual(result,bundle(oem))

    def test_repeated_long_note_stored_once_with_all_compact_bindings(self):
        text='Literal validity note '+('x'*2048)
        oem=source(['Notes'],[(text,)]*12)
        note,=contexts(oem)
        self.assertEqual(12,len(note['bindings']))
        result=bundle(oem)
        self.assertEqual(1,result['json'].count(text))
        self.assertEqual(1,result['human_summary'].count(text))
        self.assertEqual(12,len(contexts(result['user_map'])[0]['bindings']))
        self.assertLess(len(result['json']),20000)

    def test_conflicting_duplicate_aliases_remain_separate_literal_claims(self):
        oem=source(['Units/Notes','Units/Notes','Notes','Notes'],[('psi','bar','Valid in state A','Valid in state B')])
        notes=contexts(bundle(oem)['user_map'])
        self.assertEqual({'psi','bar','Valid in state A','Valid in state B'},{n['literal'] for n in notes})
        self.assertEqual({'units_notes','units_notes_2','notes','notes_2'},
                         {b['source_field'] for n in notes for b in n['bindings']})
        self.assertIsNone(oem['points'][0]['engineering_unit'])

    def test_selection_removes_unselected_context_and_shared_bindings(self):
        oem=source(['Notes'],[('Shared',),('Shared',),('Private other scope',)],names=['One','Two','Three'])
        result=bundle(oem,{'One'})
        note,=contexts(result['user_map'])
        self.assertEqual('Shared',note['literal'])
        self.assertEqual(1,len(note['bindings']))
        self.assertNotIn('Private other scope',result['json'])
        self.assertEqual(result['user_map']['points'][0]['oem_point_id'],note['bindings'][0]['oem_point_id'])

    def test_absent_blank_unrelated_fields_and_defaults_do_not_create_context(self):
        plain=source()
        self.assertEqual([],contexts(plain))
        for oem in (source(['Notes','Units/Notes'],[('','  ')]),source(['Unrelated annotation'],[('Not in scope',)]),
                    source(defaults={'minimum':0,'maximum':10})):
            self.assertEqual([],contexts(oem))
            self.assertNotIn('Literal source context',bundle(oem)['human_summary'])

    def test_missing_minimum_and_zero_maximum_remain_distinct(self):
        notes=contexts(source(['Min','Max'],[('',0)]))
        self.assertEqual([('maximum','0')],[(n['field'],n['literal']) for n in notes])

    def test_retained_nonblank_ascii_and_multibyte_exact_byte_boundaries(self):
        maximum=16*1024
        for note in ('x'*maximum, '\u00e9'*(maximum//2)):
            self.assertEqual(note,contexts(source(['Notes'],[(note,)]))[0]['literal'])
            with self.assertRaisesRegex(SourceIntakeError,'literal source context.*16 KiB'):
                source(['Notes'],[(note+'x',)])

    def test_direct_canonical_handoff_retains_existing_whitespace(self):
        from modbus_skills.source_intake import _literal_context_entries
        value='x'+' '*((16*1024)-1)
        canonical=[{'unmapped_fields':{'notes':value},'source_evidence':[]}]
        points=[{'oem_point_id':'synthetic-point','source_refs':[{'record_id':'json:0'}]}]
        notes=user_map.build_literal_source_context(_literal_context_entries(canonical,points))
        self.assertEqual(value,notes[0]['literal'])
        canonical[0]['unmapped_fields']['notes']=value+' '
        with self.assertRaisesRegex(UserMapError,'16 KiB'):
            user_map.build_literal_source_context(_literal_context_entries(canonical,points))

    def test_lowered_graph_group_and_binding_budgets_fail_closed(self):
        for constant,value,rows in [('_LITERAL_CONTEXT_BYTES',1,[('note',)]),
                                    ('_LITERAL_CONTEXT_GROUPS',1,[('first',),('second',)]),
                                    ('_LITERAL_CONTEXT_BINDINGS',1,[('shared',),('shared',)])]:
            with self.subTest(constant=constant),patch.object(user_map,constant,value):
                with self.assertRaisesRegex(SourceIntakeError,'literal source context.*limit|literal source context.*budget'):
                    source(['Notes'],rows)

    def test_html_markdown_and_formula_text_are_inert(self):
        text='<script>alert(1)</script> [open](https://example.invalid) =1+1'
        result=bundle(source(['Notes'],[(text,)]))
        self.assertEqual(text,contexts(result['user_map'])[0]['literal'])
        self.assertNotIn('<script>',result['human_summary'])
        self.assertNotIn('[open](https://',result['human_summary'])
        self.assertIn('&lt;script&gt;',result['human_summary'])
        self.assertNotIn(text,result['csv'])

    def test_malformed_or_forged_registry_is_rejected_not_called_engineering(self):
        original=source(['Notes'],[('note',)])
        for mutation in ('id','point','reference','status','field','bindings'):
            oem=copy.deepcopy(original)
            note=contexts(oem)[0]
            if mutation=='id':note['context_id']='forged'
            elif mutation=='point':note['bindings'][0]['oem_point_id']='missing-point'
            elif mutation=='reference':note['bindings'][0]['source_ref']={'record_id':'csv:999'}
            elif mutation=='status':note['status']='confirmed-engineering'
            elif mutation=='field':note['field']='executable_unit'
            else:note['bindings']='not-an-array'
            with self.subTest(mutation=mutation),self.assertRaises(UserMapError):
                bundle(oem)

    def test_absolute_path_literal_is_held_by_existing_portable_boundary(self):
        with self.assertRaises(SourceIntakeError):
            source(['Notes'],[('/tmp/not-an-artifact',)])

    def test_context_does_not_change_executable_points_csv_or_existing_holds(self):
        oem=source(['Units/Notes','Notes'],[('Unconfigured unit','Literal warning')])
        with_context=bundle(oem)
        without=copy.deepcopy(oem)
        without['assumptions']=[a for a in without['assumptions'] if a.get('code')!='source-literal-context']
        plain=bundle(without)
        for field in ('points','holds','exception_annex'):
            self.assertEqual(plain['user_map'][field],with_context['user_map'][field])
        self.assertEqual(plain['csv'],with_context['csv'])

if __name__=='__main__':
    unittest.main()
