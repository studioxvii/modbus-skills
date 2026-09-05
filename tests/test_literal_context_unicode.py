"""Malformed UTF-8 literal is a clean validation error before hashing."""
import unittest
from unittest.mock import patch
from test_literal_context_preflight import entry, registry, user_map, UserMapError

class LiteralContextUnicodeTests(unittest.TestCase):
    def test_unpaired_surrogate_is_clean_error_before_identity_hash(self):
        for imported in (False, True):
            with self.subTest(imported=imported), patch.object(user_map, 'stable_input_hash') as hashed:
                with self.assertRaises(UserMapError):
                    if imported:
                        user_map._selected_literal_source_context(registry('\ud800'), {'point'})
                    else:
                        user_map.build_literal_source_context([{**entry(), 'literal':'\ud800'}])
                hashed.assert_not_called()
