---
name: lint-modbus-map
description: Validate a normalized Modbus map for duplicate identities, overlaps, invalid ranges, datatype-width conflicts, area and function-code conflicts, unresolved fields, unsafe writes, and scoped byte-order inconsistencies. Use when explicit map fields exist and the goal is deterministic validation only. Use diagnose-modbus-map for end-to-end cleanup of a raw or messy map.
---

# Lint Modbus Map

Run deterministic checks and return structured findings.

## Workflow

1. Run `python3 <skill-dir>/scripts/run.py --input <map.json> --output <validation.json>`.
2. Group findings by `error`, `warning`, and `hold`.
3. Show the composite identity and source evidence for each finding.
4. Stop final generation when errors or holds remain.

Use route, unit identifier, area, protocol offset, and logical point identifier for identity. Do not report points in different areas or units as duplicates.

Reject write function codes and broadcast behavior. Permit only read function codes 01 through 04.
