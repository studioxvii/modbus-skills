---
name: infer-custom-modbus-export-format
description: Define a safe declarative text or CSV export format from a documented example and render it deterministically from a canonical Modbus map. Use for simple custom tool formats that do not require graphs, opaque binaries, or several related collections.
---

# Infer Custom Modbus Export Format

Create a declarative template. Do not execute user-supplied code.

## Workflow

1. Require a documented example and target field definitions.
2. Identify header, row, delimiter, quoting, and footer rules.
3. Run `python3 <skill-dir>/scripts/run.py --example <path> --map <map.json> --output <directory>`.
4. Review the inferred configuration before rendering.
5. Validate escaping, newlines, spreadsheet formulas, and deterministic ordering.

Do not use this skill for Node-RED graphs, several interdependent collections, undocumented binary formats, or target behavior that needs a dedicated adapter.
