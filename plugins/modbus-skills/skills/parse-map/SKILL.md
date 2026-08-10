---
name: parse-map
description: Parse structured Modbus register maps into candidate rows with source values, rejected rows, assumptions, and holds.
license: Apache-2.0
---

# Parse Map

Preserve source values and produce traceable candidates.

Follow `../../references/interaction-contract.md`.

## Process

1. Identify CSV, JSON, XML, XLSX, or structured-text input.
2. Read `references/format-support.md` when the shape is unclear.
3. Run `python3 <skill-dir>/scripts/run.py --input <path> --output <path>`.
4. Validate the whole parse, then summarize rejected rows, assumptions, warnings, and
   holds by cause instead of presenting rows one at a time.

## Output files

- The requested output JSON - Use this candidate map as the input to normalization. It preserves source values and lists rejected rows and parse warnings. It is not yet an approved map.

Completion requires a schema-valid candidate map and parse report. Engineering fields can remain unresolved.

## Handoff

- Candidate rows exist: suggest `$normalize-map`.
- The source is a PDF manual: suggest `$extract-pdf-map`.

Parsing preserves uncertainty. It does not approve address, area, unit, datatype, access, or byte order.
