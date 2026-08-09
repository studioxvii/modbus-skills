---
name: check-byte-order
description: Evaluate every supported byte and word layout from one immutable raw Modbus sample without choosing a winner.
---

# Check Byte Order

Decode one sample into a complete candidate table.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `references/sample-identity.md` and `references/layouts.md`.
2. Require raw 16-bit words and the point, route, unit, area, and protocol-offset identity.
3. Run `python3 <skill-dir>/scripts/run.py --input <capture.json> --types uint32,int32,float32 --output <evidence.json>` with the applicable datatype family.
4. Evaluate every supported layout for the sample width.
5. Apply scaling only after raw decoding.
6. Report NaN, infinity, subnormal, range, and stability evidence.
7. Eliminate candidates contradicted by explicit engineering constraints. If exactly
   one remains, present the proof and one scoped confirmation; otherwise ask once with
   the complete shortlist and distinguishing evidence needed.

## Output files

- `evidence.json` - Open this candidate table. It shows every supported word and byte layout for the same raw sample. It records evidence only; it does not change the map.

Completion requires every candidate to share one `sample_id`; the evidence selects no winner.

## Handoff

- No raw sample exists: suggest `$capture-sample`.
- The user confirms a layout: suggest `$apply-review`, then `$plan-reads`.
