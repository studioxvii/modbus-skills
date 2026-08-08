---
name: parse-map
description: Parse structured Modbus register maps into candidate rows with source values, rejected rows, assumptions, and holds.
---

# Parse Map

Preserve source values and produce traceable candidates.

## Process

1. Identify CSV, JSON, XML, XLSX, or structured-text input.
2. Read `references/format-support.md` when the shape is unclear.
3. Run `python3 <skill-dir>/scripts/run.py --input <path> --output <path>`.
4. Inspect rejected rows, assumptions, warnings, and holds.

Completion requires a schema-valid candidate map and parse report. Engineering fields can remain unresolved.

## Handoff

- Candidate rows exist: suggest `$normalize-map`.
- The source is a PDF manual: suggest `$extract-pdf-map`.

Parsing preserves uncertainty. It does not approve address, area, unit, datatype, access, or byte order.
