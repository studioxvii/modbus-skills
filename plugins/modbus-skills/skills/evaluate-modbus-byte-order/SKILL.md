---
name: evaluate-modbus-byte-order
description: Evaluate raw Modbus register words as explicit byte and word layouts, signed and unsigned integers, and IEEE floating-point values from one immutable sample. Use for ABCD, BADC, CDAB, DCBA, endian, word-swap, implausible-value, or unknown encoding questions.
---

# Evaluate Modbus Byte Order

Calculate candidates from the same raw sample. Do not automatically select a winner.

## Workflow

1. Accept raw 16-bit words or a `capture/v1` artifact.
2. Run `python3 <skill-dir>/scripts/run.py --input <capture.json> --types uint32,int32,float32 --output <evidence.json>`.
3. Compare `ABCD`, `BADC`, `CDAB`, and `DCBA` for two-word values.
4. Apply scaling only after raw decoding.
5. Report NaN, infinity, subnormal, range, and stability evidence.
6. Ask the user to confirm the layout.
7. Record confirmation in the canonical map and regenerate final target artifacts.

Keep one `sample_id` for every derived candidate. Read `references/layouts.md` for layout definitions and 64-bit behavior.
