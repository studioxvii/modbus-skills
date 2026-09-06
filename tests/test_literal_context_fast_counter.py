"""Prospective JSON allowance parity and finite escape-only counting controls."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))
from modbus_skills import user_map

def expected(value):
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False)
    return len(encoded.encode()) + 32 * (encoded.count('\n') + 1) + 256

class LiteralContextFastCounterTests(unittest.TestCase):
    def test_exact_json_cost_all_escape_classes_and_keys(self):
        values = [chr(i) for i in range(256)] + ['', 'x' * 16384, '\u2028', '\ud800', '\udfff', '\uffff', '\U00010000', '\U0010ffff', '\\"\b\f\n\r\t\x00\x7f\u00e9\U0001f642']
        for text in values:
            for value in (text, {text: text}, {'oem_point_id': 'point', 'source_ref': {'record_id': text}}):
                with self.subTest(text=repr(text)[:40], shape=type(value).__name__):
                    self.assertEqual(expected(value), user_map._literal_context_size(value))

    def test_exact_and_one_under_budget(self):
        for value in ('', 'x' * 1000, '\\"\n\x00\x7f\u00e9\U0001f642', {'source_ref': {'record_id': 'csv:2'}}):
            cost = expected(value)
            with self.subTest(value=repr(value)[:40]):
                with patch.object(user_map, '_LITERAL_CONTEXT_BYTES', cost * 2):
                    self.assertEqual(cost, user_map._literal_context_size(value))
                with patch.object(user_map, '_LITERAL_CONTEXT_BYTES', cost * 2 - 1):
                    with self.assertRaisesRegex(user_map.UserMapError, '4 MiB evidence budget'):
                        user_map._literal_context_size(value)

    def test_oversized_minimum_rejects_before_escape_scan_or_serialization(self):
        for value in ('x' * 10000, '\n' * 10000):
            with self.subTest(escaped=value[0] == '\n'), patch.object(user_map, '_LITERAL_CONTEXT_BYTES', 1024), patch.object(user_map.re, 'finditer', side_effect=AssertionError('escape scan reached')), patch.object(json, 'dumps', side_effect=AssertionError('serialized')), patch.object(user_map, 'stable_input_hash', side_effect=AssertionError('hashed')):
                with self.assertRaisesRegex(user_map.UserMapError, 'budget'):
                    user_map._literal_context_size(value)

    def test_escape_iterator_only_visits_expanding_characters(self):
        original = user_map.re.finditer
        for value, count in [('x' * 10000, 0), ('x' * 5000 + '\n\x7f\U0001f642' + 'y' * 5000, 3)]:
            visited = []
            calls = []
            def spy(pattern, text):
                calls.append(True)
                for match in original(pattern, text):
                    visited.append(match.group())
                    yield match
            with self.subTest(count=count), patch.object(user_map.re, 'finditer', side_effect=spy):
                actual = user_map._literal_context_size(value)
            self.assertEqual(expected(value), actual)
            self.assertEqual(1, len(calls))
            self.assertEqual(count, len(visited))

    def test_escape_budget_failure_stops_iteration_early(self):
        original = user_map.re.finditer
        visited = []
        def spy(pattern, text):
            for match in original(pattern, text):
                visited.append(match.group())
                yield match
        # 288 base + 2 quotes + 100 minimum characters = 390; only five
        # extra bytes remain, so the sixth short escape must stop iteration.
        with patch.object(user_map, '_LITERAL_CONTEXT_BYTES', 790), patch.object(user_map.re, 'finditer', side_effect=spy):
            with self.assertRaisesRegex(user_map.UserMapError, 'budget'):
                user_map._literal_context_size('\n' * 100)
        self.assertEqual(6, len(visited))

    def test_string_subclass_retains_original_count_not_overridden_length(self):
        class OddLength(str):
            def __len__(self): return 0
        value = OddLength('hello\n\x7f')
        with patch.object(user_map.re, 'finditer', side_effect=AssertionError('custom string fast path')):
            self.assertEqual(expected(str(value)), user_map._literal_context_size(value))

if __name__ == '__main__': unittest.main()
