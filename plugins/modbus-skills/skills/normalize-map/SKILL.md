---
name: normalize-map
description: Normalize Modbus candidates into explicit offsets, areas, units, datatypes, widths, access, and byte-order states.
---

# Normalize Map

Convert only fields supported by source evidence or explicit confirmation.

Follow `../../references/interaction-contract.md`.

## Process

1. Run `python3 <skill-dir>/scripts/run.py --input <candidate.json> --output <normalized.json>`.
2. Validate all deterministic conversions automatically.
3. Group remaining assumptions and holds by shared root cause and affected scope.
4. Ask once for all independent blocking engineering choices, then rerun once.

## Output files

- `normalized.json` - Use this canonical map for validation and later steps. It keeps source values, resolved engineering fields, warnings, and grouped unresolved holds together.

Completion requires every source field to be normalized, held, or rejected with
evidence; clean deterministic normalization needs no human approval.

## Handoff

- Normalization completes: suggest `$check-map`.
- The user requests a known map-wide address conversion: suggest `$remap-addresses`.

Keep the raw source address separate from protocol offset. Preserve unknown datatype, access, and byte order as unresolved.
