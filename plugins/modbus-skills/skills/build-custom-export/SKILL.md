---
name: build-custom-export
description: Build a deterministic declarative text or CSV export from a documented example and canonical Modbus map.
license: Apache-2.0
---

# Build Custom Export

Infer a data template, then render it without executing supplied code.

Follow `../../references/interaction-contract.md`.

## Process

1. Require a documented example and target field definitions.
2. Identify header, row, delimiter, quoting, and footer rules.
3. Run `python3 <skill-dir>/scripts/run.py --example <path> --map <map.json> --output <directory>`.
4. Render immediately when inference is unambiguous.
5. Validate escaping, newlines, spreadsheet formulas, and deterministic ordering;
   ask once only when multiple target shapes remain plausible.

## Output files

- `rendered-output.txt` - Open or import this generated data file.
- `format-config.json` - Keep this small recipe so the same format can be generated again.
- `evidence.json` - Normally ignore this. It records which example and map produced the output.

Completion requires a validated declarative configuration and deterministic output.

## Handoff

- The input map is not reviewed: suggest `review-map`.
- The target is Node-RED, Modpoll, or ModScan: suggest its dedicated builder.

Use this skill for one simple text or CSV shape. Use a dedicated adapter for graphs, opaque binaries, or interdependent collections.
