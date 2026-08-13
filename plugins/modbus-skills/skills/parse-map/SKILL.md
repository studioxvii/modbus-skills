---
name: parse-map
description: Parse structured Modbus register maps into candidate rows with source values, rejected rows, assumptions, and holds. Use when the source is CSV, JSON, XML, XLSX, or structured text and needs traceable candidate rows.
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

## Stop

- Stop if the source is a PDF manual; that is `extract-pdf-map` or `compile-user-map`.
- Stop for writes, broadcasts, discovery, or unbounded polling.
- Do not approve address, area, unit, datatype, access, or byte order.

## Handoff

- The user wants an organized user map, JSON, CSV, or tool outputs from this OEM source: suggest `compile-user-map`.
- Candidate rows exist and the user asked only for this stage: suggest `normalize-map`.
- The source is a PDF manual: suggest `extract-pdf-map`.

Parsing preserves uncertainty. It does not approve address, area, unit, datatype, access, or byte order.
