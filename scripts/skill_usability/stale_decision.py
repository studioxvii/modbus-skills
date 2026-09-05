"""Independent immutable-revision challenge, separate from case-file tampering.

Only fixture files and scenario prompts reach the worker. This evaluator and its
expectations remain outside its readable roots. No prose success/recovery event
can substitute for actual trusted-wrapper RPC and artifact observations.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .sessions import CodexSessionAdapter, SessionError, seed_workspace, hash_tree, _python_command

ROOT = Path(__file__).resolve().parents[2]
VERSION = 'stale-decision-oracle/v2'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def file_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and not p.is_symlink()}


def prepare_inputs(spec, directory):
    from modbus_skills.map_workflows import normalize_map
    from modbus_skills.read_plan import compile_read_plan
    facts = spec['facts']
    base = {'logical_point_id': facts['point_id'], 'name': 'Temperature',
            'route_id': facts['route_id'], 'unit_id': facts['unit_id'], 'area': facts['area'],
            'function_code': facts['function_code'], 'datatype': facts['datatype'],
            'word_span': 1, 'access': 'read-only', 'scale': 1, 'engineering_offset': 0}
    old = normalize_map([{**base, 'protocol_offset': facts['old_offset']}])
    current = normalize_map([{**base, 'protocol_offset': facts['current_offset']}])
    plan = compile_read_plan(old['points']).to_dict()
    plan['input_hashes'] = {'canonical_map': digest(old)}
    def decision(bound_map, scale, review_id, evidence):
        return {'schema_version': 'modbus-review-decisions/v1', 'canonical_map_hash': digest(bound_map),
                'review_id': review_id, 'reviewed_at': facts['reviewed_at'], 'reviewer': facts['reviewer'],
                'approve_map': True, 'hold_decisions': [], 'decisions': [{
                    'point_id': facts['point_id'], 'action': 'set', 'field': 'scale', 'value': scale,
                    'reason': 'Explicit synthetic reviewer scale for the named revision.', 'evidence_refs': [evidence]}]}
    public = {'previous-map.json': old, 'current-map.json': current, 'previous-plan.json': plan,
              'saved-review.json': decision(old, facts['old_review_scale'], 'synthetic-review-a', 'synthetic-revision-a:temperature:scale')}
    fresh = decision(current, facts['fresh_review_scale'], 'synthetic-review-b', facts['fresh_evidence_ref'])
    for name, value in public.items():
        write_json(directory / 'fixtures' / name, value)
    write_json(directory / 'future-user-input' / 'fresh-review.json', fresh)
    expected = {'version': VERSION, 'facts': copy.deepcopy(facts), 'old_map_hash': digest(old),
                'current_map_hash': digest(current), 'old_plan_hash': digest(plan),
                'initial_fixture_hashes': file_hashes(directory / 'fixtures'), 'fresh_decision_hash': digest(fresh),
                'rejection_text': 'canonical_map_hash does not match the supplied map',
                'scope': 'map-bound stale decision and rebuilding obsolete plan; not compiler packet replay or file tamper'}
    write_json(directory / 'hidden-expectations.json', expected)
    return expected


def trusted_calls(transcript, plugin, work):
    starts = {message.get('params', {}).get('item', {}).get('id'): message.get('params', {}).get('item', {})
              for message in transcript if message.get('method') == 'item/started'}
    calls = []
    for message in transcript:
        if message.get('method') != 'item/completed':
            continue
        item = message.get('params', {}).get('item', {})
        opening = starts.get(item.get('id'), {})
        if item.get('type') != 'commandExecution' or opening.get('type') != 'commandExecution' or opening.get('command') != item.get('command'):
            continue
        try:
            tokens = _python_command(item)
            cwd = Path(item.get('cwd') or work)
            if len(tokens) < 2:
                continue
            path = (cwd / tokens[1]).resolve()
            skill = next((name for name in ('apply-review', 'plan-reads')
                          if path == (plugin / 'skills' / name / 'scripts/run.py').resolve()), None)
            if not skill:
                continue
            args = tokens[2:]
            if len(args) % 2 or any(not args[i].startswith('--') for i in range(0, len(args), 2)):
                continue
            flags = dict(zip(args[::2], args[1::2]))
            if len(flags) != len(args) // 2:
                continue
            paths = {key: str((cwd / value).resolve()) for key, value in flags.items() if key != '--max-gap'}
            calls.append({'item_id': item.get('id'), 'skill': skill, 'flags': flags, 'paths': paths,
                          'exit_code': item.get('exitCode'), 'output': item.get('aggregatedOutput', '')})
        except (ValueError, OSError):
            continue
    return calls


def json_outputs(root):
    values = {}
    for path in root.rglob('*.json'):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            continue
        try:
            value = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if isinstance(value, dict):
            values[path.relative_to(root).as_posix()] = value
    return values


def evaluate_stage(stage, transcript, *, plugin, fixtures, work, expected, expected_fixture_hashes):
    calls = trusted_calls(transcript, plugin, work)
    checks = []
    def check(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})
    check('all supplied file bytes unchanged', file_hashes(fixtures) == expected_fixture_hashes)
    check('worker plugin unchanged', hash_tree(plugin) == expected['plugin_hash'])
    outputs = json_outputs(work)
    current = str((fixtures / 'current-map.json').resolve())
    if stage == 'stale':
        matching = [call for call in calls if call['skill'] == 'apply-review'
                    and call['paths'].get('--map') == current
                    and call['paths'].get('--decisions') == str((fixtures / 'saved-review.json').resolve())
                    and call['paths'].get('--output') == str((work / 'review-attempt.json').resolve())]
        check('real exact-input stale review rejection, not usage error', any(
            call['exit_code'] not in (None, 0) and expected['rejection_text'] in call['output']
            and 'usage:' not in call['output'].lower() for call in matching))
        check('no successful apply-review or plan invocation before fresh decision', not any(call['exit_code'] == 0 for call in calls))
        check('no stale reviewed artifact', not (work / 'review-attempt.json').exists())
        check('no approved map fabricated after rejection', not any(value.get('review_status') == 'approved' for value in outputs.values()))
        check('no replacement decision or plan before fresh input', not any(value.get('schema_version') in {
            'modbus-review-decisions/v1', 'modbus-read-plan/v1'} for value in outputs.values()))
    else:
        review = outputs.get('reviewed.json', {})
        plan = outputs.get('read-plan.json', {})
        apply_calls = [call for call in calls if call['skill'] == 'apply-review' and call['exit_code'] == 0
                       and call['paths'].get('--map') == current
                       and call['paths'].get('--decisions') == str((fixtures / 'fresh-review.json').resolve())
                       and call['paths'].get('--output') == str((work / 'reviewed.json').resolve())]
        plan_calls = [call for call in calls if call['skill'] == 'plan-reads' and call['exit_code'] == 0
                      and call['paths'].get('--input') == str((work / 'reviewed.json').resolve())
                      and call['paths'].get('--output') == str((work / 'read-plan.json').resolve())]
        check('real fresh review invocation', apply_calls)
        check('real rebuilt plan invocation', plan_calls)
        points = review.get('points', [])
        facts = expected['facts']
        check('one reviewed point with exact current identity and fresh scale', len(points) == 1 and all(
            points[0].get(key) == wanted for key, wanted in {
                'logical_point_id': facts['point_id'], 'route_id': facts['route_id'], 'unit_id': facts['unit_id'],
                'area': facts['area'], 'function_code': facts['function_code'], 'protocol_offset': facts['current_offset'],
                'datatype': facts['datatype'], 'scale': facts['fresh_review_scale'], 'engineering_offset': 0}.items()))
        check('approved map without blocking holds', review.get('review_status') == 'approved' and not review.get('holds'))
        check('review binds exact current map and fresh decision', review.get('input_hashes', {}).get('canonical_map_draft') == expected['current_map_hash']
              and review.get('input_hashes', {}).get('review_decisions') == expected['fresh_decision_hash']
              and review.get('approval', {}).get('input_map_hash') == expected['current_map_hash'])
        audit = review.get('review_decisions', [])
        check('fresh evidence and simulated reviewer preserved', facts['fresh_evidence_ref'] in json.dumps(audit)
              and facts['reviewer'] in json.dumps(review) and 'synthetic-review-b' in json.dumps(review)
              and 'synthetic-review-a' not in json.dumps(audit))
        check('plan hash binds exact newly reviewed map', plan.get('input_hashes', {}).get('canonical_map') == digest(review)
              and digest(review) not in (expected['old_map_hash'], expected['current_map_hash']))
        requests = plan.get('requests', [])
        check('one exact read-only new-offset request', len(requests) == 1 and all(
            requests[0].get(key) == wanted for key, wanted in {'route_id': facts['route_id'], 'unit_id': facts['unit_id'],
                'area': facts['area'], 'function_code': 3, 'start_offset': facts['current_offset'], 'quantity': 1}.items()))
        check('old plan not reused', digest(plan) != expected['old_plan_hash'])
        traces = [point for request in requests for point in request.get('points', [])]
        identity = [facts['route_id'], facts['unit_id'], facts['area'], facts['current_offset'], facts['point_id']]
        check('one exact point trace with complete width', len(traces) == 1 and traces[0].get('canonical_identity') == identity
              and traces[0].get('relative_offset') == 0 and traces[0].get('span') == 1
              and traces[0].get('protocol_offset') == facts['current_offset'])
        options = plan.get('planning_options', {})
        check('zero-gap planning policy and provenance', options.get('max_gap') == 0
              and plan.get('input_hashes', {}).get('planning_options') == digest(options))
    return {'stage': stage, 'version': VERSION, 'passed': all(item['passed'] for item in checks),
            'checks': checks, 'actual_wrapper_calls': calls}


def run_repetition(spec, prepared, expected, parent, evidence, repetition, adapter_factory=CodexSessionAdapter):
    session = None
    adapter = adapter_factory(model=spec['worker_model'], budget=spec['budget'])
    started = time.monotonic()
    deadline = started + spec['budget']['max_seconds']
    receipt = {'scenario_id': spec['scenario_id'], 'repetition': repetition, 'version': VERSION,
               'stages': [], 'status': 'failed', 'actor': 'test-harness', 'human_device_approval': False}
    destination = evidence / f'repetition-{repetition}'
    destination.mkdir(parents=True, exist_ok=False)
    try:
        scenario = {'scenario_id': spec['scenario_id'], 'entry_policy': spec['entry_policy'],
                    'fixtures': [{'path': 'fixtures/' + name} for name in expected['initial_fixture_hashes']]}
        session = seed_workspace(scenario, campaign_dir=prepared, parent=parent, plugin_source=ROOT / 'plugins/modbus-skills')
        session.state['deadline'] = deadline
        local_expected = {**expected, 'plugin_hash': session.loaded_plugin_hash}
        adapter.start(session)
        receipt['actual_model'] = session.state.get('actual_model')
        receipt['thread_id'] = session.state.get('thread_id')
        receipt['plugin_hash'] = session.loaded_plugin_hash
        for stage, prompt in (('stale', spec['prompts']['opening']), ('fresh', spec['prompts']['fresh'])):
            if stage == 'fresh':
                source = prepared / 'future-user-input/fresh-review.json'
                target = session.fixtures / 'fresh-review.json'
                if target.exists():
                    raise SessionError('fresh-decision-already-present')
                shutil.copyfile(source, target)
                receipt['fresh_decision_supplied_by'] = 'predeclared simulated reviewer; no inferred facts or human approval'
            expected_hashes = dict(expected['initial_fixture_hashes'])
            if stage == 'fresh':
                expected_hashes['fresh-review.json'] = hashlib.sha256((prepared / 'future-user-input/fresh-review.json').read_bytes()).hexdigest()
            before = len(session.state['transcript'])
            adapter.turn(session, prompt)
            words = len(session.state.get('final_text', '').split())
            observed = evaluate_stage(stage, session.state['transcript'][before:], plugin=session.plugin_root,
                fixtures=session.fixtures, work=session.work, expected=local_expected, expected_fixture_hashes=expected_hashes)
            observed['final_words'] = words
            observed['checks'].append({'name': 'scenario final-word limit', 'passed': words <= spec['max_final_words']})
            observed['checks'].append({'name': 'original cumulative deadline preserved', 'passed': session.state['deadline'] == deadline and time.monotonic() <= deadline})
            observed['passed'] = all(check['passed'] for check in observed['checks'])
            receipt['stages'].append(observed)
            shutil.copytree(adapter.snapshot(session), destination / f'{stage}-output')
        receipt['status'] = 'passed' if len(receipt['stages']) == 2 and all(stage['passed'] for stage in receipt['stages']) else 'failed'
    except Exception as exc:
        receipt['status'] = 'failed' if session and session.turn_count else 'not-run'
        receipt['error'] = str(exc)
    finally:
        if session:
            receipt['tool_calls'] = session.tool_calls
            receipt['turns'] = session.turn_count
            receipt['elapsed_seconds'] = round(time.monotonic() - started, 3)
            receipt['deadline_reset'] = session.state.get('deadline') != deadline
            try:
                write_json(destination / 'transcript.json', session.state.get('transcript', []))
                if not (destination / 'final-output').exists():
                    shutil.copytree(adapter.snapshot(session), destination / 'final-output')
            except Exception as exc:
                receipt['evidence_error'] = str(exc)
                receipt['status'] = 'failed'
            finally:
                try:
                    receipt['cleanup'] = adapter.cleanup(session)
                except Exception as exc:
                    receipt['cleanup'] = {'cleaned': False, 'error': str(exc)}
            if not receipt['cleanup'].get('cleaned'):
                receipt['status'] = 'failed'
        else:
            receipt['cleanup'] = {'cleaned': True, 'session_created': False}
        write_json(destination / 'receipt.json', receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', required=True, help='Explicit actual-model identifier; never inherit the representative campaign default')
    parser.add_argument('--frozen', action='store_true')
    args = parser.parse_args(argv)
    out = args.output.resolve()
    if not args.frozen:
        if out.exists():
            parser.error('output already exists; preserve previous evidence')
        if not out.is_relative_to(ROOT / 'artifacts'):
            parser.error('output must be under the ignored repository artifacts directory')
        frozen = out / 'frozen'
        frozen.mkdir(parents=True)
        shutil.copytree(ROOT / 'plugins/modbus-skills', frozen / 'plugins/modbus-skills', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copytree(ROOT / 'scripts/skill_usability', frozen / 'scripts/skill_usability', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copyfile(ROOT / 'scripts/run_stale_decision_challenge.py', frozen / 'scripts/run_stale_decision_challenge.py')
        shutil.copyfile(ROOT / 'scripts/run_direct_skill_acceptance.py', frozen / 'scripts/run_direct_skill_acceptance.py')
        target = frozen / 'tests/skill_usability/stale-decision.json'
        target.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'tests/skill_usability/stale-decision.json', target)
        write_json(out / 'frozen-source-hashes.json', file_hashes(frozen))
        return subprocess.call([sys.executable, str(frozen / 'scripts/run_stale_decision_challenge.py'), '--frozen', '--output', str(out), '--model', args.model])
    spec = json.loads((ROOT / 'tests/skill_usability/stale-decision.json').read_text())
    spec['worker_model'] = args.model
    prepared = out / 'prepared'
    expected = prepare_inputs(spec, prepared)
    write_json(out / 'preflight.json', {'scenario': spec, 'evaluator_version': VERSION,
        'frozen_source_hashes_sha256': hashlib.sha256((out / 'frozen-source-hashes.json').read_bytes()).hexdigest(),
        'prepared_hashes': file_hashes(prepared), 'expectations_hidden': True,
        'worker_receives': 'plugin, current stage fixture files and prompt only',
        'official_app_server_docs': 'https://learn.chatgpt.com/docs/app-server'})
    parent = out / 'sessions'
    parent.mkdir()
    results = []
    for repetition in range(1, spec['repetitions'] + 1):
        result = run_repetition(spec, prepared, expected, parent, out / 'trials', repetition)
        results.append(result)
        print(json.dumps({'repetition': repetition, 'status': result['status'], 'tool_calls': result.get('tool_calls'), 'error': result.get('error')}), flush=True)
    # Runtime imports can create bytecode only in the evaluator process; ignore
    # cache files when checking immutable source bytes, never worker artifacts.
    frozen_unchanged = all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == sha
                           for path, sha in json.loads((out / 'frozen-source-hashes.json').read_text()).items())
    summary = {'scenario_id': spec['scenario_id'], 'version': VERSION, 'actual_model_repetitions': len(results),
               'frozen_sources_unchanged': frozen_unchanged, 'results': results,
               'status': 'passed' if frozen_unchanged and all(r['status'] == 'passed' for r in results) else 'failed'}
    write_json(out / 'summary.json', summary)
    return 0 if summary['status'] == 'passed' else 1
