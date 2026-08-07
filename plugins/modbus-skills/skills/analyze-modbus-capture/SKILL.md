---
name: analyze-modbus-capture
description: Analyze bounded live or recorded Modbus samples for communication errors, response timing, missing or duplicate samples, stale and flat values, range and rate changes, counter resets or wraps, discrete transitions, and byte-order stability. Use for troubleshooting read-only captures.
---

# Analyze Modbus Capture

Analyze supplied samples. Do not expand the task into network discovery or control.

## Workflow

1. Require a bounded `capture/v1`, CSV, or JSON input.
2. Read `references/analysis-options.md` before choosing thresholds.
3. Run `python3 <skill-dir>/scripts/run.py --input <capture> --options <options.json> --output <analysis.json>`.
4. Separate communication findings from signal findings.
5. Include the sample window, thresholds, and omitted checks in the report.
6. Route raw-word ambiguity to `evaluate-modbus-byte-order`.
7. Report limitations when timestamps or expected ranges are missing.

Do not treat correlation as causation. Do not recommend a Modbus write as an automatic fix.
