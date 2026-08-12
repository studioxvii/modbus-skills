---
name: check-byte-order
description: Evaluate every supported byte and word layout from one immutable raw Modbus sample without choosing a winner. Use when byte order, word order, or multi-register decoding is unknown and raw words or a capture already exist.
license: Apache-2.0
---

# Check Byte Order

Decode one sample into a complete candidate table.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `references/sample-identity.md` and `references/layouts.md`.
2. Require raw 16-bit words and the point, route, unit, area, and protocol-offset identity.
3. Match the requested datatype width to the sample before decoding. Reject a 32-bit request when only one register was captured.
4. Run `python3 <skill-dir>/scripts/run.py --input <capture.json> --types uint32,int32,float32 --output <evidence.json>` with the applicable datatype family.
5. Evaluate every supported layout for the sample width. For one-register integers,
   state that byte and word order are not applicable and report protocol order `AB`
   without presenting a misleading `BA` interpretation.
6. Apply scaling only after raw decoding.
7. Report NaN, infinity, subnormal, range, and stability evidence.
8. Eliminate candidates contradicted by explicit engineering constraints. If exactly
   one remains, present the proof and one scoped confirmation; otherwise ask once with
   the complete shortlist and distinguishing evidence needed.

## Output files

- `evidence.json` - Open this candidate table. It says whether word order applies, then shows only layouts supported by the sample width. It records evidence only; it does not change the map.

Completion requires every candidate to share one `sample_id`; the evidence selects no winner.

## Handoff

- No raw sample exists: suggest `capture-sample`.
- The user confirms a layout: suggest `apply-review`, then `plan-reads`.
