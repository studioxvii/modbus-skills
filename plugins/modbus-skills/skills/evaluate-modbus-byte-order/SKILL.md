---
name: evaluate-modbus-byte-order
description: Evaluate raw Modbus register words as explicit byte and word layouts, signed and unsigned integers, and IEEE floating-point values from one immutable sample. Use for ABCD, BADC, CDAB, DCBA, endian, word-swap, implausible-value, or unknown encoding questions.
---

# Evaluate Modbus Byte Order

Calculate candidates from the same raw sample. Do not automatically select a winner.

## Workflow

1. Accept raw 16-bit words or a `capture/v1` artifact.
2. Require the sampled point identity: `point_id`, `route_id`, `unit_id`, `area`, and `protocol_offset`.
3. Stop if any identity field is missing or ambiguous. Do not apply evidence to a different point.
4. Run `python3 <skill-dir>/scripts/run.py --input <capture.json> --types uint32,int32,float32 --output <evidence.json>`.
5. Compare `ABCD`, `BADC`, `CDAB`, and `DCBA` for two-word values.
6. Apply scaling only after raw decoding.
7. Report NaN, infinity, subnormal, range, and stability evidence.
8. Ask the user to confirm the layout.
9. Record the decision with `apply-modbus-review-decisions`, rebuild the read plan, and then regenerate final target artifacts.

Keep one `sample_id` for every derived candidate. The evaluator reports evidence only. It never auto-selects a layout. Read `references/sample-identity.md` before applying evidence. Read `references/layouts.md` for layout definitions and 64-bit behavior.
