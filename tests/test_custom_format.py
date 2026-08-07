from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))

from modbus_skills.custom_format import (  # noqa: E402
    CustomFormatError,
    render_custom_format,
    validate_custom_format,
)
from modbus_skills.artifacts import artifact_envelope  # noqa: E402


class CustomFormatTests(unittest.TestCase):
    def test_declarative_render_with_metadata_and_field_map(self) -> None:
        config = {
            "name": "synthetic",
            "header_template": "count={meta.record_count}",
            "record_template": "{index}:{label}:{address}",
            "field_map": {"address": "source.raw"},
            "constants": {"label": "point"},
        }
        result = render_custom_format(
            [{"source": {"raw": 40001}}, {"source": {"raw": 40002}}],
            config,
        )
        self.assertEqual("count=2\n0:point:40001\n1:point:40002", result)

    def test_csv_escaping_and_spreadsheet_formula_protection(self) -> None:
        config = {
            "record_template": "{name},{description}",
            "escape_mode": "csv",
            "delimiter": ",",
        }
        result = render_custom_format(
            [{"name": "=HYPERLINK(\"bad\")", "description": "a,b"}],
            config,
        )
        self.assertEqual('"\'=HYPERLINK(""bad"")","a,b"', result)

    def test_json_mode_escapes_each_value(self) -> None:
        result = render_custom_format(
            [{"name": "A\nB", "value": 3}],
            {"record_template": "{name}:{value}", "escape_mode": "json"},
        )
        self.assertEqual('"A\\nB":3', result)

    def test_private_or_executable_looking_placeholders_are_rejected(self) -> None:
        for template in ("{__class__}", "{name.__class__}", "{name!r}", "{name:20}", "{fn()}"):
            with self.subTest(template=template):
                findings = validate_custom_format({"record_template": template})
                self.assertTrue(any(item["code"] == "INVALID_PLACEHOLDER" for item in findings))
                with self.assertRaises(CustomFormatError):
                    render_custom_format([{"name": "x"}], {"record_template": template})

    def test_missing_policy_is_explicit(self) -> None:
        with self.assertRaises(CustomFormatError):
            render_custom_format([{}], {"record_template": "{name}"})
        self.assertEqual(
            "",
            render_custom_format([{}], {"record_template": "{name}", "missing": "empty"}),
        )

    def test_unknown_config_keys_are_rejected(self) -> None:
        findings = validate_custom_format({"record_template": "{name}", "python": "import os"})
        self.assertEqual("UNKNOWN_CONFIG_KEY", findings[0]["code"])

    def test_generated_config_artifact_round_trips_into_validate_and_render(self) -> None:
        bare_config = {
            "name": "synthetic",
            "record_template": "{name},{protocol_offset}",
            "escape_mode": "csv",
        }
        generated = artifact_envelope(
            bare_config,
            schema_version="modbus-custom-format-config/v1",
            inputs={"example": b"Name,Protocol Offset\nTank Level,0\n"},
            assumptions=[],
            findings=[],
            holds=[],
        )
        round_tripped = json.loads(json.dumps(generated))

        self.assertEqual([], validate_custom_format(round_tripped))
        self.assertEqual(
            "Tank Level,0",
            render_custom_format(
                [{"name": "Tank Level", "protocol_offset": 0}],
                round_tripped,
            ),
        )
        round_tripped["unexpected_envelope_field"] = True
        findings = validate_custom_format(round_tripped)
        self.assertIn("UNKNOWN_CONFIG_KEY", {finding["code"] for finding in findings})

    def test_unhashable_and_non_scalar_config_values_return_findings(self) -> None:
        cases = (
            ({"record_template": "{name}", "line_ending": []}, "INVALID_LINE_ENDING"),
            ({"record_template": "{name}", "escape_mode": {}}, "INVALID_ESCAPE_MODE"),
            ({"record_template": "{name}", "missing": []}, "INVALID_MISSING_POLICY"),
            ({"record_template": "{name}", "constants": {"bad": bytearray(b"x")}}, "INVALID_CONSTANT_VALUE"),
            ({"record_template": "{name}", "constants": {"bad": float("nan")}}, "INVALID_CONSTANT_VALUE"),
        )
        for config, code in cases:
            with self.subTest(code=code):
                findings = validate_custom_format(config)
                self.assertIn(code, {finding["code"] for finding in findings})

        findings = validate_custom_format(
            {"record_template": "{name}"}, available_fields=[["name"]]
        )
        self.assertIn("INVALID_AVAILABLE_FIELD", {finding["code"] for finding in findings})


if __name__ == "__main__":
    unittest.main()
