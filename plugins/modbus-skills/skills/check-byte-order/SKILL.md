---
name: check-byte-order
description: Evaluate every supported byte and word layout from one immutable raw Modbus sample without choosing a winner.
---

# Check Byte Order

Decode one sample into a complete candidate table.

## Process

1. Read `references/sample-identity.md` and `references/layouts.md`.
2. Require raw 16-bit words and the point, route, unit, area, and protocol-offset identity.
3. Run `python3 <skill-dir>/scripts/run.py --input <capture.json> --types uint32,int32,float32 --output <evidence.json>` with the applicable datatype family.
4. Evaluate every supported layout for the sample width.
5. Apply scaling only after raw decoding.
6. Report NaN, infinity, subnormal, range, and stability evidence.
7. Ask the user to confirm one layout with engineering evidence.

Completion requires every candidate to share one `sample_id`; the evidence selects no winner.

## Handoff

- No raw sample exists: suggest `$capture-sample`.
- The user confirms a layout: suggest `$apply-review`, then `$plan-reads`.
