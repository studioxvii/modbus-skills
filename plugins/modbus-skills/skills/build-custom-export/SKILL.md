---
name: build-custom-export
description: Build a deterministic declarative text or CSV export from a documented example and canonical Modbus map.
---

# Build Custom Export

Infer a data template, then render it without executing supplied code.

## Process

1. Require a documented example and target field definitions.
2. Identify header, row, delimiter, quoting, and footer rules.
3. Run `python3 <skill-dir>/scripts/run.py --example <path> --map <map.json> --output <directory>`.
4. Review the inferred configuration before rendering.
5. Validate escaping, newlines, spreadsheet formulas, and deterministic ordering.

Completion requires a reviewed declarative configuration and deterministic output.

## Handoff

- The input map is not reviewed: suggest `$review-map`.
- The target is Node-RED, Modpoll, or ModScan: suggest its dedicated builder.

Use this skill for one simple text or CSV shape. Use a dedicated adapter for graphs, opaque binaries, or interdependent collections.
