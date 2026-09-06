"""The review wrapper must pass the supplied PDF location to native readers."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))
sys.path.insert(0, str(ROOT / 'tests'))
from modbus_skills.cli import run_cli
from modbus_skills.map_workflows import diagnose_map
from test_pdf_merged_cell_proof import draw_pdf


class ReviewMapPdfSourcePathTests(unittest.TestCase):
    def invoke(self, source, output, *extra):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = run_cli('review-map', ['--input', str(source), '--output', str(output), *extra])
        self.assertEqual(0, code, stderr.getvalue())
        self.assertEqual('', stderr.getvalue())
        return json.loads(stdout.getvalue())

    def assert_pdf_review(self, source, output):
        parsed = json.loads((output / 'parsed.json').read_text())
        draft = json.loads((output / 'map-draft.json').read_text())
        review = json.loads((output / 'review.json').read_text())
        self.assertEqual(['0', '1'], [r['source_register'] for r in parsed['records']])
        self.assertEqual(['First quantity', 'Second quantity'], [p['name'] for p in draft['points']])
        self.assertEqual(['uint16', 'uint16'], [p['datatype'] for p in draft['points']])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), parsed['source']['sha256'])
        self.assertEqual(source.name, parsed['source']['filename'])
        self.assertNotIn('pdf-text-extraction-failed', [h['code'] for h in review['holds']])
        self.assertEqual('blocked', review['review_status'])
        self.assertTrue(all(p.get('unit_id') is None for p in draft['points']))
        self.assertTrue(all(p.get('byte_order_confirmed') is not True for p in draft['points']))
        self.assertEqual(2, len(review['items']))

    def test_absolute_pdf_outside_cwd_preserves_spaces_and_unicode(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / 'source café map.pdf'
            draw_pdf(source)
            original = source.read_bytes()
            cwd = base / 'unrelated'
            cwd.mkdir()
            with contextlib.chdir(cwd):
                self.invoke(source, base / 'out')
            self.assert_pdf_review(source, base / 'out')
            self.assertEqual(original, source.read_bytes())

    def test_relative_parent_path_and_same_named_cwd_decoy(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / 'source map.pdf'
            draw_pdf(source)
            cwd = base / 'different cwd'
            cwd.mkdir()
            decoy = cwd / source.name
            decoy.write_bytes(b'not the supplied PDF')
            with contextlib.chdir(cwd):
                self.invoke(Path('..') / source.name, base / 'out')
            self.assert_pdf_review(source, base / 'out')
            self.assertEqual(b'not the supplied PDF', decoy.read_bytes())

    def test_pdf_magic_with_non_pdf_extension_keeps_location(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / 'register payload.bin'
            draw_pdf(source)
            self.invoke(source, base / 'out')
            self.assert_pdf_review(source, base / 'out')

    def test_structured_bytes_dispatch_remains_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / 'source résumé.csv'
            source.write_text('Protocol Offset,Name,Datatype,Access\n0,First quantity,uint16,R\n')
            with mock.patch('modbus_skills.cli.diagnose_map', wraps=diagnose_map) as routed:
                self.invoke(source, base / 'out')
            self.assertEqual(source.read_bytes(), routed.call_args.args[0])
            draft = json.loads((base / 'out/map-draft.json').read_text())
            self.assertEqual('First quantity', draft['points'][0]['name'])
            # The source does not identify an area; path repair must not
            # turn its source token into a complete physical identity.
            self.assertIsNone(draft['points'][0]['protocol_offset'])
            self.assertIsNone(draft['points'][0]['area'])

    def test_existing_bytes_pdf_api_keeps_explicit_filename_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'bytes-only.pdf'
            draw_pdf(source)
            data = source.read_bytes()
            result = diagnose_map(data, filename=str(source))
            self.assertEqual(['0', '1'], [r['source_register'] for r in result['parsed']['records']])
            self.assertEqual(data, source.read_bytes())


if __name__ == '__main__':
    unittest.main()
