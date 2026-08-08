---
name: normalize-map
description: Normalize Modbus candidates into explicit offsets, areas, units, datatypes, widths, access, and byte-order states.
---

# Normalize Map

Convert only fields supported by source evidence or explicit confirmation.

## Process

1. Run `python3 <skill-dir>/scripts/run.py --input <candidate.json> --output <normalized.json>`.
2. Review every assumption and hold.
3. Ask for one blocking engineering choice at a time.
4. Rerun after each confirmed choice.

Completion requires every source field to be normalized, held, or rejected with evidence.

## Handoff

- Normalization completes: suggest `$check-map`.
- The user requests a known map-wide address conversion: suggest `$remap-addresses`.

Keep the raw source address separate from protocol offset. Preserve unknown datatype, access, and byte order as unresolved.
