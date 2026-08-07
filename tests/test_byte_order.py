import math
import unittest

from modbus_skills.byte_order import (
    RawSample,
    all_modbus_layouts,
    candidate_for,
    evaluate_byte_orders,
)
from modbus_skills.models import DataType


class ByteOrderEvaluationTests(unittest.TestCase):
    def test_32_bit_layouts_are_the_four_expected_combinations(self):
        self.assertEqual(
            all_modbus_layouts(32),
            ("ABCD", "BADC", "CDAB", "DCBA"),
        )

    def test_16_bit_sample_evaluates_ab_and_ba_without_selecting_a_winner(self):
        result = evaluate_byte_orders(RawSample("word", (0x1234,)))

        self.assertEqual(all_modbus_layouts(16), ("AB", "BA"))
        self.assertEqual(len(result.candidates), 4)
        self.assertEqual(candidate_for(result, "AB", "uint16").decoded_value, 0x1234)
        self.assertEqual(candidate_for(result, "BA", "uint16").decoded_value, 0x3412)
        self.assertNotIn("winner", result.to_dict())

    def test_one_sample_produces_every_32_bit_type_and_layout(self):
        sample = RawSample("sample-001", (0x3F80, 0x0000))
        result = evaluate_byte_orders(sample)

        self.assertEqual(len(result.candidates), 12)
        self.assertEqual({item.sample_id for item in result.candidates}, {"sample-001"})
        self.assertEqual(
            candidate_for(result, "ABCD", DataType.FLOAT32).decoded_value,
            1.0,
        )
        self.assertNotIn("selected_layout", result.to_dict())
        self.assertNotIn("winner", result.to_dict())

    def test_single_string_type_and_layout_are_not_treated_as_character_iterables(self):
        result = evaluate_byte_orders(
            RawSample("scalar-options", (0x3F80, 0)),
            datatypes="float32",
            layouts="ABCD",
        )
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].decoded_value, 1.0)

    def test_scaling_occurs_after_signed_decode(self):
        sample = RawSample("signed", (0xFFFF, 0xFFFE))
        result = evaluate_byte_orders(
            sample,
            datatypes=(DataType.INT32,),
            layouts=("ABCD",),
            scale=2,
            engineering_offset=10,
        )
        candidate = result.candidates[0]

        self.assertEqual(candidate.decoded_value, -2)
        self.assertEqual(candidate.scaled_value, 6)

    def test_each_32_bit_layout_reorders_the_same_immutable_words(self):
        sample = RawSample("layout", (0x0102, 0x0304))
        result = evaluate_byte_orders(
            sample, datatypes=("uint32",), scale=1, engineering_offset=0
        )
        values = {item.layout: item.ordered_hex for item in result.candidates}

        self.assertEqual(
            values,
            {
                "ABCD": "01020304",
                "BADC": "02010403",
                "CDAB": "03040102",
                "DCBA": "04030201",
            },
        )
        self.assertEqual(sample.words, (0x0102, 0x0304))

    def test_64_bit_defaults_are_explicit_and_exhaustive_for_word_orders(self):
        layouts = all_modbus_layouts(64)

        self.assertEqual(len(layouts), 48)
        self.assertEqual(len(set(layouts)), 48)
        self.assertIn("ABCDEFGH", layouts)
        self.assertIn("BADCFEHG", layouts)
        self.assertIn("GHEFCDAB", layouts)
        self.assertIn("HGFEDCBA", layouts)

    def test_explicit_64_bit_layout_decodes_float_and_integers(self):
        sample = RawSample("double", (0x3FF0, 0x0000, 0x0000, 0x0000))
        result = evaluate_byte_orders(
            sample,
            datatypes=("float64", "uint64", "int64"),
            layouts=("ABCDEFGH", "GHEFCDAB"),
        )

        self.assertEqual(candidate_for(result, "ABCDEFGH", "float64").decoded_value, 1.0)
        self.assertEqual(len(result.candidates), 6)

    def test_any_explicit_full_64_bit_permutation_can_be_requested(self):
        sample = RawSample("custom", (0x0102, 0x0304, 0x0506, 0x0708))
        result = evaluate_byte_orders(
            sample,
            datatypes=("uint64",),
            layouts=("ACEGBDFH",),
        )

        self.assertEqual(result.candidates[0].ordered_hex, "0103050702040608")

    def test_ieee_754_float32_special_values_are_classified(self):
        cases = (
            ((0x7FC0, 0x0000), "nan"),
            ((0x7F80, 0x0000), "positive-infinity"),
            ((0xFF80, 0x0000), "negative-infinity"),
            ((0x0000, 0x0001), "subnormal"),
            ((0x8000, 0x0000), "negative-zero"),
        )
        for words, expected in cases:
            with self.subTest(expected=expected):
                result = evaluate_byte_orders(
                    RawSample(expected, words),
                    datatypes=("float32",),
                    layouts=("ABCD",),
                )
                self.assertEqual(result.candidates[0].classification, expected)

    def test_ieee_754_float64_special_values_are_classified(self):
        cases = (
            ((0x7FF8, 0, 0, 0), "nan"),
            ((0x7FF0, 0, 0, 0), "positive-infinity"),
            ((0x0000, 0, 0, 1), "subnormal"),
        )
        for words, expected in cases:
            with self.subTest(expected=expected):
                result = evaluate_byte_orders(
                    RawSample(f"f64-{expected}", words),
                    datatypes=("float64",),
                    layouts=("ABCDEFGH",),
                )
                self.assertEqual(result.candidates[0].classification, expected)

    def test_non_finite_values_are_json_safe_strings(self):
        result = evaluate_byte_orders(
            RawSample("infinity", (0x7F80, 0)),
            datatypes=("float32",),
            layouts=("ABCD",),
        ).to_dict()
        self.assertEqual(result["candidates"][0]["decoded_value"], "Infinity")

    def test_invalid_samples_and_layouts_fail_closed(self):
        with self.assertRaises(ValueError):
            RawSample("three-word", (1, 2, 3))
        with self.assertRaises(ValueError):
            RawSample("bad-word", (0, 65_536))
        with self.assertRaises(ValueError):
            evaluate_byte_orders(
                RawSample("bad-layout", (1, 2)), layouts=("AABC",)
            )
        with self.assertRaises(ValueError):
            evaluate_byte_orders(
                RawSample("bad-type", (1, 2)), datatypes=("uint64",)
            )
        with self.assertRaises(ValueError):
            evaluate_byte_orders(
                RawSample("bad-scale", (1, 2)), scale=math.inf
            )


if __name__ == "__main__":
    unittest.main()
