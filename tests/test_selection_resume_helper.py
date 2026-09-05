from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))
from modbus_skills import selection_resume
from modbus_skills.compiler import compile_user_map, inspect_compile_case
from test_compiler import request, selection_pause_request


class SelectionResumeHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'case'
        compile_user_map(selection_pause_request(), self.root)
        self.inspection = inspect_compile_case(self.root)
        self.kwargs = {'expected_case_hash':self.inspection['case_hash'],
                       'include':['temperature'], 'reason':'Test-harness choice: temperature only.'}

    def snapshot(self):
        return {str(p.relative_to(self.root)):(p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob('*') if p.is_file()}

    def apply_reply(self, **options):
        reply = selection_resume.prepare_selection_resume(self.root, **(options or self.kwargs))
        return compile_user_map(None, self.root, resume=reply)

    def test_include_reuses_finished_source_work_and_compiler_hash_validation(self):
        before = self.snapshot()
        supplied = selection_resume.prepare_selection_resume(self.root / 'case.json', **self.kwargs)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(supplied, selection_resume.prepare_selection_resume(self.root, **self.kwargs))
        result = compile_user_map(None, self.root, resume=supplied)
        self.assertEqual('offline-complete', result['state'])
        self.assertEqual(self.inspection['case_hash'], supplied['case_hash'])
        self.assertEqual(self.inspection['active_packet']['input_hashes'], supplied['decision_candidate']['input_hashes'])
        user = json.loads((self.root / 'output/user-map.json').read_text())
        self.assertEqual(['Temperature'], [p['name'] for p in user['points']])
        self.assertEqual([], user['holds'])
        after = self.snapshot()
        case_before = json.loads(before['case.json'][0])
        for name in ('oem_map', 'source'):
            if name in case_before['artifacts']:
                path = case_before['artifacts'][name]['path']
                self.assertEqual(before[path], after[path])

    def test_explicit_exclude_all_selects_nothing(self):
        result = self.apply_reply(expected_case_hash=self.inspection['case_hash'],
                                  exclude_all=True, reason='Test-harness choice: exclude all.')
        self.assertNotEqual('awaiting-selection-decision', result['state'])
        user = json.loads((self.root / 'output/user-map.json').read_text())
        self.assertEqual([], user['points'])

    def test_invalid_choices_do_not_mutate(self):
        variants = [{'include':[]}, {'include':['temperature'],'exclude_all':True},
                    {'include':'temperature'}, {'include':None}, {'include':['not-offered']},
                    {'include':[5]}, {'reason':''}, {'reason':'   '}, {'exclude_all':1},
                    {'expected_case_hash':'0'*64}]
        for changes in variants:
            with self.subTest(changes=changes):
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    selection_resume.prepare_selection_resume(self.root, **{**self.kwargs, **changes})
                self.assertEqual(before, self.snapshot())

    def test_changed_indexed_artifact_is_not_repaired(self):
        case = json.loads((self.root / 'case.json').read_text())
        (self.root / case['artifacts']['oem_map']['path']).write_text('{}')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'stale'):
            selection_resume.prepare_selection_resume(self.root, **self.kwargs)
        self.assertEqual(before, self.snapshot())

    def test_complete_state_and_stale_repeat_are_rejected_without_mutation(self):
        self.apply_reply()
        before = self.snapshot()
        with self.assertRaises(ValueError):
            selection_resume.prepare_selection_resume(self.root, **self.kwargs)
        fresh = inspect_compile_case(self.root)
        with self.assertRaisesRegex(ValueError, 'active grouped selection'):
            selection_resume.prepare_selection_resume(self.root, **{**self.kwargs,'expected_case_hash':fresh['case_hash']})
        self.assertEqual(before, self.snapshot())

    def test_unsupported_packet_shape_or_phase_never_calls_compiler(self):
        for variation in ('multiple','phase','action'):
            inspection = copy.deepcopy(self.inspection)
            if variation == 'multiple':
                inspection['active_packet']['decisions'] *= 2
            elif variation == 'phase':
                inspection['active_packet']['phase'] = 'source'
            else:
                inspection['next_action']['kind'] = 'provide-binding'
            with self.subTest(variation=variation), \
                 patch.object(selection_resume,'inspect_compile_case',return_value=inspection):
                with self.assertRaises(ValueError):
                    selection_resume.prepare_selection_resume(self.root, **self.kwargs)

    def test_symlink_case_rejected(self):
        alias = Path(self.temp.name) / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            selection_resume.prepare_selection_resume(alias, **self.kwargs)
        self.assertEqual(before, self.snapshot())

    def test_cli_success_and_structured_stale_error(self):
        reply = Path(self.temp.name) / 'reply.json'
        command = [sys.executable,'-B',str(ROOT / 'plugins/modbus-skills/skills/compile-user-map/scripts/prepare_selection.py'),
                   str(self.root),'--case-hash',self.inspection['case_hash'],'--include','temperature',
                   '--reason','Test-harness choice: temperature only.','--output',str(reply)]
        before = self.snapshot()
        good = subprocess.run(command,capture_output=True,text=True,timeout=10)
        self.assertEqual(0,good.returncode,good.stderr)
        self.assertEqual('prepared',json.loads(good.stdout)['status'])
        self.assertEqual(before,self.snapshot())
        run = subprocess.run([sys.executable,'-B',str(ROOT / 'plugins/modbus-skills/skills/compile-user-map/scripts/run.py'),
                              '--case',str(self.root),'--resume',str(reply)],capture_output=True,text=True,timeout=10)
        self.assertEqual(0,run.returncode,run.stderr)
        self.assertEqual('offline-complete',json.loads(run.stdout)['status'])
        before = self.snapshot()
        bad = subprocess.run(command,capture_output=True,text=True,timeout=10)
        self.assertEqual(1,bad.returncode)
        self.assertEqual('',bad.stdout)
        self.assertEqual('selection-reply-invalid',json.loads(bad.stderr)['code'])
        self.assertEqual(before,self.snapshot())

    def test_cli_rejects_existing_symlink_and_inside_case_reply(self):
        output = Path(self.temp.name) / 'foreign.json'
        output.write_text('foreign data')
        alias = Path(self.temp.name) / 'alias.json'
        alias.symlink_to(output)
        for destination in (output, alias, self.root / 'unexpected.json'):
            with self.subTest(destination=destination):
                before = self.snapshot()
                command = [sys.executable,'-B',str(ROOT / 'plugins/modbus-skills/skills/compile-user-map/scripts/prepare_selection.py'),
                           str(self.root),'--case-hash',self.inspection['case_hash'],'--include','temperature',
                           '--reason','Test-harness choice.','--output',str(destination)]
                result = subprocess.run(command,capture_output=True,text=True,timeout=10)
                self.assertEqual(1,result.returncode)
                self.assertEqual(before,self.snapshot())
                self.assertEqual('foreign data',output.read_text())


if __name__ == '__main__':
    unittest.main()
