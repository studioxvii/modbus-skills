---
name: parse-modbus-map
description: Parse Modbus register maps from CSV, JSON, XML, XLSX, or structured text into candidate-map artifacts with source values, rejected rows, warnings, assumptions, and holds. Use when a user supplies a register list or wants a machine-readable map without silent engineering guesses.
---

# Parse Modbus Map

Preserve source values. Produce candidates and evidence. Do not treat parsing as engineering approval.

## Workflow

1. Identify the input format.
2. Run `python3 <skill-dir>/scripts/run.py --input <path> --output <path>`.
3. Inspect `rejected_rows`, `assumptions`, and `holds`.
4. Route the candidate map to `normalize-modbus-map`.

Do not silently choose an address convention, register area, unit identifier, datatype, access mode, or byte order. Preserve unknown fields as unresolved.

Read `references/format-support.md` when the input shape is unclear.

Success requires a schema-valid candidate map and a parse report. It does not require all fields to be resolved.
