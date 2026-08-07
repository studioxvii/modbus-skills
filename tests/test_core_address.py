import unittest

from modbus_skills.address import format_modicon_reference, resolve_address
from modbus_skills.models import AddressConvention, FindingSeverity, RegisterArea


class AddressResolutionTests(unittest.TestCase):
    def test_protocol_offsets_include_both_protocol_boundaries(self):
        for value in (0, 65_535, "0xFFFF"):
            with self.subTest(value=value):
                result = resolve_address(
                    value, AddressConvention.PROTOCOL_OFFSET, RegisterArea.HOLDING_REGISTER
                )
                self.assertTrue(result.resolved)
        self.assertEqual(
            resolve_address(0, "protocol-offset", "holding-register").protocol_offset,
            0,
        )
        self.assertEqual(
            resolve_address("0xFFFF", "protocol-offset", "holding-register").protocol_offset,
            65_535,
        )

    def test_one_based_offsets_convert_without_changing_the_source(self):
        first = resolve_address("1", "one-based-offset", "input-register")
        last = resolve_address(65_536, "one-based-offset", "input-register")

        self.assertEqual(first.protocol_offset, 0)
        self.assertEqual(first.source_address.raw, "1")
        self.assertEqual(last.protocol_offset, 65_535)

    def test_five_digit_modicon_references_use_explicit_areas(self):
        cases = (
            ("00001", RegisterArea.COIL),
            ("10001", RegisterArea.DISCRETE_INPUT),
            ("30001", RegisterArea.INPUT_REGISTER),
            ("40001", RegisterArea.HOLDING_REGISTER),
        )
        for raw, area in cases:
            with self.subTest(raw=raw):
                result = resolve_address(raw, "modicon-reference", area)
                self.assertTrue(result.resolved)
                self.assertEqual(result.protocol_offset, 0)

    def test_six_digit_reference_supports_upper_protocol_offset(self):
        raw = format_modicon_reference(
            RegisterArea.HOLDING_REGISTER, 65_535, width=6
        )
        result = resolve_address(raw, "modicon-reference", "holding-register")

        self.assertEqual(raw, "465536")
        self.assertTrue(result.resolved)
        self.assertEqual(result.protocol_offset, 65_535)

    def test_automatic_format_uses_six_digits_when_five_are_ambiguous(self):
        self.assertEqual(format_modicon_reference("input-register", 9_998), "39999")
        self.assertEqual(format_modicon_reference("input-register", 9_999), "310000")

    def test_area_prefix_mismatch_is_an_error_not_an_inference(self):
        result = resolve_address("40001", "modicon-reference", "input-register")

        self.assertFalse(result.resolved)
        self.assertIsNone(result.protocol_offset)
        self.assertEqual(result.area, RegisterArea.INPUT_REGISTER)
        self.assertEqual(result.findings[0].severity, FindingSeverity.ERROR)

    def test_unknown_area_and_convention_create_holds(self):
        result = resolve_address("40001", None, None)

        self.assertFalse(result.resolved)
        self.assertEqual(result.source_address.raw, "40001")
        self.assertEqual(
            {finding.code for finding in result.findings},
            {"address.area-unresolved", "address.convention-unresolved"},
        )
        self.assertTrue(
            all(finding.severity is FindingSeverity.HOLD for finding in result.findings)
        )

    def test_invalid_boundaries_are_errors(self):
        invalid_cases = (
            (-1, "protocol-offset"),
            (65_536, "protocol-offset"),
            (0, "one-based-offset"),
            (65_537, "one-based-offset"),
        )
        for value, convention in invalid_cases:
            with self.subTest(value=value, convention=convention):
                result = resolve_address(value, convention, "holding-register")
                self.assertFalse(result.resolved)
                self.assertEqual(result.findings[0].code, "address.invalid")

    def test_coil_integer_reference_accounts_for_lost_leading_zero(self):
        result = resolve_address(1, "modicon-reference", "coil")
        self.assertTrue(result.resolved)
        self.assertEqual(result.protocol_offset, 0)

    def test_five_digit_format_rejects_offsets_that_cross_the_area_prefix(self):
        with self.assertRaises(ValueError):
            format_modicon_reference("holding-register", 10_000, width=5)


if __name__ == "__main__":
    unittest.main()
