from __future__ import annotations

import copy
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from skill_usability import stale_decision as challenge
from skill_usability.sessions import SessionAdapter, SessionError, hash_tree


class StaleDecisionChallengeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = json.loads((ROOT / 'tests/skill_usability/stale-decision.json').read_text())
        self.spec['worker_model'] = 'test-only-no-model-call'
        self.prepared = self.root / 'prepared'
        self.expected = challenge.prepare_inputs(self.spec, self.prepared)
        self.fixtures = self.prepared / 'fixtures'
        self.work = self.root / 'work'; self.work.mkdir()
        self.plugin = ROOT / 'plugins/modbus-skills'
        self.expected['plugin_hash'] = hash_tree(self.plugin)

    def command(self, skill, flags, item_id='test-command'):
        argv = [sys.executable, str(self.plugin / 'skills' / skill / 'scripts/run.py'), *flags]
        run = subprocess.run(argv, cwd=self.work, text=True, capture_output=True)
        item = {'id': item_id, 'type': 'commandExecution', 'command': shlex.join(argv), 'cwd': str(self.work)}
        return [{'method': 'item/started', 'params': {'item': item}},
                {'method': 'item/completed', 'params': {'item': {**item, 'exitCode': run.returncode,
                    'aggregatedOutput': run.stdout + run.stderr}}}]

    def stage(self, name, transcript):
        return challenge.evaluate_stage(name, transcript, plugin=self.plugin, fixtures=self.fixtures,
            work=self.work, expected=self.expected, expected_fixture_hashes=challenge.file_hashes(self.fixtures))

    def stale_call(self):
        return self.command('apply-review', ['--map', str(self.fixtures / 'current-map.json'), '--decisions',
            str(self.fixtures / 'saved-review.json'), '--output', str(self.work / 'review-attempt.json')])

    def test_real_runtime_stale_rejection_then_fresh_review_and_plan(self):
        stale = self.stale_call()
        self.assertTrue(self.stage('stale', stale)['passed'])
        shutil.copyfile(self.prepared / 'future-user-input/fresh-review.json', self.fixtures / 'fresh-review.json')
        fresh = self.command('apply-review', ['--map', str(self.fixtures / 'current-map.json'), '--decisions',
            str(self.fixtures / 'fresh-review.json'), '--output', str(self.work / 'reviewed.json')], 'fresh')
        fresh += self.command('plan-reads', ['--input', str(self.work / 'reviewed.json'), '--output',
            str(self.work / 'read-plan.json'), '--max-gap', '0'], 'plan')
        result = self.stage('fresh', fresh)
        self.assertTrue(result['passed'], result)
        # An obsolete plan or altered engineering value cannot ride a real receipt.
        original = (self.work / 'read-plan.json').read_bytes()
        shutil.copyfile(self.fixtures / 'previous-plan.json', self.work / 'read-plan.json')
        self.assertFalse(self.stage('fresh', fresh)['passed'])
        (self.work / 'read-plan.json').write_bytes(original)
        plan = json.loads(original); plan['requests'][0]['points'][0]['canonical_identity'][-1] = 'wrong-point'
        challenge.write_json(self.work / 'read-plan.json', plan)
        self.assertFalse(self.stage('fresh', fresh)['passed'])
        (self.work / 'read-plan.json').write_bytes(original)
        review = json.loads((self.work / 'reviewed.json').read_text()); review['points'][0]['scale'] = 0.1
        challenge.write_json(self.work / 'reviewed.json', review)
        self.assertFalse(self.stage('fresh', fresh)['passed'])

    def test_forged_final_message_usage_error_and_unpaired_rpc_never_pass(self):
        self.assertFalse(self.stage('stale', [{'method': 'item/completed', 'params': {'item': {
            'type': 'agentMessage', 'text': 'Rejected stale review; canonical_map_hash does not match the supplied map'}}}])['passed'])
        transcript = self.stale_call()
        fake = copy.deepcopy(transcript)
        fake[-1]['params']['item']['aggregatedOutput'] = 'usage: apply-review\ncanonical_map_hash does not match the supplied map'
        self.assertFalse(self.stage('stale', fake)['passed'])
        self.assertFalse(self.stage('stale', transcript[1:])['passed'])
        fake = copy.deepcopy(transcript)
        fake[0]['params']['item']['command'] = 'echo fake'
        self.assertFalse(self.stage('stale', fake)['passed'])

    def test_copied_or_rebound_decision_not_exact_stale_input(self):
        transcript = self.stale_call()
        for message in transcript:
            message['params']['item']['command'] = message['params']['item']['command'].replace('saved-review.json', 'replacement-review.json')
        self.assertFalse(self.stage('stale', transcript)['passed'])
        challenge.write_json(self.work / 'fabricated.json', {'review_status': 'approved'})
        self.assertFalse(self.stage('stale', self.stale_call())['passed'])

    def test_replacement_decision_before_fresh_user_input_is_rejected(self):
        transcript = self.stale_call()
        challenge.write_json(self.work / 'replacement.json', {'schema_version': 'modbus-review-decisions/v1'})
        self.assertFalse(self.stage('stale', transcript)['passed'])

    def test_fixture_changes_are_detected_and_fresh_facts_initially_hidden(self):
        self.assertFalse((self.fixtures / 'fresh-review.json').exists())
        self.assertNotEqual(self.expected['old_map_hash'], self.expected['current_map_hash'])
        transcript = self.stale_call()
        (self.fixtures / 'previous-plan.json').write_text('{}')
        result = challenge.evaluate_stage('stale', transcript, plugin=self.plugin, fixtures=self.fixtures,
            work=self.work, expected=self.expected, expected_fixture_hashes=self.expected['initial_fixture_hashes'])
        self.assertFalse(result['passed'])

    def test_runner_requires_explicit_model(self):
        with self.assertRaises(SystemExit) as raised:
            challenge.main(['--output', str(self.root / 'unstarted')])
        self.assertEqual(2, raised.exception.code)
        self.assertFalse((self.root / 'unstarted').exists())

    def test_deadline_never_reset_and_cleanup_even_snapshot_failure(self):
        seen = []
        class BrokenAdapter(SessionAdapter):
            def __init__(self, **kwargs): pass
            def start(self, session):
                seen.append(session.state['deadline']); session.state['transcript'] = []; return session
            def turn(self, session, prompt):
                session.turn_count += 1
                self_test.assertEqual(seen[0], session.state['deadline'])
                raise SessionError('budget-exceeded')
            def snapshot(self, session): raise RuntimeError('snapshot unavailable')
            def cleanup(self, session):
                seen.append('cleaned'); return super().cleanup(session)
        self_test = self
        parent = self.root / 'sessions'; parent.mkdir()
        result = challenge.run_repetition(self.spec, self.prepared, self.expected, parent,
            self.root / 'evidence', 1, adapter_factory=BrokenAdapter)
        self.assertEqual('failed', result['status'])
        self.assertEqual('budget-exceeded', result['error'])
        self.assertFalse(result['deadline_reset'])
        self.assertTrue(result['cleanup']['cleaned'])
        self.assertIn('cleaned', seen)
        self.assertEqual([], list(parent.iterdir()))


if __name__ == '__main__':
    unittest.main()
