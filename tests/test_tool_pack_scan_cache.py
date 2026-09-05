"""Repeated pack validation remains content-bound and safety-equivalent."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'plugins/modbus-skills/runtime'))
from modbus_skills import tool_pack as pack
from modbus_skills.exporters import Artifact


def original_scan(content):
    text = content.decode('utf-8', errors='ignore')
    text_paths = pack._EMBEDDED_HTTP_ROUTE.sub('route', text)
    return (pack._PEM_KEY_BLOCK.search(text.upper()) is not None
            or pack._SENSITIVE_VALUE.search(text) is not None
            or 'file://' in text_paths.lower()
            or any(pattern.search(text_paths) is not None for pattern in (
                pack._EMBEDDED_WINDOWS_PATH, pack._EMBEDDED_WINDOWS_UNC,
                pack._EMBEDDED_FORWARD_UNC, pack._EMBEDDED_UNIX_PATH,
                pack._EMBEDDED_TILDE_PATH)))


class ToolPackScanCacheTests(unittest.TestCase):
    def setUp(self):
        with pack._ARTIFACT_SCAN_LOCK:
            pack._ARTIFACT_SCAN_CACHE.clear()

    def test_unchanged_bytes_are_scanned_once_but_new_bytes_are_rechecked(self):
        with patch.object(pack, '_scan_artifact_content', wraps=pack._scan_artifact_content) as scan:
            self.assertFalse(pack._artifact_content_is_unsafe(b'public synthetic map'))
            self.assertFalse(pack._artifact_content_is_unsafe(b'public synthetic map'))
            self.assertTrue(pack._artifact_content_is_unsafe(b'open /private/example/map.json'))
            self.assertTrue(pack._artifact_content_is_unsafe(b'open /private/example/map.json'))
            self.assertEqual(2, scan.call_count)

    def test_identical_unsafe_content_reports_each_actual_artifact_path(self):
        content = b'open /private/example/map.json'
        artifacts = [Artifact(name, 'text/plain', content, 'test') for name in ('one.txt', 'two.txt')]
        self.assertEqual(['one.txt', 'two.txt'], pack._find_unsafe_artifact_paths(artifacts))

    def test_mutable_bytes_are_never_cached(self):
        content = bytearray(b'public example')
        self.assertFalse(pack._artifact_content_is_unsafe(content))
        content[:] = b'open /private/example/map.json'
        self.assertTrue(pack._artifact_content_is_unsafe(content))
        self.assertFalse(pack._ARTIFACT_SCAN_CACHE)

    def test_custom_contains_cannot_bypass_original_path_scan(self):
        class CustomText(str):
            def __contains__(self, value):
                return False

        class CustomBytes(bytes):
            def decode(self, *args, **kwargs):
                return CustomText(super().decode(*args, **kwargs))

        content = CustomBytes(b'open /private/example/map.json')
        self.assertTrue(original_scan(content))
        self.assertTrue(pack._artifact_content_is_unsafe(content))
        self.assertFalse(pack._ARTIFACT_SCAN_CACHE)

    def test_cache_is_bounded_and_retains_no_artifact_content(self):
        for index in range(pack._ARTIFACT_SCAN_CACHE_LIMIT + 10):
            pack._artifact_content_is_unsafe(f'public example {index}'.encode())
        self.assertEqual(pack._ARTIFACT_SCAN_CACHE_LIMIT, len(pack._ARTIFACT_SCAN_CACHE))
        self.assertTrue(all(len(key[0]) == 32 and type(key[1]) is int
                            and type(value) is bool for key, value in pack._ARTIFACT_SCAN_CACHE.items()))

    def test_original_patterns_and_new_cached_checks_agree(self):
        corpus = [b'plain text', b'lead /lag', b'GET /modbus-dashboard',
                  b'open /private/example/map.json', b'C:\\example\\file',
                  b'\\\\server\\share', b'//server/share', b'~/example',
                  b'file:///example', b'-' * 5 + b'BEGIN ' + b'PRIVATE KEY' + b'-' * 5,
                  ('Bearer ' + 'x' * 20).encode(),
                  ('password=' + 'x' * 8).encode(), b'\xffinvalid utf8']
        rng = random.Random(51792)
        alphabet = 'abcXYZ0123 /\\~:\t\n\u2003,;[]{}()\"\''
        corpus += [''.join(rng.choice(alphabet) for _ in range(rng.randrange(80))).encode() for _ in range(500)]
        for content in corpus:
            expected = original_scan(content)
            self.assertEqual(expected, pack._artifact_content_is_unsafe(content))
            self.assertEqual(expected, pack._artifact_content_is_unsafe(content))

    def test_concurrent_checks_preserve_verdicts(self):
        values = [b'public synthetic value', b'open /private/example/map.json'] * 64
        with ThreadPoolExecutor(max_workers=4) as pool:
            actual = list(pool.map(pack._artifact_content_is_unsafe, values))
        self.assertEqual([False, True] * 64, actual)


if __name__ == '__main__':
    unittest.main()
