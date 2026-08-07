---
name: remap-modbus-addresses
description: Preview and apply a requested map-wide conversion between protocol offsets, one-based offsets, and Modicon reference notation, with explicit areas and collision checks. Use when the source and target conventions are known. Use normalize-modbus-map first when source conventions or engineering fields remain mixed or unresolved.
---

# Remap Modbus Addresses

Preview conversions before applying them.

## Workflow

1. Require the source convention and target convention.
2. Require an explicit register area when the notation does not prove it.
3. Run `python3 <skill-dir>/scripts/run.py --input <map.json> --from <convention> --to <convention> --output <preview.json>`.
4. Review invalid offsets and collisions.
5. Apply only after the user confirms the preview.

Keep the raw source token. Permit protocol offset zero. Reject protocol offsets above 65535. Do not treat Modicon reference numbers as protocol addresses.
