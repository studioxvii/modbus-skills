"""Explicit synthetic selection decisions survive all later offline resumes."""
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
from modbus_skills import compiler
from modbus_skills.artifacts import stable_input_hash
from modbus_skills.compiler_contracts import build_device_binding, build_oem_map


class SelectionResumeInvariantTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def load(self, case, name):
        return json.loads((case / name).read_text())

    def start(self, size, *, target=False):
        folder = self.root / f'case-{size}-{target}'
        source = self.root / f'source-{size}-{target}.csv'
        source.write_text('Name,Protocol Offset,Area,Datatype,Access,Scale\n'
                          'Temperature,10,Input Registers,uint16,read-only,1\n'
                          + ('Pressure,20,Input Registers,uint16,read-only,1\n' if size == 2 else ''))
        names = ['Temperature'] + (['Pressure'] if size == 2 else [])
        request = {'schema_version':'modbus-compile-request/v1','source':{'path':str(source)},
            'selection_template':{'schema_version':'modbus-user-selection-template/v1',
                'requested_measurements':['suggested measurements awaiting an explicit choice'],
                'included':[], 'suggested':[{'exact_name':name,'matched_intent':'suggested measurements','match_quality':'near',
                                           'reason':'unconfirmed synthetic suggestion','evidence_refs':[f'csv:row:{i+2}']} for i,name in enumerate(names)],
                'excluded':[]}, 'targets':['node-red'] if target else [], 'target_options':{}}
        result = compiler.compile_user_map(request, folder)
        self.assertEqual('awaiting-selection-decision', result['state'])
        packet = self.load(folder, 'control/selection-packet.json')
        self.assertEqual(1, len(packet['decisions']))
        self.assertEqual(size, len(packet['decisions'][0]['subject_ids']))
        return folder

    def reply(self, folder, names):
        case = self.load(folder, 'case.json')
        packet = self.load(folder, 'control/selection-packet.json')
        oem = self.load(folder, 'artifacts/oem-map.json')
        selected = sorted(p['oem_point_id'] for p in oem['points'] if p['name'] in names)
        decision = packet['decisions'][0]
        candidate = {key:copy.deepcopy(packet[key]) for key in ('case_id','phase','packet_id','source_hash','input_hashes')}
        candidate.update({'schema_version':'modbus-compiler-decision-candidate/v1','decisions':[{
            'decision_id':decision['decision_id'],'disposition':'include-specified' if selected else 'exclude-all',
            'selected_subject_ids':selected,'reason':'Test-harness simulated explicit choice',
            'evidence_refs':decision['evidence_refs']}]})
        return {'schema_version':'modbus-compile-resume/v1','case_id':case['case_id'],'case_hash':stable_input_hash(case),
                'action':'provide-selection-decision','decision_candidate':candidate}, selected

    def test_explicit_include_is_grouped_honored_once_and_does_not_reparse(self):
        for size in (1,2):
            with self.subTest(size=size):
                folder = self.start(size)
                reply, selected = self.reply(folder, ['Temperature'])
                with patch.object(compiler, 'compile_source_descriptor', side_effect=AssertionError('source must not reparse')):
                    result = compiler.compile_user_map(None, folder, resume=reply)
                    self.assertEqual('offline-complete', result['state'])
                    self.assertEqual(result, compiler.compile_user_map(None, folder, resume=reply))
                selection = self.load(folder, 'artifacts/selection.json')
                self.assertEqual(selected, [p['oem_point_id'] for p in selection['included']])
                self.assertEqual([], selection['suggested'])
                self.assertEqual(size-1, len(selection['excluded']))
                self.assertEqual(1, len(self.load(folder,'case.json')['completed_receipts']))

    def test_explicit_exclude_all_must_not_reoffer_answered_selection(self):
        for size in (1,2):
            with self.subTest(size=size):
                folder = self.start(size)
                reply, selected = self.reply(folder, [])
                with patch.object(compiler, 'compile_source_descriptor', side_effect=AssertionError('source must not reparse')):
                    result = compiler.compile_user_map(None, folder, resume=reply)
                selection = self.load(folder,'artifacts/selection.json')
                self.assertEqual([], selection['included'])
                self.assertEqual([], selection['suggested'])
                self.assertEqual(size, len(selection['excluded']))
                self.assertEqual(1, len(self.load(folder,'case.json')['completed_receipts']))
                self.assertNotEqual('awaiting-selection-decision', result['state'])
                self.assertNotEqual('provide-selection-decision', result['next_action']['kind'])
                self.assertEqual('offline-complete', result['state'])
                self.assertEqual('no-points-selected', result['next_action']['reason'])
                self.assertEqual([], self.load(folder,'output/user-map.json')['points'])
                self.assertEqual([], list(csv.DictReader(io.StringIO((folder/'output/user-map.csv').read_text()))))
                self.assertIn('No points selected', (folder/'output/user-map.md').read_text())
                self.assertEqual(result, compiler.compile_user_map(None, folder, resume=reply))

    def test_explicit_subset_survives_later_binding_without_repeated_question(self):
        folder = self.start(2, target=True)
        reply, selected = self.reply(folder, ['Temperature'])
        with patch.object(compiler, 'compile_source_descriptor', side_effect=AssertionError('source must not reparse')):
            result = compiler.compile_user_map(None, folder, resume=reply)
            self.assertEqual('awaiting-binding', result['state'])
            before = self.load(folder,'artifacts/selection.json')
            case = self.load(folder,'case.json')
            oem = self.load(folder,'artifacts/oem-map.json')
            binding_reply = {'schema_version':'modbus-compile-resume/v1','case_id':case['case_id'],
                'case_hash':stable_input_hash(case),'action':'provide-binding',
                'binding':build_device_binding(oem,route_id='synthetic-route',unit_id=7)}
            completed = compiler.compile_user_map(None,folder,resume=binding_reply)
        after = self.load(folder,'artifacts/selection.json')
        self.assertEqual(before, after)
        self.assertEqual(selected,[p['oem_point_id'] for p in after['included']])
        self.assertNotEqual('awaiting-selection-decision',completed['state'])
        self.assertEqual(2,len(self.load(folder,'case.json')['completed_receipts']))
        self.assertEqual(['Temperature'],[p['name'] for p in self.load(folder,'output/user-map.json')['points']])
        self.assertEqual('complete', completed['state'])
        self.assertEqual(completed, compiler.compile_user_map(None,folder,resume=binding_reply))

    def test_empty_resolved_target_is_held_without_binding_or_generation(self):
        folder = self.start(2,target=True)
        reply, _ = self.reply(folder, [])
        with patch.object(compiler,'build_tool_pack',side_effect=AssertionError('no target for empty map')), \
             patch.object(compiler,'_compile_plan',side_effect=AssertionError('no plan for empty map')), \
             patch.object(compiler,'validate_device_binding',side_effect=AssertionError('no binding for empty map')):
            result = compiler.compile_user_map(None,folder,resume=reply)
        self.assertEqual('offline-complete',result['state'])
        self.assertEqual([{'target':'node-red','status':'held','reason':'no-points-selected'}],result['target_statuses'])
        self.assertEqual('none',result['next_action']['kind'])
        self.assertFalse((folder/'targets').exists())
        self.assertFalse((folder/'artifacts/read-plan.json').exists())
        self.assertFalse((folder/'control/device-binding.json').exists())
        user=self.load(folder,'output/user-map.json')
        self.assertEqual([],user['points'])
        self.assertEqual(2,len([x for x in user['exception_annex'] if x['kind']=='excluded']))

    def direct_request(self, *, coverage=None, holds=()):
        oem=build_oem_map([{'oem_point_id':'temperature','name':'Temperature','area':'input-register',
                           'protocol_offset':10,'datatype':'uint16','word_span':1,'source_refs':[{'record_id':'row-1'}]}],
                          source_hash='a'*64,source_coverage=coverage,holds=holds)
        entry={'oem_point_id':'temperature','reason':'not yet confirmed','evidence_refs':['row-1'],
               'matched_intent':'temperature','match_quality':'near'}
        return {'schema_version':'modbus-compile-request/v1','oem_map':oem,
                'selection_candidate':{'oem_map_hash':stable_input_hash(oem),'requested_measurements':['temperature'],
                                       'included':[],'suggested':[entry],'excluded':[]},'targets':[],'target_options':{}}

    def test_initial_empty_or_excluded_candidates_are_not_a_validated_exclude_reply(self):
        for disposition in ('empty','excluded'):
            with self.subTest(disposition=disposition):
                request=self.direct_request()
                entry=request['selection_candidate']['suggested'].pop()
                if disposition=='excluded':
                    entry['selection_basis']='typed-decision'
                    request['selection_candidate']['excluded']=[entry]
                result=compiler.compile_user_map(request,self.root/disposition)
                self.assertEqual('awaiting-selection-decision',result['state'])

    def test_exclude_all_does_not_clear_incomplete_coverage_or_global_hold(self):
        for label, options in (
            ('coverage',{'coverage':{'status':'unknown','discovery_complete':False,
                                     'accepted_row_count':1,'rejected_row_count':0,
                                     'quarantined_row_count':0,'detected_pages':[], 'detected_regions':[]}}),
            ('hold',{'holds':[{'code':'source.synthetic-conflict','severity':'hold','blocking':True,'message':'Global source conflict'}]}),
        ):
            with self.subTest(label=label):
                folder=self.root/label
                result=compiler.compile_user_map(self.direct_request(**options),folder)
                self.assertEqual('awaiting-selection-decision',result['state'])
                reply,_=self.reply(folder,[])
                result=compiler.compile_user_map(None,folder,resume=reply)
                self.assertEqual('partial',result['state'])
                self.assertEqual('provide-corrected-source',result['next_action']['kind'])
                self.assertEqual([],self.load(folder,'output/user-map.json')['points'])
                if label=='hold':
                    self.assertEqual('source.synthetic-conflict',self.load(folder,'output/user-map.json')['holds'][0]['code'])

    def test_invalid_exclude_reply_cannot_mutate_the_checkpoint(self):
        folder=self.start(1)
        reply,_=self.reply(folder,[])
        reply['decision_candidate']['packet_id']='b'*64
        before={str(p.relative_to(folder)):p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        with self.assertRaises(compiler.CompilerError):
            compiler.compile_user_map(None,folder,resume=reply)
        self.assertEqual(before,{str(p.relative_to(folder)):p.read_bytes() for p in folder.rglob('*') if p.is_file()})

    def test_binding_does_not_trust_a_modified_indexed_selection(self):
        folder=self.start(2,target=True)
        reply,_=self.reply(folder,['Temperature'])
        compiler.compile_user_map(None,folder,resume=reply)
        case=self.load(folder,'case.json')
        binding_reply={'schema_version':'modbus-compile-resume/v1','case_id':case['case_id'],'case_hash':stable_input_hash(case),
                       'action':'provide-binding','binding':build_device_binding(self.load(folder,'artifacts/oem-map.json'),route_id='synthetic-route',unit_id=7)}
        selection=self.load(folder,'artifacts/selection.json')
        selection['included']=[]
        (folder/'artifacts/selection.json').write_text(json.dumps(selection))
        before=(folder/'case.json').read_bytes()
        with self.assertRaises(compiler.CompilerError):
            compiler.compile_user_map(None,folder,resume=binding_reply)
        self.assertEqual(before,(folder/'case.json').read_bytes())

if __name__=='__main__':unittest.main()
